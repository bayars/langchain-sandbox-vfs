"""
Kubernetes Job sandbox for bash_execute.

run_command(thread_id, command, vfs_files) -> (stdout: str, modified_files: dict[str, bytes])

Strategy:
  1. Try in-cluster service account first (KUBERNETES_SERVICE_HOST env present).
  2. Fall back to KUBECONFIG / ~/.kube/config for out-of-cluster use.

VFS files are injected into the Job via a ConfigMap mounted at /workspace.
After the Job finishes, modified or new files under /workspace are captured
by running a second `find + cat` pass inside the same pod (before cleanup).
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import uuid
from typing import Any

from kubernetes import client, config as k8s_config
from kubernetes.client.rest import ApiException

NAMESPACE = os.getenv("AGENT_SANDBOX_NAMESPACE", "agent-sandbox")
IMAGE     = os.getenv("AGENT_SANDBOX_IMAGE",     "python:3.12-slim")
TIMEOUT   = int(os.getenv("AGENT_SANDBOX_TIMEOUT", "60"))


def _load_k8s() -> None:
    """Load kube config: in-cluster first, then fall back to kubeconfig."""
    try:
        k8s_config.load_incluster_config()
    except k8s_config.ConfigException:
        k8s_config.load_kube_config()


def _file_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def run_command(
    thread_id: str,
    command: str,
    vfs_files: dict[str, bytes],
) -> tuple[str, dict[str, bytes]]:
    """
    Execute `command` inside a Kubernetes Job with VFS files pre-populated
    in /workspace. Returns (stdout+stderr, modified_files).

    modified_files contains only files that were created or changed.
    """
    _load_k8s()
    batch_v1 = client.BatchV1Api()
    core_v1  = client.CoreV1Api()

    job_name = f"agent-{thread_id[:8]}-{uuid.uuid4().hex[:8]}"
    cm_name  = f"{job_name}-vfs"

    # Build ConfigMap data (text files only; binary files are base64-encoded
    # and decoded by an init script)
    cm_data:        dict[str, str] = {}
    cm_binary_data: dict[str, str] = {}
    original_hashes: dict[str, str] = {}

    for rel_path, content in vfs_files.items():
        safe_key = rel_path.replace("/", "__")
        original_hashes[rel_path] = _file_hash(content)
        try:
            text = content.decode("utf-8")
            cm_data[safe_key] = text
        except UnicodeDecodeError:
            cm_binary_data[safe_key] = base64.b64encode(content).decode()

    # Init script: restore files from ConfigMap keys back to their original paths
    restore_lines = ["mkdir -p /workspace"]
    for rel_path in vfs_files:
        safe_key = rel_path.replace("/", "__")
        restore_lines.append(
            f'mkdir -p /workspace/$(dirname "{rel_path}") 2>/dev/null || true'
        )
        if rel_path.replace("/", "__") in cm_binary_data:
            restore_lines.append(
                f'base64 -d /cm/{safe_key} > /workspace/{rel_path}'
            )
        else:
            restore_lines.append(f'cp /cm/{safe_key} /workspace/{rel_path}')

    restore_script = " && ".join(restore_lines) if restore_lines else "true"

    # After user command, emit a JSON manifest of all files so we can diff
    collect_script = (
        "python3 -c \""
        "import os, json, base64; "
        "out={}; "
        "[out.update({os.path.relpath(os.path.join(r,f),'/workspace'): "
        "base64.b64encode(open(os.path.join(r,f),'rb').read()).decode()}) "
        "for r,_,fs in os.walk('/workspace') for f in fs]; "
        "print('__VFS_MANIFEST__' + json.dumps(out))\""
    )

    full_cmd = (
        f"set -e; {restore_script}; cd /workspace; "
        f"({command}) 2>&1; "
        f"{collect_script}"
    )

    # ConfigMap
    cm_body = client.V1ConfigMap(
        metadata=client.V1ObjectMeta(name=cm_name, namespace=NAMESPACE),
        data=cm_data or None,
        binary_data=cm_binary_data or None,
    )

    # Job spec
    job_body = client.V1Job(
        metadata=client.V1ObjectMeta(name=job_name, namespace=NAMESPACE),
        spec=client.V1JobSpec(
            active_deadline_seconds=TIMEOUT,
            ttl_seconds_after_finished=300,
            backoff_limit=0,
            template=client.V1PodTemplateSpec(
                spec=client.V1PodSpec(
                    restart_policy="Never",
                    automount_service_account_token=False,
                    containers=[client.V1Container(
                        name="runner",
                        image=IMAGE,
                        command=["sh", "-c", full_cmd],
                        working_dir="/workspace",
                        resources=client.V1ResourceRequirements(
                            limits={"cpu": "500m", "memory": "512Mi"},
                            requests={"cpu": "100m", "memory": "128Mi"},
                        ),
                        volume_mounts=[client.V1VolumeMount(
                            name="vfs", mount_path="/cm", read_only=True,
                        )],
                    )],
                    volumes=[client.V1Volume(
                        name="vfs",
                        config_map=client.V1ConfigMapVolumeSource(name=cm_name),
                    )],
                )
            ),
        ),
    )

    # Ensure namespace exists
    _ensure_namespace(core_v1, NAMESPACE)

    try:
        core_v1.create_namespaced_config_map(NAMESPACE, cm_body)
        batch_v1.create_namespaced_job(NAMESPACE, job_body)

        pod_name = _wait_for_pod(core_v1, NAMESPACE, job_name, TIMEOUT)
        raw_log  = _read_log(core_v1, NAMESPACE, pod_name)
    finally:
        # Best-effort cleanup
        try:
            batch_v1.delete_namespaced_job(
                job_name, NAMESPACE,
                body=client.V1DeleteOptions(propagation_policy="Foreground"),
            )
        except ApiException:
            pass
        try:
            core_v1.delete_namespaced_config_map(cm_name, NAMESPACE)
        except ApiException:
            pass

    # Split stdout from VFS manifest
    stdout, modified = _parse_output(raw_log, original_hashes)
    return stdout, modified


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ensure_namespace(core_v1: client.CoreV1Api, name: str) -> None:
    try:
        core_v1.read_namespace(name)
    except ApiException as e:
        if e.status == 404:
            core_v1.create_namespace(
                client.V1Namespace(metadata=client.V1ObjectMeta(name=name))
            )


def _wait_for_pod(
    core_v1: client.CoreV1Api, namespace: str, job_name: str, timeout: int
) -> str:
    deadline = time.time() + timeout
    label_selector = f"job-name={job_name}"

    while time.time() < deadline:
        pods = core_v1.list_namespaced_pod(namespace, label_selector=label_selector)
        for pod in pods.items:
            phase = pod.status.phase
            if phase in ("Succeeded", "Failed"):
                return pod.metadata.name
        time.sleep(0.5)

    raise TimeoutError(f"Sandbox job {job_name} did not complete within {timeout}s")


def _read_log(core_v1: client.CoreV1Api, namespace: str, pod_name: str) -> str:
    try:
        return core_v1.read_namespaced_pod_log(pod_name, namespace) or ""
    except ApiException as e:
        return f"(log unavailable: {e})"


def _parse_output(
    raw: str, original_hashes: dict[str, str]
) -> tuple[str, dict[str, bytes]]:
    marker = "__VFS_MANIFEST__"
    if marker in raw:
        idx = raw.index(marker)
        stdout = raw[:idx].strip()
        manifest_line = raw[idx + len(marker):]
        # Take only the first JSON object on that line
        try:
            manifest: dict[str, str] = json.loads(manifest_line.split("\n")[0])
        except json.JSONDecodeError:
            manifest = {}
    else:
        stdout = raw.strip()
        manifest = {}

    modified: dict[str, bytes] = {}
    for rel_path, b64 in manifest.items():
        try:
            content = base64.b64decode(b64)
        except Exception:
            continue
        if _file_hash(content) != original_hashes.get(rel_path):
            modified[rel_path] = content

    return stdout[:4000], modified
