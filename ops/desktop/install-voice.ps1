param([string]$ProjectPath = (Split-Path -Parent (Split-Path -Parent $PSScriptRoot)))
$ErrorActionPreference = 'Stop'
$ProjectPath = (Resolve-Path -LiteralPath $ProjectPath).Path
$voiceHome = Join-Path $env:LOCALAPPDATA 'Atlas/Voice'
$modelName = 'vosk-model-small-ru-0.22'
$modelPath = Join-Path $voiceHome "Models/$modelName"
$archive = Join-Path $voiceHome "$modelName.zip"
$ttsDir = Join-Path $voiceHome 'TTS'
$ttsName = 'ru_RU-dmitri-medium'
$ttsModel = Join-Path $ttsDir "$ttsName.onnx"
$ttsJson = "$ttsModel.json"
$python = Join-Path $ProjectPath '.voice-venv/Scripts/python.exe'
$pythonw = Join-Path $ProjectPath '.voice-venv/Scripts/pythonw.exe'
$startupName = 'AtlasVoice'

if (-not (Test-Path -LiteralPath $python)) { throw "Python окружение не найдено: $python" }
$webviewCandidates = @(
    (Join-Path ${env:ProgramFiles(x86)} 'Microsoft\EdgeWebView\Application\*\msedgewebview2.exe'),
    (Join-Path $env:LOCALAPPDATA 'Microsoft\EdgeWebView\Application\*\msedgewebview2.exe')
)
if (-not ($webviewCandidates | Where-Object { Test-Path -Path $_ })) {
    Write-Warning 'WebView2 Runtime не найден. Голосовой трей и фоновые команды установятся, но окно Atlas Desktop потребует Microsoft Edge WebView2 Runtime.'
}
New-Item -ItemType Directory -Force -Path (Join-Path $voiceHome 'Models') | Out-Null
if (-not (Test-Path -LiteralPath $modelPath)) {
    $download = 'https://alphacephei.com/vosk/models/vosk-model-small-ru-0.22.zip'
    Invoke-WebRequest -Uri $download -OutFile $archive
    Expand-Archive -LiteralPath $archive -DestinationPath (Join-Path $voiceHome 'Models') -Force
    Remove-Item -LiteralPath $archive
}

