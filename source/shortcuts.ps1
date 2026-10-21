param([Parameter(Mandatory=$true)][string]$DtsRoot,[ValidateSet('install','remove')][string]$Mode='install')
$ErrorActionPreference='Stop'
$dtsExe=Join-Path $DtsRoot 'app\DiscordStreamTunnel.exe'
$dtsData=Join-Path $DtsRoot 'data'
$dtsRecord=Join-Path $dtsData 'shortcuts.json'
$dtsShell=New-Object -ComObject WScript.Shell
if ($Mode -eq 'remove') {
    if (Test-Path -LiteralPath $dtsRecord) {
        foreach ($item in (Get-Content -LiteralPath $dtsRecord -Raw | ConvertFrom-Json)) {
            if (Test-Path -LiteralPath $item.path) {
                $current=$dtsShell.CreateShortcut($item.path)
                if ($current.TargetPath -eq $dtsExe) { Copy-Item -LiteralPath $item.backup -Destination $item.path -Force }
            }
        }
    }
    exit 0
}
if (Test-Path -LiteralPath $dtsRecord) { exit 0 }
$dtsLinks=@()
$dtsFolders=@([Environment]::GetFolderPath('Desktop'),(Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs'),(Join-Path $env:APPDATA 'Microsoft\Internet Explorer\Quick Launch\User Pinned\TaskBar'))
foreach ($folder in $dtsFolders) {
    if (Test-Path -LiteralPath $folder) { $dtsLinks+=Get-ChildItem -LiteralPath $folder -Filter 'Discord.lnk' -Recurse -File }
}
$dtsBackups=Join-Path $dtsData 'shortcut-backups'
New-Item -ItemType Directory -Path $dtsBackups -Force | Out-Null
$dtsChanges=@()
foreach ($file in ($dtsLinks | Sort-Object FullName -Unique)) {
    $shortcut=$dtsShell.CreateShortcut($file.FullName)
    $name=[IO.Path]::GetFileName($shortcut.TargetPath)
    $matches=($name -eq 'Discord.exe') -or (($name -eq 'Update.exe') -and ($shortcut.Arguments -match '(?i)(?:^|\s)--processStart\s+"?Discord\.exe(?:"|\s|$)'))
    if (-not $matches) { continue }
    $backup=Join-Path $dtsBackups (([guid]::NewGuid().ToString())+'.lnk')
    Copy-Item -LiteralPath $file.FullName -Destination $backup
    $shortcut.TargetPath=$dtsExe
    $shortcut.Arguments='--root "'+$DtsRoot+'" launch'
    $shortcut.WorkingDirectory=$DtsRoot
    $shortcut.Save()
    $dtsChanges+=@{path=$file.FullName;backup=$backup}
}
ConvertTo-Json -InputObject @($dtsChanges) -Depth 4 | Set-Content -LiteralPath $dtsRecord -Encoding UTF8
