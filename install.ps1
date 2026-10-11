[CmdletBinding()]
param(
    [string]$Home = (Join-Path $env:USERPROFILE "HoloCore"),
    [string]$Project,
    [switch]$Yes,
    [switch]$UpgradeOnly,
    [switch]$EnableAutoUpdate
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$installDir = Join-Path $env:LOCALAPPDATA "Programs\HoloCore"
$exePath = Join-Path $installDir "holocore.exe"
$installerPath = Join-Path $installDir "install.ps1"
$releaseBase = "https://github.com/HassanMahomed/HoloCore/releases/latest/download"
$exeUrl = "$releaseBase/holocore-windows-x64.exe"
$checksumUrl = "$exeUrl.sha256"
$scriptUrl = "https://raw.githubusercontent.com/HassanMahomed/HoloCore/main/install.ps1"
$temporaryDir = Join-Path ([IO.Path]::GetTempPath()) ("holocore-install-" + [guid]::NewGuid().ToString("N"))
$downloadedExe = Join-Path $temporaryDir "holocore.exe"

function Read-YesNo([string]$Prompt, [bool]$DefaultYes = $true) {
    if ($Yes) { return $true }
    $suffix = if ($DefaultYes) { "[Y/n]" } else { "[y/N]" }
    $answer = Read-Host "$Prompt $suffix"
    if ([string]::IsNullOrWhiteSpace($answer)) { return $DefaultYes }
    return $answer.Trim().ToLowerInvariant() -in @("y", "yes")
}

function Add-ToUserPath([string]$Directory) {
    $current = [Environment]::GetEnvironmentVariable("Path", "User")
    $parts = @($current -split ";" | Where-Object { $_ })
    if ($parts -notcontains $Directory) {
        [Environment]::SetEnvironmentVariable("Path", (($parts + $Directory) -join ";"), "User")
    }
    if (($env:Path -split ";") -notcontains $Directory) { $env:Path = "$Directory;$env:Path" }
}

try {
    Write-Host "Installing HoloCore..." -ForegroundColor Cyan
    New-Item -ItemType Directory -Path $installDir -Force | Out-Null
    New-Item -ItemType Directory -Path $temporaryDir -Force | Out-Null
    Invoke-WebRequest -UseBasicParsing -Uri $exeUrl -OutFile $downloadedExe

    try {
        $checksumFile = Join-Path $temporaryDir "holocore.sha256"
        Invoke-WebRequest -UseBasicParsing -Uri $checksumUrl -OutFile $checksumFile
        $expected = ((Get-Content -Raw $checksumFile).Trim() -split "\s+")[0].ToLowerInvariant()
        $actual = (Get-FileHash -Algorithm SHA256 $downloadedExe).Hash.ToLowerInvariant()
        if ($expected -ne $actual) { throw "HoloCore download checksum did not match." }
    } catch {
        if ($_.Exception.Message -like "*checksum did not match*") { throw }
        Write-Warning "The release checksum was unavailable; the download came directly from the official HoloCore GitHub release."
    }

    if (Test-Path $exePath) {
        Copy-Item -LiteralPath $exePath -Destination "$exePath.previous" -Force
    }
    Move-Item -LiteralPath $downloadedExe -Destination $exePath -Force
    try { Invoke-WebRequest -UseBasicParsing -Uri $scriptUrl -OutFile $installerPath } catch { Write-Warning "Could not cache install.ps1 for automatic updates." }
    Add-ToUserPath $installDir

    & $exePath home $Home | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "HoloCore could not initialize its Home folder." }

    if ($UpgradeOnly) {
        Write-Host "Checking for newly installed AI clients..." -ForegroundColor Cyan
        & $exePath sync-all --detected --yes | Out-Host
        Write-Host "HoloCore is up to date." -ForegroundColor Green
        exit 0
    }

    $detected = & $exePath --json detect-clients | ConvertFrom-Json
    $selected = [System.Collections.Generic.List[string]]::new()
    foreach ($name in @("claude", "codex", "cursor", "gemini", "opencode")) {
        $detail = $detected.$name
        if ($null -ne $detail -and $detail.installed) {
            $evidence = ($detail.evidence -join ", ")
            if (Read-YesNo "Connect $name to HoloCore? Detected: $evidence") { $selected.Add($name) }
        }
    }

    if ([string]::IsNullOrWhiteSpace($Project) -and -not $Yes) {
        $Project = Read-Host "First project folder to connect (leave blank to do this later)"
    }
    if (-not [string]::IsNullOrWhiteSpace($Project)) {
        $arguments = [System.Collections.Generic.List[string]]::new()
        $arguments.Add("setup"); $arguments.Add($Project); $arguments.Add("--home"); $arguments.Add($Home)
        foreach ($client in $selected) { $arguments.Add("--platform"); $arguments.Add($client) }
        & $exePath @arguments | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "HoloCore was installed, but project setup did not complete." }
    } else {
        Write-Host "HoloCore is installed. Connect a project later with:" -ForegroundColor Yellow
        Write-Host "  holocore setup C:\path\to\project --home `"$Home`""
    }

    $enableUpdates = $EnableAutoUpdate -or ((-not $Yes) -and (Read-YesNo "Enable lightweight daily update checks during active HoloCore sessions?" $false))
    if ($enableUpdates) { & $exePath auto-update --enable | Out-Host }

    Write-Host "HoloCore installation completed." -ForegroundColor Green
    Write-Host "Open a new terminal, then run: holocore status"
} finally {
    if (Test-Path $temporaryDir) { Remove-Item -LiteralPath $temporaryDir -Recurse -Force -ErrorAction SilentlyContinue }
}
