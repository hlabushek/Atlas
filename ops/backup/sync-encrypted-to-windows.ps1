# Pull encrypted VPS archives to a second machine when this Windows account is online.
$ErrorActionPreference = 'Stop'
$sshKey = Join-Path $env:USERPROFILE '.ssh\selectel_key'
$destination = Join-Path $env:LOCALAPPDATA 'Atlas\Backups'
$remote = 'root@139.100.239.8'
$remoteDir = '/opt/atlas/backups/encrypted'
if (-not (Test-Path -LiteralPath $sshKey)) { throw 'SSH key is unavailable' }
New-Item -ItemType Directory -Path $destination -Force | Out-Null
& icacls $destination /inheritance:r /grant:r "$($env:USERNAME):(OI)(CI)F" 'SYSTEM:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Unable to restrict backup directory permissions' }

function RemoteHash([string]$remotePath) {
    $line = & ssh -i $sshKey -o BatchMode=yes $remote "sha256sum $remotePath"
    if ($LASTEXITCODE -ne 0 -or $line -notmatch '^([a-f0-9]{64})\s') { throw 'Remote checksum unavailable' }
    return $Matches[1]
}

$names = & ssh -i $sshKey -o BatchMode=yes $remote "find $remoteDir -maxdepth 1 -type f -name 'atlas-*.dump.gpg' -printf '%f\n'"
if ($LASTEXITCODE -ne 0 -or -not $names) { throw 'Encrypted VPS archives unavailable' }
foreach ($name in $names) {
    if ($name -notmatch '^atlas-[0-9]{8}T[0-9]{6}Z\.dump\.gpg$') { throw 'Unexpected archive name' }
    $target = Join-Path $destination $name
    $expected = RemoteHash "$remoteDir/$name"
    if (Test-Path -LiteralPath $target) {
        if ((Get-FileHash -LiteralPath $target -Algorithm SHA256).Hash.ToLowerInvariant() -eq $expected) { continue }
        throw 'Existing offsite archive checksum differs'
    }
    $temporary = "$target.partial"
    & scp -q -i $sshKey "${remote}:$remoteDir/$name" $temporary
    if ($LASTEXITCODE -ne 0) { throw 'Archive transfer failed' }
    if ((Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash.ToLowerInvariant() -ne $expected) { throw 'Archive checksum mismatch' }
    Move-Item -LiteralPath $temporary -Destination $target
}

$keyTarget = Join-Path $destination 'recovery-passphrase'
$keyHash = RemoteHash '/etc/atlas-backup/passphrase'
if (-not (Test-Path -LiteralPath $keyTarget)) {
    $temporary = "$keyTarget.partial"
    & scp -q -i $sshKey "${remote}:/etc/atlas-backup/passphrase" $temporary
    if ($LASTEXITCODE -ne 0) { throw 'Recovery key transfer failed' }
    if ((Get-FileHash -LiteralPath $temporary -Algorithm SHA256).Hash.ToLowerInvariant() -ne $keyHash) { throw 'Recovery key checksum mismatch' }
    Move-Item -LiteralPath $temporary -Destination $keyTarget
} elseif ((Get-FileHash -LiteralPath $keyTarget -Algorithm SHA256).Hash.ToLowerInvariant() -ne $keyHash) {
    throw 'Recovery key differs from VPS key'
}
& icacls $destination /inheritance:r /grant:r "$($env:USERNAME):(OI)(CI)F" 'SYSTEM:(OI)(CI)F' | Out-Null
if ($LASTEXITCODE -ne 0) { throw 'Unable to restrict copied file permissions' }
Write-Output "Verified encrypted offsite archives: $(@($names).Count)"
