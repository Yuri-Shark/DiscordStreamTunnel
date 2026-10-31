param(
    [ValidateSet('Backup','Restore')][string]$Action = 'Backup',
    [string]$Directory = (Join-Path $env:LOCALAPPDATA 'DiscordStreamTunnelPublisher'),
    [string]$OutputDirectory,
    [string]$BackupFile,
    [string]$NodeLicense,
    [string]$StatusFile,
    [switch]$LibraryOnly
)
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
[Windows.Forms.Application]::EnableVisualStyles()
$cryptoScript = Join-Path $PSScriptRoot 'publisher_backup.cjs'
$bundledNode = Join-Path $PSScriptRoot 'node.exe'
$nodePath = if (Test-Path -LiteralPath $bundledNode) { $bundledNode } else { (Get-Command node.exe -ErrorAction Stop).Source }
$trustPath = Join-Path $PSScriptRoot 'trust.json'
if (!(Test-Path -LiteralPath $trustPath)) { $trustPath = Join-Path (Split-Path $PSScriptRoot -Parent) 'source/trust.json' }
$trust = Get-Content -LiteralPath $trustPath -Raw | ConvertFrom-Json
$keyFile = Join-Path $Directory 'release-signing-key.dpapi'
if (!$OutputDirectory) { $OutputDirectory = Join-Path ([Environment]::GetFolderPath('DesktopDirectory')) ('DiscordStreamTunnel-Backup-Chave-' + [DateTime]::Now.ToString('yyyyMMdd-HHmmss')) }
if (!$BackupFile) { $BackupFile = Join-Path $PSScriptRoot 'release-signing-key.dtskey' }

