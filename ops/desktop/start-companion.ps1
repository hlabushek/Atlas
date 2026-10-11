param([Parameter(Mandatory=$true)][string]$ProjectPath)
$ErrorActionPreference = 'Stop'
$project = $ProjectPath
$python = Join-Path $project 'backend\.venv\Scripts\python.exe'
$config = Join-Path $env:LOCALAPPDATA 'Atlas\Companion\companion.json'
$status = Join-Path $env:LOCALAPPDATA 'Atlas\Companion\startup-status.txt'
Set-Content -LiteralPath $status -Value 'Проверяю локальные файлы Companion'
if (-not (Test-Path -LiteralPath $python)) { Set-Content -LiteralPath $status -Value 'Python для Companion не найден'; exit 2 }
if (-not (Test-Path -LiteralPath $config)) { Set-Content -LiteralPath $status -Value 'Конфигурация Companion не найдена'; exit 2 }
Set-Content -LiteralPath $status -Value 'Companion запущен'
Set-Location (Join-Path $project 'backend')
& $python -m atlas.companion $config 2>&1 | Out-Null
Set-Content -LiteralPath $status -Value "Companion остановился с кодом $LASTEXITCODE"
exit $LASTEXITCODE
