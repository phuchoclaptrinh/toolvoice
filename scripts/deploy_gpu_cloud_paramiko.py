from __future__ import annotations

import os
import posixpath
import shutil
import stat
import sys
import tempfile
import time
import zipfile
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_NAME = "toolvoice-gpu-deploy.zip"
HOST = os.getenv("GPU_SSH_HOST", "n2.ckey.vn")
PORT = int(os.getenv("GPU_SSH_PORT", "2713"))
USER = os.getenv("GPU_SSH_USER", "root")
PASSWORD = os.getenv("GPU_SSH_PASSWORD")
REMOTE_DIR = os.getenv("GPU_REMOTE_DIR", "/root/Toolvoice")

EXCLUDED_DIRS = {
    ".git",
    "node_modules",
    ".next",
    ".vinext",
    "dist",
    ".wrangler",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}
EXCLUDED_PATH_PARTS = {
    ("backend", ".venv"),
    ("backend", "data"),
}
EXCLUDED_FILES = {ARCHIVE_NAME}


def should_include(path: Path) -> bool:
    relative = path.relative_to(ROOT)
    parts = relative.parts
    if any(part in EXCLUDED_DIRS for part in parts):
        return False
    if any(tuple(parts[: len(excluded)]) == excluded for excluded in EXCLUDED_PATH_PARTS):
        return False
    return path.name not in EXCLUDED_FILES


def make_archive() -> Path:
    archive = ROOT / ARCHIVE_NAME
    if archive.exists():
        archive.unlink()
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in ROOT.rglob("*"):
            if path.is_file() and should_include(path):
                zf.write(path, path.relative_to(ROOT).as_posix())
    return archive


def connect() -> paramiko.SSHClient:
    if not PASSWORD:
        raise SystemExit("GPU_SSH_PASSWORD is required")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(
        HOST,
        port=PORT,
        username=USER,
        password=PASSWORD,
        timeout=20,
        look_for_keys=False,
        allow_agent=False,
    )
    return client


def run(client: paramiko.SSHClient, command: str, timeout: int | None = None) -> None:
    print(f"$ {command}", flush=True)
    stdin, stdout, stderr = client.exec_command(command, get_pty=True, timeout=timeout)
    stdin.close()
    while not stdout.channel.exit_status_ready():
        if stdout.channel.recv_ready():
            write_output(stdout.channel.recv(8192).decode(errors="replace"))
        if stdout.channel.recv_stderr_ready():
            write_output(stderr.channel.recv_stderr(8192).decode(errors="replace"))
        time.sleep(0.2)
    while stdout.channel.recv_ready():
        write_output(stdout.channel.recv(8192).decode(errors="replace"))
    while stderr.channel.recv_stderr_ready():
        write_output(stderr.channel.recv_stderr(8192).decode(errors="replace"))
    code = stdout.channel.recv_exit_status()
    if code != 0:
        raise SystemExit(f"Remote command failed with exit code {code}: {command}")


def write_output(text: str) -> None:
    sys.stdout.buffer.write(text.encode("utf-8", errors="replace"))
    sys.stdout.buffer.flush()


def main() -> None:
    archive = make_archive()
    client = connect()
    try:
        sftp = client.open_sftp()
        remote_archive = "/root/toolvoice-gpu-deploy.zip"
        print(f"Uploading {archive} -> {remote_archive}", flush=True)
        sftp.put(str(archive), remote_archive)
        sftp.close()

        run(client, "apt-get update && DEBIAN_FRONTEND=noninteractive apt-get install -y unzip")
        run(client, f"rm -rf {REMOTE_DIR} && mkdir -p {REMOTE_DIR}")
        run(client, f"cd /root && unzip -q {remote_archive} -d {REMOTE_DIR}")
        run(client, f"chmod +x {posixpath.join(REMOTE_DIR, 'scripts/gpu-cloud-setup.sh')}")
        run(client, f"bash {posixpath.join(REMOTE_DIR, 'scripts/gpu-cloud-setup.sh')}", timeout=None)
    finally:
        client.close()
        archive.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
