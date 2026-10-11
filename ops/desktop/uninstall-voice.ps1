param(
    [switch]$RemoveLocalModels,
    [switch]$Confirm
)
$ErrorActionPreference = 'Stop'
if (-not $Confirm) {
    throw 'Для удаления запустите скрипт с -Confirm. Модели удаляются только при явном -RemoveLocalModels.'
}
$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
Remove-ItemProperty -Path $runKey -Name 'AtlasVoice' -ErrorAction SilentlyContinue
$shortcutPaths = @(
    (Join-Path $env:APPDATA 'Microsoft/Windows/Start Menu/Programs/Atlas Desktop.lnk'),
    (Join-Path $env:APPDATA 'Microsoft/Windows/Start Menu/Programs/Atlas — Настройки голоса.lnk'),
    (Join-Path $env:APPDATA 'Microsoft/Windows/Start Menu/Programs/Atlas Voice — слушает микрофон.lnk')
)
$desktop = [Environment]::GetFolderPath('Desktop')
if ($desktop) {
    $shortcutPaths += (Join-Path $desktop 'Atlas Desktop.lnk')
    $shortcutPaths += (Join-Path $desktop 'Atlas — Настройки голоса.lnk')
    $shortcutPaths += (Join-Path $desktop 'Atlas Voice — слушает микрофон.lnk')
}
foreach ($shortcutPath in $shortcutPaths) {
    if (Test-Path -LiteralPath $shortcutPath) { Remove-Item -LiteralPath $shortcutPath -Force }
}
if ($RemoveLocalModels) {
    $voiceHome = Join-Path $env:LOCALAPPDATA 'Atlas/Voice'
    if (Test-Path -LiteralPath $voiceHome) { Remove-Item -LiteralPath $voiceHome -Recurse -Force }
}
Write-Host 'Автозапуск и ярлыки Atlas удалены.'
if ($RemoveLocalModels) { Write-Host 'Локальные голосовые модели и настройки удалены.' }
