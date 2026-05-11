"""
S3-compatible VFS layer — works with SeaweedFS, Ceph RGW, RustFS, or any S3 endpoint.

Files are stored as objects under the key pattern:
    {bucket}/{thread_id}/{path}

Text files are stored as UTF-8 bytes. Binary files pass through unchanged.
Embeddings of text files are stored in PostgreSQL vfs_embeddings (pgvector).

Environment variables (S3_* preferred; MINIO_* accepted for backward compatibility):
  S3_ENDPOINT    / MINIO_ENDPOINT    — host:port of the S3 gateway
  S3_ACCESS_KEY  / MINIO_ACCESS_KEY
  S3_SECRET_KEY  / MINIO_SECRET_KEY
  S3_BUCKET      / S3_BUCKET
  S3_SECURE      / MINIO_SECURE      — "true" to enable TLS
"""

from __future__ import annotations

import io
import os

from minio import Minio
from minio.error import S3Error


def _env(key: str, legacy_key: str, default: str) -> str:
    return os.getenv(key) or os.getenv(legacy_key) or default


S3_ENDPOINT        = _env("S3_ENDPOINT",        "MINIO_ENDPOINT",   "seaweedfs:8333")
# S3_PUBLIC_ENDPOINT is the address embedded in presigned URLs returned to clients.
# Must differ from S3_ENDPOINT when the internal address (e.g. seaweedfs:8333)
# is not reachable by external clients. In Docker Compose, set to localhost:8333.
S3_PUBLIC_ENDPOINT = os.getenv("S3_PUBLIC_ENDPOINT") or S3_ENDPOINT
S3_ACCESS_KEY      = _env("S3_ACCESS_KEY", "MINIO_ACCESS_KEY", "agent_access")
S3_SECRET_KEY      = _env("S3_SECRET_KEY", "MINIO_SECRET_KEY", "agent_secret")
S3_BUCKET          = _env("S3_BUCKET",     "MINIO_BUCKET",     "agent-vfs")
S3_REGION          = os.getenv("S3_REGION", "us-east-1")
_SECURE             = _env("S3_SECURE",    "MINIO_SECURE",     "false").lower() == "true"


def _client() -> Minio:
    return Minio(
        S3_ENDPOINT,
        access_key=S3_ACCESS_KEY,
        secret_key=S3_SECRET_KEY,
        secure=_SECURE,
        region=S3_REGION,
    )


def _public_client() -> Minio:
    """Client whose presigned URLs use the public-facing endpoint.

    Passing region explicitly prevents the minio SDK from making a
    GET /bucket?location network call (which would fail when S3_PUBLIC_ENDPOINT
    is not reachable from inside the container).
    """
    return Minio(
        S3_PUBLIC_ENDPOINT,
        access_key=S3_ACCESS_KEY,
        secret_key=S3_SECRET_KEY,
        secure=_SECURE,
        region=S3_REGION,
    )


def _ensure_bucket(mc: Minio) -> None:
    if not mc.bucket_exists(S3_BUCKET):
        mc.make_bucket(S3_BUCKET)


def _key(thread_id: str, path: str) -> str:
    return f"{thread_id}/{path.lstrip('/')}"


# ── Public API ────────────────────────────────────────────────────────────────

def vfs_write(thread_id: str, path: str, content: bytes | str) -> None:
    if isinstance(content, str):
        content = content.encode("utf-8")
    mc = _client()
    _ensure_bucket(mc)
    mc.put_object(
        S3_BUCKET,
        _key(thread_id, path),
        io.BytesIO(content),
        length=len(content),
        content_type="application/octet-stream",
    )


def vfs_read(thread_id: str, path: str) -> bytes | None:
    mc = _client()
    try:
        resp = mc.get_object(S3_BUCKET, _key(thread_id, path))
        return resp.read()
    except S3Error as e:
        if e.code in ("NoSuchKey", "NoSuchBucket"):
            return None
        raise
    finally:
        try:
            resp.close()  # type: ignore[union-attr]
            resp.release_conn()  # type: ignore[union-attr]
        except Exception:
            pass


