"""
LangGraph tools for the Deep Agent.

VFS tools persist to MinIO — no in-memory state.
bash_execute materializes VFS files into a Kubernetes Job, runs the command,
then syncs any new or modified files back to MinIO.

Falls back to local subprocess when AGENT_SANDBOX_BACKEND=local (dev/test only).
"""

import os

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool
from langgraph.types import interrupt

from agent.db import todos_write
from agent import storage

_BACKEND = os.getenv("AGENT_SANDBOX_BACKEND", "kubernetes")


def _tid(config: RunnableConfig) -> str:
    return config.get("configurable", {}).get("thread_id", "default")


# ── File system ───────────────────────────────────────────────────────────────

@tool
def write_file(path: str, content: str, config: RunnableConfig) -> str:
    """Write content to a virtual file stored in MinIO."""
    storage.vfs_write(_tid(config), path, content)
    # Best-effort: embed text files for knowledge retrieval
    storage.vfs_embed(_tid(config), path, content)
    return f"Written {len(content)} bytes to '{path}'"


@tool
def read_file(path: str, config: RunnableConfig) -> str:
    """Read a virtual file from MinIO."""
    content = storage.vfs_read_text(_tid(config), path)
    if content is None:
        return f"File not found: '{path}'"
    return content


@tool
def list_files(config: RunnableConfig) -> str:
    """List all files in the virtual file system for this thread."""
    paths = storage.vfs_list(_tid(config))
    return "\n".join(paths) if paths else "No files yet."


# ── Bash execution ────────────────────────────────────────────────────────────

@tool
def bash_execute(command: str, config: RunnableConfig) -> str:
    """
    Execute a shell command in a sandboxed environment pre-populated
    with all VFS files for this thread.

    After execution, any files that were created or modified are
    automatically written back to the VFS (MinIO).

    Sandbox backend is controlled by AGENT_SANDBOX_BACKEND env var:
      kubernetes  — Kubernetes Job (default, production)
      local       — subprocess in tempdir (dev/testing only, no isolation)
    """
    tid = _tid(config)

    if _BACKEND == "local":
        return _local_execute(tid, command)

    return _kubernetes_execute(tid, command)


def _kubernetes_execute(tid: str, command: str) -> str:
    from agent.sandbox import run_command
    vfs_files: dict[str, bytes] = storage.vfs_get_all(tid)
    stdout, modified = run_command(tid, command, vfs_files)
    for path, content in modified.items():
        storage.vfs_write(tid, path, content)
    return stdout or "(no output)"


def _local_execute(tid: str, command: str) -> str:
    """Local subprocess fallback — no isolation, dev use only."""
    import subprocess
    import tempfile
    from pathlib import Path

    vfs_files = storage.vfs_get_all(tid)
    with tempfile.TemporaryDirectory(prefix=f"agent_vfs_{tid[:8]}_") as workdir:
        workdir_path = Path(workdir)
        for rel_path, content in vfs_files.items():
            dest = workdir_path / rel_path
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(content)

        try:
            result = subprocess.run(
                ["sh", "-c", command],
                cwd=workdir,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except FileNotFoundError:
            return "Error: sh not found"
        except subprocess.TimeoutExpired:
            return "Error: command timed out after 30 seconds"

        output = (result.stdout + result.stderr).replace(workdir, ".").strip()

        for file_path in workdir_path.rglob("*"):
            if not file_path.is_file():
                continue
            rel = str(file_path.relative_to(workdir_path))
            new_content = file_path.read_bytes()
            if new_content != vfs_files.get(rel):
                storage.vfs_write(tid, rel, new_content)

    return output[:4000] if output else "(no output)"


# ── Planning ──────────────────────────────────────────────────────────────────

@tool
def write_todos(todos: list[str], config: RunnableConfig) -> str:
    """Publish a task plan. Todos are visible to reviewers in real time."""
    todos_write(_tid(config), todos)
    return f"Published {len(todos)} todos"


# ── Human-in-the-loop ─────────────────────────────────────────────────────────

@tool
def request_options(question: str, options: list[str]) -> str:
    """
    Pause execution and ask the human to choose an option.
    The agent resumes automatically after the human responds.
    """
    choice = interrupt({"question": question, "options": options})
    return f"Human chose: {choice!r}"
