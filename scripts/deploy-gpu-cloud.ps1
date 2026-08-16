param(
  [string]$HostName = "n2.ckey.vn",
  [int]$SshPort = 2713,
  [string]$RemoteDir = "/root/Toolvoice"
)

$ErrorActionPreference = "Stop"

$archive = Join-Path $PWD "toolvoice-gpu-deploy.zip"
if (Test-Path $archive) {
  Remove-Item -LiteralPath $archive -Force
}

$staging = Join-Path $env:TEMP ("toolvoice-gpu-deploy-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $staging | Out-Null
try {
  Copy-Item -Path (Join-Path $PWD "*") -Destination $staging -Recurse -Force
  $remove = @(
    ".git",
    "node_modules",
    ".next",
    ".vinext",
    "dist",
    ".wrangler",
    "toolvoice-gpu-deploy.zip",
    "backend/.venv",
    "backend/data"
  )

  foreach ($item in $remove) {
    $path = Join-Path $staging $item
    if (Test-Path $path) {
      Remove-Item -LiteralPath $path -Recurse -Force
    }
  }

  Compress-Archive -Path (Join-Path $staging "*") -DestinationPath $archive -Force
}
finally {
  if (Test-Path $staging) {
    Remove-Item -LiteralPath $staging -Recurse -Force
  }
}

ssh -p $SshPort "root@$HostName" "rm -rf $RemoteDir && mkdir -p $RemoteDir"
scp -P $SshPort $archive "root@${HostName}:/root/toolvoice-gpu-deploy.zip"
ssh -p $SshPort "root@$HostName" "cd /root && unzip -q toolvoice-gpu-deploy.zip -d Toolvoice && chmod +x Toolvoice/scripts/gpu-cloud-setup.sh && bash Toolvoice/scripts/gpu-cloud-setup.sh"

Write-Host ""
Write-Host "GPU backend should be available at: http://${HostName}:2714"
Write-Host "Local frontend is configured through .env.local"
