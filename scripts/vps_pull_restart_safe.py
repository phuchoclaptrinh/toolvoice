import os
import sys

import paramiko


host = os.getenv("GPU_SSH_HOST", "n2.ckey.vn")
port = int(os.getenv("GPU_SSH_PORT", "2753"))
password = os.getenv("GPU_SSH_PASSWORD")

if not password:
    raise SystemExit("Missing GPU_SSH_PASSWORD")

command = r"""
set -e
cd /root/Toolvoice
echo ---STOP_OLD---
for pid in $(pgrep -f 'bash scripts/gpu-cloud-setup.sh' || true); do
  if [ "$pid" != "$$" ] && [ "$pid" != "$PPID" ]; then
    kill -TERM "$pid" 2>/dev/null || true
    echo killed_setup:$pid
  fi
done
for pid in $(pgrep -f 'pip install' || true); do
  if [ "$pid" != "$$" ] && [ "$pid" != "$PPID" ]; then
    kill -TERM "$pid" 2>/dev/null || true
    echo killed_pip:$pid
  fi
done
sleep 2
echo ---PULL---
git fetch origin main
git reset --hard origin/main
git rev-parse --short HEAD
echo ---SCRIPT_TORCH_LINES---
grep -nE 'torch|toolvoice-requirements-gpu' scripts/gpu-cloud-setup.sh
echo ---START---
nohup bash scripts/gpu-cloud-setup.sh > /root/Toolvoice/setup-gpu.log 2>&1 < /dev/null &
echo SETUP_PID:$!
sleep 2
tail -40 setup-gpu.log
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
stdin, stdout, stderr = client.exec_command(command, timeout=120)
sys.stdout.buffer.write(stdout.read())
sys.stderr.buffer.write(stderr.read())
client.close()
