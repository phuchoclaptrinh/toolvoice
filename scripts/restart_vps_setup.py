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
pkill -f '[p]ip install' || true
pkill -f '[g]pu-cloud-setup' || true
git pull --ff-only
nohup bash scripts/gpu-cloud-setup.sh > /root/Toolvoice/setup-gpu.log 2>&1 < /dev/null &
echo SETUP_PID:$!
sleep 2
tail -50 setup-gpu.log
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
