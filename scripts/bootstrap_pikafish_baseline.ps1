[CmdletBinding()]
param(
    [string]$Destination
)

if (-not $Destination) {
    $Destination = Join-Path $PSScriptRoot "..\external\pikafish-official-2026-01-02"
}

$archiveName = "Pikafish.2026-01-02.7z"
$archiveUrl = "https://github.com/official-pikafish/Pikafish/releases/download/Pikafish-2026-01-02/$archiveName"
$archiveSha256 = "84257063905615919fb4ee6a70273a94843bb6ec04c45e3ac706098838bc1a49"
$executableRelativePath = "Windows\pikafish-avx512.exe"
$executableSha256 = "38e988912b62592d94dcd64573e0a115198502ab0e054bc620b4657e08d89f80"
$nnueSha256 = "c4026370d7516d9b0f668447f9ca1931241538bdc689cde6fec6a991ac4d5f77"

$destinationPath = [System.IO.Path]::GetFullPath($Destination)
New-Item -ItemType Directory -Force -Path $destinationPath | Out-Null
$archivePath = Join-Path $destinationPath $archiveName

if (-not (Test-Path -LiteralPath $archivePath)) {
    Invoke-WebRequest -Uri $archiveUrl -OutFile $archivePath
}

function Assert-Sha256([string]$Path, [string]$ExpectedHash) {
    $actualHash = (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
    if ($actualHash -ne $ExpectedHash) {
        throw "SHA-256 mismatch for ${Path}: $actualHash"
    }
}

Assert-Sha256 $archivePath $archiveSha256

$executablePath = Join-Path $destinationPath $executableRelativePath
$nnuePath = Join-Path $destinationPath "pikafish.nnue"
if (-not ((Test-Path -LiteralPath $executablePath) -and (Test-Path -LiteralPath $nnuePath))) {
    $sevenZip = @(
        "C:\\Program Files\\7-Zip\\7z.exe",
        "C:\\Program Files (x86)\\7-Zip\\7z.exe",
        "C:\\ProgramData\\chocolatey\\tools\\7z.exe"
    ) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $sevenZip) {
        throw "7-Zip is required to extract $archiveName"
    }

    & $sevenZip x $archivePath "-o$destinationPath" -y
    if ($LASTEXITCODE -ne 0) {
        throw "7-Zip extraction failed with exit code $LASTEXITCODE"
    }
}

Assert-Sha256 $executablePath $executableSha256
Assert-Sha256 $nnuePath $nnueSha256
Write-Output "Pikafish baseline is ready at $destinationPath"
