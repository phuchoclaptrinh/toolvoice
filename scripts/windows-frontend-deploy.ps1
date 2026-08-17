$ErrorActionPreference = "Stop"

$Root = "D:\Toolvoice"
$Repo = Join-Path $Root "repo"
$Tools = Join-Path $Root "_tools"
$NodeRoot = Join-Path $Tools "Node22"
$FrontendPort = $env:FRONTEND_PORT
if (-not $FrontendPort) { $FrontendPort = "3000" }

$BackendPort = $env:BACKEND_PORT
if (-not $BackendPort) { $BackendPort = "7681" }

$PublicHost = $env:PUBLIC_HOST
if (-not $PublicHost) { $PublicHost = "100.78.120.72" }

$BackendApi = $env:NEXT_PUBLIC_CHATTERBOX_API
if (-not $BackendApi) { $BackendApi = "http://${PublicHost}:${BackendPort}" }

if (-not (Test-Path $Repo)) {
  throw "Khong thay repo tai $Repo. Hay clone code vao $Repo truoc."
}

New-Item -ItemType Directory -Force -Path $Tools | Out-Null

function Get-NodeBin {
  $node = Get-ChildItem -Path $NodeRoot -Recurse -Filter node.exe -ErrorAction SilentlyContinue |
    Select-Object -First 1
  if ($node) { return $node.DirectoryName }
  return $null
}

$NodeBin = Get-NodeBin
if (-not $NodeBin) {
  Write-Host "Dang tai Node.js 22 LTS..."
  $Index = Invoke-RestMethod "https://nodejs.org/dist/index.json"
  $Version = ($Index | Where-Object { $_.version -like "v22.*" } | Select-Object -First 1).version
  if (-not $Version) { throw "Khong tim thay Node.js v22 trong index cua nodejs.org." }

  $ZipUrl = "https://nodejs.org/dist/$Version/node-$Version-win-x64.zip"
  $ZipPath = Join-Path $Tools "node-$Version-win-x64.zip"
  Invoke-WebRequest -Uri $ZipUrl -OutFile $ZipPath
  Remove-Item -Recurse -Force $NodeRoot -ErrorAction SilentlyContinue
  Expand-Archive -Path $ZipPath -DestinationPath $NodeRoot -Force
  $NodeBin = Get-NodeBin
}

if (-not $NodeBin) { throw "Khong tim thay node.exe sau khi cai." }

$env:Path = "$NodeBin;$env:Path"
Write-Host "Node:" (& "$NodeBin\node.exe" -v)
Write-Host "NPM:" (& "$NodeBin\npm.cmd" -v)

Set-Location $Repo
"NEXT_PUBLIC_CHATTERBOX_API=$BackendApi" | Set-Content -Encoding ASCII ".env.local"

Write-Host "Dang cai frontend dependencies..."
& "$NodeBin\npm.cmd" install

Write-Host "Dang build frontend production..."
$env:NEXT_PUBLIC_CHATTERBOX_API = $BackendApi
& "$NodeBin\npm.cmd" run build

$FrontendTaskScript = Join-Path $Root "start_frontend_task.ps1"
@"
`$ErrorActionPreference = "Stop"
Set-Location "$Repo"
`$env:Path = "$NodeBin;`$env:Path"
`$env:NEXT_PUBLIC_CHATTERBOX_API = "$BackendApi"
`$env:PORT = "$FrontendPort"
& "$NodeBin\npm.cmd" run start *> "$Repo\frontend.task.log"
"@ | Set-Content -Encoding ASCII $FrontendTaskScript

$PythonExe = Join-Path $Repo "backend\.venv\Scripts\python.exe"
if (Test-Path $PythonExe) {
  $CorsOrigins = @(
    "http://localhost:$FrontendPort",
    "http://127.0.0.1:$FrontendPort",
    "http://${PublicHost}:$FrontendPort"
  ) -join ","

  $BackendTaskScript = Join-Path $Root "start_backend_task.ps1"
  @"
`$ErrorActionPreference = "Stop"
Set-Location "$Repo"
`$env:CHATTERBOX_CORS_ORIGINS = "$CorsOrigins"
`$env:TTS_MAX_WORKERS = "1"
`$env:HF_HUB_DISABLE_SYMLINKS_WARNING = "1"
Start-Transcript -Path "$Repo\backend\gpu-backend.task.log" -Append
& "$PythonExe" -m uvicorn backend.main:app --host 0.0.0.0 --port $BackendPort
Stop-Transcript
"@ | Set-Content -Encoding ASCII $BackendTaskScript

  schtasks /Create /TN ToolvoiceBackend /SC ONSTART /TR "powershell -NoProfile -ExecutionPolicy Bypass -File `"$BackendTaskScript`"" /RL HIGHEST /F | Out-Null
  schtasks /End /TN ToolvoiceBackend 2>$null | Out-Null
  Start-Sleep -Seconds 2
  schtasks /Run /TN ToolvoiceBackend | Out-Null
}
else {
  Write-Warning "Khong thay backend venv tai $PythonExe, bo qua cap nhat backend task."
}

schtasks /Create /TN ToolvoiceFrontend /SC ONSTART /TR "powershell -NoProfile -ExecutionPolicy Bypass -File `"$FrontendTaskScript`"" /RL HIGHEST /F | Out-Null
schtasks /End /TN ToolvoiceFrontend 2>$null | Out-Null
Start-Sleep -Seconds 2
schtasks /Run /TN ToolvoiceFrontend | Out-Null

New-NetFirewallRule -DisplayName "Toolvoice Frontend $FrontendPort" -Direction Inbound -Action Allow -Protocol TCP -LocalPort $FrontendPort -ErrorAction SilentlyContinue | Out-Null
New-NetFirewallRule -DisplayName "Toolvoice Backend $BackendPort" -Direction Inbound -Action Allow -Protocol TCP -LocalPort $BackendPort -ErrorAction SilentlyContinue | Out-Null

Start-Sleep -Seconds 6
Write-Host "Frontend:"
curl.exe -s -S --max-time 20 "http://127.0.0.1:$FrontendPort" | Select-Object -First 1
Write-Host ""
Write-Host "Backend:"
curl.exe -s -S --max-time 20 "http://127.0.0.1:$BackendPort/health"
Write-Host ""
Write-Host "Xong. Mo frontend: http://${PublicHost}:$FrontendPort"
