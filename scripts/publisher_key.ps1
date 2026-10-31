param(
    [ValidateSet('Create','Upload','Sign')][string]$Action = 'Sign',
    [string]$Directory = (Join-Path $env:LOCALAPPDATA 'DiscordStreamTunnelPublisher'),
    [string]$CompatibilityManifest,
    [string]$PreviousTrust
)
$ErrorActionPreference = 'Stop'
$repositoryRoot = Split-Path $PSScriptRoot -Parent
$keyFile = Join-Path $Directory 'release-signing-key.dpapi'
if ($Action -eq 'Create') {
    if (Test-Path -LiteralPath $keyFile) { throw 'Publisher key already exists; refusing to replace it.' }
    New-Item -ItemType Directory -Path $Directory -Force | Out-Null
    $sid = [System.Security.Principal.WindowsIdentity]::GetCurrent().User.Value
    & icacls.exe $Directory /inheritance:r /grant:r "*${sid}:(OI)(CI)F" '*S-1-5-18:(OI)(CI)F' | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Cannot protect publisher directory.' }
    $generated = & node -e "const c=require('crypto'),k=c.generateKeyPairSync('ed25519');process.stdout.write(JSON.stringify({privateKey:k.privateKey.export({type:'pkcs8',format:'pem'}),publicKey:k.publicKey.export({type:'spki',format:'pem'})}));"
    if ($LASTEXITCODE -ne 0) { throw 'Cannot generate publisher key.' }
    $pair = $generated | ConvertFrom-Json
    $secret = ConvertTo-SecureString $pair.privateKey -AsPlainText -Force
    ConvertFrom-SecureString $secret | Set-Content -LiteralPath $keyFile -Encoding ASCII
    @{repository='Yuri-Shark/DiscordStreamTunnel';publicKey=$pair.publicKey} | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $repositoryRoot 'source/trust.json') -Encoding ASCII
    Write-Output 'Publisher key created with user-scoped Windows DPAPI and a private directory ACL.'
    return
}
if (!(Test-Path -LiteralPath $keyFile)) { throw 'Encrypted publisher key not found.' }
$secret = (Get-Content -LiteralPath $keyFile -Raw).Trim() | ConvertTo-SecureString
$memory = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secret)
try {
    $plain = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($memory)
    if ($Action -eq 'Upload') {
        $plain | & gh secret set DTS_RELEASE_SIGNING_KEY --repo Yuri-Shark/DiscordStreamTunnel
        if ($LASTEXITCODE -ne 0) { throw 'GitHub secret upload failed.' }
        Write-Output 'Publisher key saved in the encrypted GitHub Actions secret DTS_RELEASE_SIGNING_KEY.'
    } else {
        if (!$CompatibilityManifest) { throw 'Provide -CompatibilityManifest.' }
        $signArguments = @((Join-Path $PSScriptRoot 'sign_release.cjs'),'--key-stdin','--compat-manifest',$CompatibilityManifest)
        if ($PreviousTrust) { $signArguments += @('--previous-trust',$PreviousTrust) }
        Push-Location $repositoryRoot
        try {
            $plain | & node @signArguments
            if ($LASTEXITCODE -ne 0) { throw 'Release signing failed.' }
        } finally { Pop-Location }
    }
} finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($memory)
    $plain = $null
    $secret.Dispose()
}
