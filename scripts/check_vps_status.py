import os
import sys

import paramiko


host = os.getenv("GPU_SSH_HOST", "n2.ckey.vn")
port = int(os.getenv("GPU_SSH_PORT", "2753"))
password = os.getenv("GPU_SSH_PASSWORD")

if not password:
    raise SystemExit("Missing GPU_SSH_PASSWORD")

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

remote_command = r"""
echo ---GPU---
nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv,noheader || true
echo ---CUDA---
nvcc --version 2>/dev/null | tail -3 || true
echo ---PORTS---
ss -lntp | grep -E '(:7681|:3001|:3000)' || true
echo ---PROJECT---
ls -la /root/Toolvoice 2>/dev/null || true
echo ---HEALTH---
curl -s --max-time 8 http://127.0.0.1:7681/health || true
echo
echo ---PROC---
ps aux | grep -E 'uvicorn|vinext|node' | grep -v grep || true
echo ---DISK---
df -h /
"""

stdin, stdout, stderr = client.exec_command(remote_command, timeout=90)
output = stdout.read().decode("utf-8", "replace")
errors = stderr.read().decode("utf-8", "replace")
sys.stdout.write(output)
sys.stderr.write(errors)
client.close()