def vfs_read_text(thread_id: str, path: str) -> str | None:
    data = vfs_read(thread_id, path)
    return data.decode("utf-8", errors="replace") if data is not None else None


def vfs_list(thread_id: str) -> list[str]:
    mc = _client()
    prefix = f"{thread_id}/"
    try:
        objects = mc.list_objects(S3_BUCKET, prefix=prefix, recursive=True)
        return [obj.object_name[len(prefix):] for obj in objects]
    except S3Error:
        return []


def vfs_get_all(thread_id: str) -> dict[str, bytes]:
    result: dict[str, bytes] = {}
    for path in vfs_list(thread_id):
        data = vfs_read(thread_id, path)
        if data is not None:
            result[path] = data
    return result


def vfs_get_all_text(thread_id: str) -> dict[str, str]:
    """Return only text-decodable files as strings (for bash_execute compat)."""
    out: dict[str, str] = {}
    for path, content in vfs_get_all(thread_id).items():
        try:
            out[path] = content.decode("utf-8")
        except UnicodeDecodeError:
            pass
    return out


def vfs_presigned_url(thread_id: str, path: str, expires_seconds: int = 3600) -> str:
    """Generate a presigned GET URL signed for S3_PUBLIC_ENDPOINT.

    Uses _public_client() which embeds the public host in the signature,
    so the URL works for external clients without host rewriting.
    Region is set explicitly to avoid a network bucket-location lookup.
    """
    from datetime import timedelta
    mc = _public_client()
    return mc.presigned_get_object(
        S3_BUCKET,
        _key(thread_id, path),
        expires=timedelta(seconds=expires_seconds),
    )


def vfs_delete(thread_id: str, path: str) -> None:
    mc = _client()
    try:
        mc.remove_object(S3_BUCKET, _key(thread_id, path))
    except S3Error:
        pass


# ── pgvector embeddings ───────────────────────────────────────────────────────

def vfs_embed(thread_id: str, path: str, content: str) -> None:
    """Chunk text and store embeddings in vfs_embeddings via pgvector.

    Requires: DATABASE_URL env var, pgvector extension, vfs_embeddings table.
    Uses Ollama embeddings endpoint.
    """
    try:
        import psycopg
        from agent.config import LLM_GATEWAY_URL, LLM_GATEWAY_KEY
        from agent.db import DATABASE_URL
        import httpx, json

        chunks = _chunk_text(content, size=500, overlap=50)
        embed_url = LLM_GATEWAY_URL.rstrip("/v1").rstrip("/") + "/api/embed"

        with psycopg.connect(DATABASE_URL) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS vfs_embeddings (
                    id         BIGSERIAL PRIMARY KEY,
                    thread_id  TEXT NOT NULL,
                    path       TEXT NOT NULL,
                    chunk_idx  INT  NOT NULL,
                    chunk_text TEXT NOT NULL,
                    embedding  vector(768)
                )
            """)
            conn.execute(
                "DELETE FROM vfs_embeddings WHERE thread_id = %s AND path = %s",
                (thread_id, path),
            )
            for idx, chunk in enumerate(chunks):
                resp = httpx.post(
                    embed_url,
                    json={"model": "nomic-embed-text", "input": chunk},
                    timeout=30,
                )
                resp.raise_for_status()
                embedding = resp.json()["embeddings"][0]
                conn.execute(
                    """
                    INSERT INTO vfs_embeddings (thread_id, path, chunk_idx, chunk_text, embedding)
                    VALUES (%s, %s, %s, %s, %s::vector)
                    """,
                    (thread_id, path, idx, chunk, str(embedding)),
                )
            conn.commit()
    except Exception:
        pass  # embeddings are best-effort; don't fail the write


def _chunk_text(text: str, size: int = 500, overlap: int = 50) -> list[str]:
    words = text.split()
    chunks, i = [], 0
    while i < len(words):
        chunk = " ".join(words[i:i + size])
        chunks.append(chunk)
        i += size - overlap
    return chunks