function Set-BackupStatus([string]$State, [string]$Path = '') {
    if ($StatusFile) {
        @{state=$State;path=$Path} | ConvertTo-Json | Set-Content -LiteralPath $StatusFile -Encoding UTF8
    }
}
function Invoke-KeyCrypto($Request, [string]$Script = $cryptoScript) {
    $info = New-Object Diagnostics.ProcessStartInfo
    $info.FileName = $nodePath
    $info.Arguments = '"' + $Script + '"'
    $info.UseShellExecute = $false; $info.CreateNoWindow = $true
    $info.RedirectStandardInput = $true; $info.RedirectStandardOutput = $true; $info.RedirectStandardError = $true
    $info.StandardOutputEncoding = [Text.Encoding]::UTF8
    $process = New-Object Diagnostics.Process
    $process.StartInfo = $info
    try {
        [void]$process.Start()
        $process.StandardInput.Write(($Request | ConvertTo-Json -Depth 12 -Compress))
        $process.StandardInput.Close()
        $result = $process.StandardOutput.ReadToEnd()
        $null = $process.StandardError.ReadToEnd()
        $process.WaitForExit()
        if ($process.ExitCode -ne 0) { throw 'BackupCryptoFailed' }
        return ($result | ConvertFrom-Json)
    } finally { $process.Dispose(); $result = $null }
}
function Protect-PublisherDirectory {
    [void][IO.Directory]::CreateDirectory($Directory)
    $sid = [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    & icacls.exe $Directory /inheritance:r /grant:r "*${sid}:(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'CannotProtectPublisherDirectory' }
}
function New-PortableBackup([string]$Password) {
    if (!(Test-Path -LiteralPath $keyFile)) { throw 'PublisherKeyMissing' }
    if (Test-Path -LiteralPath $OutputDirectory) { throw 'BackupDestinationExists' }
    $license = $NodeLicense
    if (!$license) { $license = Join-Path (Split-Path $nodePath -Parent) 'LICENSE' }
    if (!(Test-Path -LiteralPath $license)) { $license = Join-Path $PSScriptRoot 'LICENSE-Node.txt' }
    if (!(Test-Path -LiteralPath $license)) { throw 'NodeLicenseMissing' }
    $finalDirectory = [IO.Path]::GetFullPath($OutputDirectory)
    $stagingDirectory = $finalDirectory + '.tmp-' + [Guid]::NewGuid().ToString('N')
    $OutputDirectory = $stagingDirectory
    $secret = (Get-Content -LiteralPath $keyFile -Raw).Trim() | ConvertTo-SecureString
    $memory = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
    try {
        $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($memory)
        $result = Invoke-KeyCrypto @{action='encrypt';privateKey=$plain;password=$Password;expectedPublicKey=$trust.publicKey}
        [void][IO.Directory]::CreateDirectory($OutputDirectory)
        $encryptedFile = Join-Path $OutputDirectory 'release-signing-key.dtskey'
        [IO.File]::WriteAllText($encryptedFile, ($result.envelope | ConvertTo-Json -Depth 12), (New-Object Text.UTF8Encoding($false)))
        Copy-Item -LiteralPath $cryptoScript -Destination (Join-Path $OutputDirectory 'publisher_backup.cjs')
        Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'publisher_backup.ps1') -Destination (Join-Path $OutputDirectory 'publisher_backup.ps1')
        Copy-Item -LiteralPath $trustPath -Destination (Join-Path $OutputDirectory 'trust.json')
        Copy-Item -LiteralPath $nodePath -Destination (Join-Path $OutputDirectory 'node.exe')
        Copy-Item -LiteralPath $license -Destination (Join-Path $OutputDirectory 'LICENSE-Node.txt')
        $launcher = '@echo off' + "`r`n" + 'powershell.exe -NoProfile -STA -ExecutionPolicy Bypass -File "%~dp0publisher_backup.ps1" -Action Restore -BackupFile "%~dp0release-signing-key.dtskey"' + "`r`n"
        [IO.File]::WriteAllText((Join-Path $OutputDirectory 'RESTAURAR.cmd'), $launcher, [Text.Encoding]::ASCII)
        $readme = @'
BACKUP DA CHAVE DE PUBLICACAO - DiscordStreamTunnel

Guarde a PASTA INTEIRA em dois locais independentes (por exemplo, pendrive
e armazenamento de sua confianca). Guarde a senha separada, de preferencia
em seu gerenciador de senhas. A senha nao fica nesta pasta e nao pode ser
recuperada se voce a esquecer. Nao publique o backup no GitHub ou na release.

PARA RESTAURAR EM OUTRO WINDOWS:
1. Copie a pasta inteira e execute RESTAURAR.cmd.
2. Digite a senha escolhida no momento da criacao.
3. A chave sera recriada com protecao DPAPI do NOVO perfil Windows em:
   %LOCALAPPDATA%\DiscordStreamTunnelPublisher\release-signing-key.dpapi
   Uma chave ja existente nunca sera substituida.
4. No repositorio do projeto, execute para repor o secret do GitHub:
   powershell -File scripts/publisher_key.ps1 -Action Upload
   E necessario ter o GitHub CLI autenticado com acesso ao repositorio.

Node.js esta incluido com sua licenca; o restauro nao baixa programas.
Formato: AES-256-GCM, chave de 256 bits derivada com scrypt
(N=131072, r=8, p=1), salt aleatorio de 32 bytes e nonce de 12 bytes.
O arquivo .dtskey contem a chave PRIVADA somente na forma criptografada.
trust.json contem a chave PUBLICA correspondente. Os metadados tambem
sao autenticados; senha incorreta ou arquivo alterado impede o restauro.
O backup independe do DPAPI/perfil do computador em que foi criado.

Verifique SHA256SUMS.txt para conferir copias. Os hashes detectam alteracoes
acidentais e nao substituem a autenticacao criptografica nem uma senha forte.
'@
        [IO.File]::WriteAllText((Join-Path $OutputDirectory 'LEIA-ME.txt'), $readme, [Text.Encoding]::UTF8)
        $saved = Get-Content -LiteralPath $encryptedFile -Raw | ConvertFrom-Json
        $verified = Invoke-KeyCrypto @{action='verify';envelope=$saved;password=$Password;expectedPublicKey=$trust.publicKey} (Join-Path $OutputDirectory 'publisher_backup.cjs')
        if (!$verified.verified -or $verified.fingerprint -ne $result.fingerprint) { throw 'BackupVerificationFailed' }
        $hashes = Get-ChildItem -LiteralPath $OutputDirectory -File | Sort-Object Name | ForEach-Object { (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant() + '  ' + $_.Name }
        [IO.File]::WriteAllLines((Join-Path $OutputDirectory 'SHA256SUMS.txt'), [string[]]$hashes, [Text.Encoding]::ASCII)
        [IO.Directory]::Move($stagingDirectory,$finalDirectory)
        return $finalDirectory
    } finally {
        [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($memory)
        $plain = $null; $secret.Dispose(); $Password = $null
        if (Test-Path -LiteralPath $stagingDirectory) {
            $stagingItem = Get-Item -LiteralPath $stagingDirectory
            if ([IO.Path]::GetFullPath($stagingItem.FullName) -ceq $stagingDirectory -and
                (Split-Path $stagingDirectory -Parent) -ceq (Split-Path $finalDirectory -Parent) -and
                ($stagingItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0) {
                Remove-Item -LiteralPath $stagingDirectory -Recurse -Force
            }
        }
    }
}
function Restore-PortableBackup([string]$Password) {
    if (Test-Path -LiteralPath $keyFile) { throw 'PublisherKeyAlreadyExists' }
    if ((Get-Item -LiteralPath $BackupFile).Length -gt 65536) { throw 'InvalidBackupSize' }
    $saved = Get-Content -LiteralPath $BackupFile -Raw | ConvertFrom-Json
    $recovered = Invoke-KeyCrypto @{action='decrypt';envelope=$saved;password=$Password;expectedPublicKey=$trust.publicKey}
    try {
        Protect-PublisherDirectory
        $secure = ConvertTo-SecureString $recovered.privateKey -AsPlainText -Force
        try {
            $encrypted = ConvertFrom-SecureString $secure
            # CreateNew prevents replacement even if another process created it.
            $stream = [IO.File]::Open($keyFile, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
            try { $bytes = [Text.Encoding]::ASCII.GetBytes($encrypted); $stream.Write($bytes,0,$bytes.Length) } finally { $stream.Dispose() }
            [IO.File]::WriteAllText((Join-Path $Directory 'trust.json'), ($trust | ConvertTo-Json), [Text.Encoding]::UTF8)
        } finally { if ($secure) { $secure.Dispose() } }
        return $keyFile
    } finally { $recovered.privateKey = $null; $Password = $null }
}

if ($LibraryOnly) { return }
if (!('PublisherBackupWindow' -as [type])) {
    Add-Type @'
using System;
using System.Runtime.InteropServices;
public static class PublisherBackupWindow {
    [DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr handle, int state);
}
'@
}
$form = New-Object Windows.Forms.Form
$form.Text = if ($Action -eq 'Backup') { 'Backup portatil da chave - DiscordStreamTunnel' } else { 'Restaurar chave - DiscordStreamTunnel' }
$form.ClientSize = New-Object Drawing.Size(650,335)
$form.StartPosition = 'CenterScreen'; $form.FormBorderStyle = 'FixedDialog'; $form.MaximizeBox = $false; $form.MinimizeBox = $false; $form.TopMost = $true
$form.Font = New-Object Drawing.Font('Segoe UI',10)
$intro = New-Object Windows.Forms.Label
$intro.SetBounds(20,15,610,58)
$intro.Text = if ($Action -eq 'Backup') { 'Escolha uma senha de pelo menos 16 caracteres e guarde-a separadamente. Ela sera usada para recuperar a chave em outro computador. Nao envie a senha pelo chat.' } else { 'Digite a senha do backup. A chave sera protegida para o perfil atual deste Windows. Uma chave existente nao sera substituida.' }
$form.Controls.Add($intro)
$location = New-Object Windows.Forms.Label
$location.SetBounds(20,77,610,48)
$location.Text = if ($Action -eq 'Backup') { 'Destino: ' + $OutputDirectory } else { 'Arquivo: ' + $BackupFile }
$form.Controls.Add($location)
$label1 = New-Object Windows.Forms.Label; $label1.SetBounds(20,134,180,25); $label1.Text = 'Senha:'; $form.Controls.Add($label1)
$password1 = New-Object Windows.Forms.TextBox; $password1.SetBounds(205,130,420,30); $password1.UseSystemPasswordChar = $true; $form.Controls.Add($password1)
$label2 = New-Object Windows.Forms.Label; $label2.SetBounds(20,175,180,25); $label2.Text = 'Confirmar senha:'; $form.Controls.Add($label2)
$password2 = New-Object Windows.Forms.TextBox; $password2.SetBounds(205,170,420,30); $password2.UseSystemPasswordChar = $true; $form.Controls.Add($password2)
if ($Action -eq 'Restore') { $label2.Visible = $false; $password2.Visible = $false }
$status = New-Object Windows.Forms.Label; $status.SetBounds(20,213,610,45); $status.ForeColor = [Drawing.Color]::DarkRed; $form.Controls.Add($status)
$submit = New-Object Windows.Forms.Button; $submit.SetBounds(355,275,160,38); $submit.Text = if ($Action -eq 'Backup') { 'Criar backup' } else { 'Restaurar' }; $form.Controls.Add($submit)
$cancel = New-Object Windows.Forms.Button; $cancel.SetBounds(525,275,100,38); $cancel.Text = 'Cancelar'; $cancel.DialogResult = 'Cancel'; $form.Controls.Add($cancel)
$form.AcceptButton = $submit; $form.CancelButton = $cancel
$form.Add_Shown({
    # A hidden PowerShell console must not hide the interactive password form.
    [void][PublisherBackupWindow]::ShowWindow($form.Handle,5)
    $form.Activate(); [void]$password1.Focus()
})
$submit.Add_Click({
    if ([Globalization.StringInfo]::ParseCombiningCharacters($password1.Text).Length -lt 16) { $status.Text = 'Use uma senha de pelo menos 16 caracteres.'; return }
    if ($Action -eq 'Backup' -and $password1.Text -cne $password2.Text) { $status.Text = 'As senhas nao coincidem.'; return }
    $submit.Enabled = $false; $cancel.Enabled = $false; $status.ForeColor = [Drawing.Color]::DarkGreen; $status.Text = 'Processando e verificando...'; $status.Refresh()
    try {
        $destination = if ($Action -eq 'Backup') { New-PortableBackup $password1.Text } else { Restore-PortableBackup $password1.Text }
        $password1.Clear(); $password2.Clear()
        Set-BackupStatus 'complete' $destination
        $message = if ($Action -eq 'Backup') { "Backup criado e recuperacao verificada.`r`n`r`nGuarde a pasta inteira em outro local e a senha separadamente.`r`n`r`n" + $destination } else { 'Chave restaurada e protegida para este perfil Windows.' }
        [void][Windows.Forms.MessageBox]::Show($form,$message,'Concluido',[Windows.Forms.MessageBoxButtons]::OK,[Windows.Forms.MessageBoxIcon]::Information)
        if ($Action -eq 'Backup') { Start-Process explorer.exe -ArgumentList ('"' + $destination + '"') }
        $form.DialogResult = 'OK'; $form.Close()
    } catch {
        $status.ForeColor = [Drawing.Color]::DarkRed
        $status.Text = switch ($_.Exception.Message) {
            'PublisherKeyAlreadyExists' { 'Ja existe uma chave neste perfil. Nenhuma chave foi substituida.' }
            'BackupDestinationExists' { 'A pasta de destino ja existe. Abra a ferramenta novamente para criar outra.' }
            'PublisherKeyMissing' { 'A chave protegida deste perfil Windows nao foi encontrada.' }
            default { 'Nao foi possivel concluir. Confira a senha e se o arquivo de backup esta completo.' }
        }
        Set-BackupStatus 'retry-needed'
        $submit.Enabled = $true; $cancel.Enabled = $true
    }
})
Set-BackupStatus 'waiting-for-password'
$result = $form.ShowDialog()
if ($result -ne [Windows.Forms.DialogResult]::OK) { Set-BackupStatus 'cancelled' }
$password1.Clear(); $password2.Clear(); $form.Dispose()