New-Item -ItemType Directory -Force -Path $ttsDir | Out-Null
if ((Test-Path -LiteralPath $ttsModel) -and (Get-FileHash -LiteralPath $ttsModel -Algorithm SHA256).Hash.ToLowerInvariant() -ne 'f073356ebc4bd0f80c5af58df2953a5988bd5bdab1eb38635ce960b071fbefcb') {
    Remove-Item -LiteralPath $ttsModel -Force
}
if (-not (Test-Path -LiteralPath $ttsModel)) {
    $revision = 'e9ef9dd'
    $base = "https://huggingface.co/rhasspy/piper-voices/resolve/$revision/ru/ru_RU/dmitri/medium"
    Invoke-WebRequest -Uri "$base/$ttsName.onnx" -OutFile $ttsModel
    $hash = (Get-FileHash -LiteralPath $ttsModel -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($hash -ne 'f073356ebc4bd0f80c5af58df2953a5988bd5bdab1eb38635ce960b071fbefcb') {
        Remove-Item -LiteralPath $ttsModel -Force
        throw 'SHA-256 мужской модели Piper не совпал; файл удалён.'
    }
}
if (-not (Test-Path -LiteralPath $ttsJson)) {
    $revision = 'e9ef9dd'
    $base = "https://huggingface.co/rhasspy/piper-voices/resolve/$revision/ru/ru_RU/dmitri/medium"
    Invoke-WebRequest -Uri "$base/$ttsName.onnx.json" -OutFile $ttsJson
}

$configPath = Join-Path $voiceHome 'voice.json'
if (-not (Test-Path -LiteralPath $configPath)) {
    $template = Join-Path $ProjectPath 'ops/desktop/voice.example.json'
    $config = Get-Content -LiteralPath $template -Raw | ConvertFrom-Json
    $config.model_path = $modelPath.Replace('\', '/')
    $config | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $configPath -Encoding utf8
}
$config = Get-Content -LiteralPath $configPath -Raw | ConvertFrom-Json
$changed = $false
if (-not $config.model_path) { $config.model_path = $modelPath.Replace('\', '/'); $changed = $true }
if (-not $config.companion_config) { $config | Add-Member -NotePropertyName companion_config -NotePropertyValue '%LOCALAPPDATA%/Atlas/Companion/companion.json'; $changed = $true }
if ($null -eq $config.speak_responses) { $config | Add-Member -NotePropertyName speak_responses -NotePropertyValue $true; $changed = $true }
if ($null -eq $config.voice_interrupt_enabled) { $config | Add-Member -NotePropertyName voice_interrupt_enabled -NotePropertyValue $false; $changed = $true }
if (-not $config.default_mode) { $config | Add-Member -NotePropertyName default_mode -NotePropertyValue 'chat'; $changed = $true }
if ($null -eq $config.device_index) { $config | Add-Member -NotePropertyName device_index -NotePropertyValue -1; $changed = $true }
elseif ($config.device_index -is [string] -and $config.device_index -match '^-?\d+$') { $config.device_index = [int]$config.device_index; $changed = $true }
if (-not $config.activation_mode) { $config | Add-Member -NotePropertyName activation_mode -NotePropertyValue 'wake_word'; $changed = $true }
if ($null -eq $config.follow_up_seconds) { $config | Add-Member -NotePropertyName follow_up_seconds -NotePropertyValue 0; $changed = $true }
if (-not $config.ptt_key) { $config | Add-Member -NotePropertyName ptt_key -NotePropertyValue 'F8'; $changed = $true }
if ($null -eq $config.min_confidence) { $config | Add-Member -NotePropertyName min_confidence -NotePropertyValue 0.55; $changed = $true }
if ($null -eq $config.vad_enabled) { $config | Add-Member -NotePropertyName vad_enabled -NotePropertyValue $true; $changed = $true }
if ($null -eq $config.silence_timeout_ms) { $config | Add-Member -NotePropertyName silence_timeout_ms -NotePropertyValue 650; $changed = $true }
if ($null -eq $config.confirm_local_actions) { $config | Add-Member -NotePropertyName confirm_local_actions -NotePropertyValue $true; $changed = $true }
if ($null -eq $config.test_mode) { $config | Add-Member -NotePropertyName test_mode -NotePropertyValue $false; $changed = $true }
if (-not $config.tts_model_path) { $config | Add-Member -NotePropertyName tts_model_path -NotePropertyValue $ttsModel.Replace('\', '/'); $changed = $true }
if ($null -eq $config.reply_timeout_seconds) { $config | Add-Member -NotePropertyName reply_timeout_seconds -NotePropertyValue 90; $changed = $true }
if ($null -eq $config.tts_length_scale) { $config | Add-Member -NotePropertyName tts_length_scale -NotePropertyValue 0.94; $changed = $true }
if ($null -eq $config.show_command_pill) { $config | Add-Member -NotePropertyName show_command_pill -NotePropertyValue $true; $changed = $true }
if ($null -eq $config.command_pill_seconds) { $config | Add-Member -NotePropertyName command_pill_seconds -NotePropertyValue 5; $changed = $true }
if ($changed) { $config | ConvertTo-Json -Depth 12 | Set-Content -LiteralPath $configPath -Encoding utf8 }

$uv = (Get-Command uv -ErrorAction Stop).Source
Write-Host 'Устанавливаю локальные голосовые зависимости в отдельное окружение...'
& $uv pip install --python $python -r (Join-Path $ProjectPath 'backend/requirements-voice.lock')
if ($LASTEXITCODE -ne 0) { throw 'Не удалось установить голосовые зависимости.' }
& $uv pip install --python $python --no-deps -e (Join-Path $ProjectPath 'backend')
if ($LASTEXITCODE -ne 0) { throw 'Не удалось подключить пакет Atlas Companion.' }
Write-Host "Готово. Конфигурация: $configPath"
if (-not (Test-Path -LiteralPath $pythonw)) { throw "pythonw не найден: $pythonw" }
$runKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Run'
$startupCommand = '"' + $pythonw + '" -m atlas.local_voice --tray'
Set-ItemProperty -Path $runKey -Name $startupName -Value $startupCommand
$startMenu = Join-Path $env:APPDATA 'Microsoft/Windows/Start Menu/Programs'
$desktop = [Environment]::GetFolderPath('Desktop')
$shell = New-Object -ComObject WScript.Shell
New-Item -ItemType Directory -Force -Path $startMenu | Out-Null

function Set-AtlasShortcut {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Arguments,
        [Parameter(Mandatory = $true)][string]$Description
    )

    if (Test-Path -LiteralPath $Path) {
        $existingShortcut = $shell.CreateShortcut($Path)
        if ($existingShortcut.TargetPath -ne $pythonw -or $existingShortcut.Arguments -ne $Arguments) {
            throw "Ярлык уже существует и принадлежит другой программе: $Path"
        }
    }

    $shortcut = $shell.CreateShortcut($Path)
    $shortcut.TargetPath = $pythonw
    $shortcut.Arguments = $Arguments
    $shortcut.WorkingDirectory = $ProjectPath
    $shortcut.Description = $Description
    $shortcut.IconLocation = "$pythonw,0"
    $shortcut.Save()
}

$shortcutPath = Join-Path $startMenu 'Atlas Desktop.lnk'
$settingsShortcutPath = Join-Path $startMenu 'Atlas — Настройки голоса.lnk'
$voiceShortcutPath = Join-Path $startMenu 'Atlas Voice — слушает микрофон.lnk'
Set-AtlasShortcut -Path $shortcutPath -Arguments '-m atlas.local_voice --desktop-window' -Description 'Открыть Atlas в окне Windows'
Set-AtlasShortcut -Path $settingsShortcutPath -Arguments '-m atlas.local_voice --voice-settings' -Description 'Открыть настройки голосового управления Atlas'
Set-AtlasShortcut -Path $voiceShortcutPath -Arguments '-m atlas.local_voice --tray' -Description 'Запустить локальное голосовое управление Atlas'

if ($desktop) {
    Set-AtlasShortcut -Path (Join-Path $desktop 'Atlas Desktop.lnk') -Arguments '-m atlas.local_voice --desktop-window' -Description 'Открыть Atlas в окне Windows'
    Set-AtlasShortcut -Path (Join-Path $desktop 'Atlas — Настройки голоса.lnk') -Arguments '-m atlas.local_voice --voice-settings' -Description 'Открыть настройки голосового управления Atlas'
    Set-AtlasShortcut -Path (Join-Path $desktop 'Atlas Voice — слушает микрофон.lnk') -Arguments '-m atlas.local_voice --tray' -Description 'Запустить локальное голосовое управление Atlas'
}
Write-Host 'Готово: локальный мужской голос Piper установлен; Atlas будет запускаться после входа в Windows.'
Write-Host 'Скажите «Атлас» и команду. В трее можно приостановить микрофон, отключить автозапуск или выйти.'
Write-Host 'Ярлыки Atlas добавлены в меню Пуск и на рабочий стол.'
Write-Host "Для запуска в окне сейчас: `"$pythonw`" -m atlas.local_voice --desktop-window"
