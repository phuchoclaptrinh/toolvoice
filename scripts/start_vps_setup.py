import os
import sys

import paramiko


host = os.getenv("GPU_SSH_HOST", "n2.ckey.vn")
port = int(os.getenv("GPU_SSH_PORT", "2753"))
password = os.getenv("GPU_SSH_PASSWORD")

if not password:
    raise SystemExit("Missing GPU_SSH_PASSWORD")

command = r"""
cd /root/Toolvoice
echo ---PROC---
ps aux | grep gpu-cloud-setup | grep -v grep || true
echo ---LOG---
tail -60 setup-gpu.log 2>/dev/null || true
echo ---START_IF_NEEDED---
if ! ps aux | grep 'bash scripts/gpu-cloud-setup.sh' | grep -v grep >/dev/null; then
  nohup bash scripts/gpu-cloud-setup.sh > /root/Toolvoice/setup-gpu.log 2>&1 < /dev/null &
  echo SETUP_PID:$!
else
  echo already_running
fi
sleep 2
echo ---LOG_AFTER_START---
tail -40 setup-gpu.log 2>/dev/null || true
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
stdin, stdout, stderr = client.exec_command(command, timeout=90)
sys.stdout.buffer.write(stdout.read())
sys.stderr.buffer.write(stderr.read())
client.close()
