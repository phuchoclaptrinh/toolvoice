import os
import sys

import paramiko


host = os.getenv("GPU_SSH_HOST", "n2.ckey.vn")
port = int(os.getenv("GPU_SSH_PORT", "2753"))
password = os.getenv("GPU_SSH_PASSWORD")

if not password:
    raise SystemExit("Missing GPU_SSH_PASSWORD")

command = r"""
echo ---STOP_SETUP---
for pid in $(pgrep -f 'bash scripts/gpu-cloud-setup.sh' || true); do
  if [ "$pid" != "$$" ] && [ "$pid" != "$PPID" ]; then
    kill -TERM "$pid" 2>/dev/null || true
    echo killed_setup:$pid
  fi
done
echo ---BACKEND---
ps aux | grep 'uvicorn backend.main:app' | grep -v grep || true
echo ---HEALTH---
curl -s --max-time 8 http://127.0.0.1:7681/health || true
"""

client = paramiko.SSHClient()
client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
client.connect(
    host,
    port=port,
    username="root",
    password=password,
    timeout=20,
    look_for_keys=False,
    allow_agent=False,
)
stdin, stdout, stderr = client.exec_command(command, timeout=60)
sys.stdout.buffer.write(stdout.read())
sys.stderr.buffer.write(stderr.read())
client.close()
