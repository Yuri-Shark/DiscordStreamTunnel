$ErrorActionPreference = 'Stop'
$testRoot = Join-Path ([IO.Path]::GetTempPath()) ('DTS-Publisher-Test-' + [Guid]::NewGuid().ToString('N'))
[void][IO.Directory]::CreateDirectory($testRoot)
$publisherScript = Join-Path (Split-Path $PSScriptRoot -Parent) 'scripts/publisher_backup.ps1'
$testPublisher = Join-Path $testRoot 'original'
$testDestination = Join-Path $testRoot 'portable'
$testLicense = Join-Path $testRoot 'synthetic-license.txt'
[IO.File]::WriteAllText($testLicense,'Synthetic license fixture; temporary test only.')
try {
    . $publisherScript -LibraryOnly -Directory $testPublisher -OutputDirectory $testDestination -NodeLicense $testLicense
    $pairJson = & node -e "const c=require('crypto'),k=c.generateKeyPairSync('ed25519');process.stdout.write(JSON.stringify({privateKey:k.privateKey.export({format:'pem',type:'pkcs8'}),publicKey:k.publicKey.export({format:'pem',type:'spki'})}));"
    if ($LASTEXITCODE -ne 0) { throw 'Synthetic key generation failed' }
    $pair = $pairJson | ConvertFrom-Json; $pairJson = $null
    $trust = @{repository='Yuri-Shark/DiscordStreamTunnel';publicKey=$pair.publicKey}
    # The synthetic public key also travels with the portable package.
    $trustPath = Join-Path $testRoot 'synthetic-trust.json'
    $trust | ConvertTo-Json | Set-Content -LiteralPath $trustPath -Encoding ASCII
    Protect-PublisherDirectory
    $testSecure = ConvertTo-SecureString $pair.privateKey -AsPlainText -Force
    try { ConvertFrom-SecureString $testSecure | Set-Content -LiteralPath $keyFile -Encoding ASCII } finally { $testSecure.Dispose() }
    $password = 'Synthetic publisher test password 123! ' + [char]0xE3 + [char]::ConvertFromUtf32(0x1F510)
    $created = New-PortableBackup $password
    if ($created -cne $testDestination) { throw 'Unexpected destination' }
    foreach ($name in 'node.exe','publisher_backup.ps1','publisher_backup.cjs','trust.json','release-signing-key.dtskey','SHA256SUMS.txt','RESTAURAR.cmd','LEIA-ME.txt','LICENSE-Node.txt') {
        if (!(Test-Path -LiteralPath (Join-Path $created $name))) { throw 'Incomplete portable package' }
    }
    $Directory = Join-Path $testRoot 'restored'; $keyFile = Join-Path $Directory 'release-signing-key.dpapi'
    $BackupFile = Join-Path $created 'release-signing-key.dtskey'
    $cryptoScript = Join-Path $created 'publisher_backup.cjs'; $nodePath = Join-Path $created 'node.exe'
    $restored = Restore-PortableBackup $password
    if ($restored -cne $keyFile) { throw 'Unexpected restored destination' }
    $protected = (Get-Content -LiteralPath $keyFile -Raw).Trim() | ConvertTo-SecureString
    $memory = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($protected)
    try { if ([Runtime.InteropServices.Marshal]::PtrToStringBSTR($memory) -cne $pair.privateKey) { throw 'Restored key mismatch' } }
    finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($memory); $protected.Dispose() }
    $before = (Get-FileHash -LiteralPath $keyFile).Hash
    $refused = $false
    try { $null = Restore-PortableBackup $password } catch { if ($_.Exception.Message -eq 'PublisherKeyAlreadyExists') { $refused=$true } else { throw } }
    if (!$refused -or (Get-FileHash -LiteralPath $keyFile).Hash -ne $before) { throw 'Existing key was not protected' }
    Write-Output 'OK: Windows backup packaging, bundled Node recovery, DPAPI round trip and refusal to overwrite existing keys.'
} finally {
    $pair.privateKey=$null; $password=$null
    $resolved = [IO.Path]::GetFullPath($testRoot)
    if ((Split-Path $resolved -Parent) -ceq ([IO.Path]::GetTempPath().TrimEnd('\')) -and
        (Split-Path $resolved -Leaf) -like 'DTS-Publisher-Test-*' -and
        (Get-Item -LiteralPath $resolved).Attributes -band [IO.FileAttributes]::ReparsePoint -eq 0) {
        Remove-Item -LiteralPath $resolved -Recurse -Force
    }
}
