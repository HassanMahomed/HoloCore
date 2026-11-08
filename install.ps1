[CmdletBinding()]
param(
    [Alias("Home")]
    [string]$HomePath = (Join-Path $env:USERPROFILE "HoloCore"),
    [string]$Project,
    [switch]$Yes,
    [switch]$UpgradeOnly,
    [switch]$EnableAutoUpdate,
    [switch]$NoUI,
    [string]$Version,
    [switch]$Uninstall,
    [switch]$Rollback,
    [string]$InstallDir,
    [string]$ReleaseDirectory,
    [switch]$SkipShellIntegration
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$installDir = if ([string]::IsNullOrWhiteSpace($InstallDir)) {
    Join-Path $env:LOCALAPPDATA "Programs\HoloCore"
} else {
    [IO.Path]::GetFullPath($InstallDir)
}
$exePath = Join-Path $installDir "holocore.exe"
$setupExePath = Join-Path $installDir "HoloCore Setup.exe"
$installerPath = Join-Path $installDir "install.ps1"
$temporaryDir = Join-Path ([IO.Path]::GetTempPath()) ("holocore-install-" + [guid]::NewGuid().ToString("N"))
$downloadedExe = Join-Path $temporaryDir "holocore.exe"
$downloadedSetupExe = Join-Path $temporaryDir "HoloCore Setup.exe"

function Resolve-ReleaseTag {
    if (-not [string]::IsNullOrWhiteSpace($Version)) {
        $candidate = $Version.Trim()
        if (-not $candidate.StartsWith("v")) { $candidate = "v$candidate" }
        if ($candidate -notmatch '^v\d+\.\d+\.\d+(?:(?:a|b|rc)\d+|\.dev\d+|[-+][0-9A-Za-z.-]+)?$') {
            throw "Invalid HoloCore release version: $Version"
        }
        return $candidate
    }
    $release = Invoke-RestMethod -UseBasicParsing -Uri "https://api.github.com/repos/HassanMahomed/HoloCore/releases/latest"
    if ([string]::IsNullOrWhiteSpace($release.tag_name)) { throw "The latest HoloCore release did not provide a tag." }
    return [string]$release.tag_name
}

function Test-DownloadedArtifact([string]$File, [string]$Expected, [string]$Label) {
    if ([string]::IsNullOrWhiteSpace($Expected) -or $Expected -notmatch '^[0-9a-fA-F]{64}$') {
        throw "The release manifest has no valid SHA-256 checksum for $Label."
    }
    $actual = (Get-FileHash -Algorithm SHA256 $File).Hash.ToLowerInvariant()
    if ($Expected.ToLowerInvariant() -ne $actual) { throw "$Label download checksum did not match the release manifest." }
}

function Assert-AuthenticodeSignature([string]$File, [string]$ExpectedThumbprint, [string]$Label) {
    $signature = Get-AuthenticodeSignature -LiteralPath $File
    if ($signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid -or $null -eq $signature.SignerCertificate) {
        throw "$Label does not have a valid Authenticode signature. Status: $($signature.Status)."
    }
    $actual = $signature.SignerCertificate.Thumbprint.Replace(" ", "").ToUpperInvariant()
    $expected = ([string]$ExpectedThumbprint).Replace(" ", "").ToUpperInvariant()
    if ([string]::IsNullOrWhiteSpace($expected) -or $actual -ne $expected) {
        throw "$Label signer does not match the release manifest."
    }
}

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

function Remove-FromUserPath([string]$Directory) {
    $current = [Environment]::GetEnvironmentVariable("Path", "User")
    $parts = @($current -split ";" | Where-Object { $_ -and $_.TrimEnd('\') -ine $Directory.TrimEnd('\') })
    [Environment]::SetEnvironmentVariable("Path", ($parts -join ";"), "User")
}

function Invoke-HoloCoreUninstall {
    Write-Host "Uninstalling HoloCore application files..." -ForegroundColor Cyan
    if (-not $SkipShellIntegration) {
        Remove-FromUserPath $installDir
        $shortcutPath = Join-Path ([Environment]::GetFolderPath("Programs")) "HoloCore Setup.lnk"
        if (Test-Path -LiteralPath $shortcutPath) { Remove-Item -LiteralPath $shortcutPath -Force }
    }
    if (Test-Path -LiteralPath $installDir) { Remove-Item -LiteralPath $installDir -Recurse -Force }
    Write-Host "HoloCore application files were removed. HoloCore Home and all project data were preserved." -ForegroundColor Green
}

function Invoke-HoloCoreRollback {
    $previousExe = "$exePath.previous"
    $previousSetup = "$setupExePath.previous"
    if (-not (Test-Path -LiteralPath $previousExe) -or -not (Test-Path -LiteralPath $previousSetup)) {
        throw "No complete HoloCore rollback pair is available."
    }
    $currentExe = "$exePath.rollback-current"
    $currentSetup = "$setupExePath.rollback-current"
    Move-Item -LiteralPath $exePath -Destination $currentExe -Force
    Move-Item -LiteralPath $setupExePath -Destination $currentSetup -Force
    try {
        Move-Item -LiteralPath $previousExe -Destination $exePath -Force
        Move-Item -LiteralPath $previousSetup -Destination $setupExePath -Force
        Move-Item -LiteralPath $currentExe -Destination $previousExe -Force
        Move-Item -LiteralPath $currentSetup -Destination $previousSetup -Force
    } catch {
        if (Test-Path -LiteralPath $currentExe) { Move-Item -LiteralPath $currentExe -Destination $exePath -Force }
        if (Test-Path -LiteralPath $currentSetup) { Move-Item -LiteralPath $currentSetup -Destination $setupExePath -Force }
        throw
    }
    Write-Host "HoloCore rolled back to the previous installed release. User data was preserved." -ForegroundColor Green
}

if ($Uninstall) {
    Invoke-HoloCoreUninstall
    exit 0
}

if ($Rollback) {
    Invoke-HoloCoreRollback
    exit 0
}

try {
    Write-Host "Installing HoloCore..." -ForegroundColor Cyan
    New-Item -ItemType Directory -Path $installDir -Force | Out-Null
    New-Item -ItemType Directory -Path $temporaryDir -Force | Out-Null
    $releaseTag = Resolve-ReleaseTag
    $manifestPath = Join-Path $temporaryDir "release-manifest.json"
    if ([string]::IsNullOrWhiteSpace($ReleaseDirectory)) {
        $releaseBase = "https://github.com/HassanMahomed/HoloCore/releases/download/$releaseTag"
        Invoke-WebRequest -UseBasicParsing -Uri "$releaseBase/release-manifest.json" -OutFile $manifestPath
    } else {
        $releaseBase = $null
        $resolvedReleaseDirectory = [IO.Path]::GetFullPath($ReleaseDirectory)
        Copy-Item -LiteralPath (Join-Path $resolvedReleaseDirectory "release-manifest.json") -Destination $manifestPath
    }
    $manifest = Get-Content -Raw -LiteralPath $manifestPath | ConvertFrom-Json
    if ($manifest.schema_version -ne 2 -or $manifest.tag -ne $releaseTag) {
        throw "The HoloCore release manifest does not match requested release $releaseTag."
    }
    if ($null -ne $releaseBase) {
        Invoke-WebRequest -UseBasicParsing -Uri "$releaseBase/holocore-windows-x64.exe" -OutFile $downloadedExe
        Invoke-WebRequest -UseBasicParsing -Uri "$releaseBase/holocore-setup-windows-x64.exe" -OutFile $downloadedSetupExe
    } else {
        Copy-Item -LiteralPath (Join-Path $resolvedReleaseDirectory "holocore-windows-x64.exe") -Destination $downloadedExe
        Copy-Item -LiteralPath (Join-Path $resolvedReleaseDirectory "holocore-setup-windows-x64.exe") -Destination $downloadedSetupExe
    }
    Test-DownloadedArtifact $downloadedExe $manifest.assets.'holocore-windows-x64.exe'.sha256 "HoloCore"
    Test-DownloadedArtifact $downloadedSetupExe $manifest.assets.'holocore-setup-windows-x64.exe'.sha256 "HoloCore Setup"
    Assert-AuthenticodeSignature $downloadedExe $manifest.signing.thumbprint "HoloCore"
    Assert-AuthenticodeSignature $downloadedSetupExe $manifest.signing.thumbprint "HoloCore Setup"
    $reportedVersion = ((& $downloadedExe --version) | Out-String).Trim()
    if ($LASTEXITCODE -ne 0 -or $reportedVersion -ne [string]$manifest.version) {
        throw "Downloaded HoloCore reports version '$reportedVersion', expected '$($manifest.version)'."
    }

    if (Test-Path $exePath) {
        Copy-Item -LiteralPath $exePath -Destination "$exePath.previous" -Force
    }
    if (Test-Path $setupExePath) {
        Copy-Item -LiteralPath $setupExePath -Destination "$setupExePath.previous" -Force
    }
    Move-Item -LiteralPath $downloadedExe -Destination $exePath -Force
    Move-Item -LiteralPath $downloadedSetupExe -Destination $setupExePath -Force
    if (-not $SkipShellIntegration) { try {
        $startMenu = [Environment]::GetFolderPath("Programs")
        $shortcutPath = Join-Path $startMenu "HoloCore Setup.lnk"
        $shell = New-Object -ComObject WScript.Shell
        $shortcut = $shell.CreateShortcut($shortcutPath)
        $shortcut.TargetPath = $setupExePath
        $shortcut.WorkingDirectory = $installDir
        $shortcut.Description = "Configure HoloCore folders, AI tools, features, and local models"
        $shortcut.Save()
    } catch {
        Write-Warning "HoloCore was installed, but the Start menu shortcut could not be created."
    } }
    if ($null -ne $releaseBase) {
        Invoke-WebRequest -UseBasicParsing -Uri "$releaseBase/install.ps1" -OutFile $installerPath
    } else {
        Copy-Item -LiteralPath $PSCommandPath -Destination $installerPath -Force
    }
    if (-not $SkipShellIntegration) { Add-ToUserPath $installDir }

    if (-not $Yes -and -not $NoUI -and -not $UpgradeOnly) {
        $wizardArguments = [System.Collections.Generic.List[string]]::new()
        $wizardArguments.Add("--home"); $wizardArguments.Add(('"' + $HomePath + '"'))
        if (-not [string]::IsNullOrWhiteSpace($Project)) {
            $wizardArguments.Add("--project"); $wizardArguments.Add(('"' + $Project + '"'))
        }
        Start-Process -FilePath $setupExePath -ArgumentList $wizardArguments -WorkingDirectory $installDir
        Write-Host "HoloCore is installed. The native setup application is opening." -ForegroundColor Green
        Write-Host "The wizard lets you choose folders, features, AI tools, and a local model before applying changes."
        exit 0
    }

    & $exePath home $HomePath | Out-Host
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
        $arguments.Add("setup"); $arguments.Add($Project); $arguments.Add("--home"); $arguments.Add($HomePath)
        foreach ($client in $selected) { $arguments.Add("--platform"); $arguments.Add($client) }
        & $exePath @arguments | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "HoloCore was installed, but project setup did not complete." }
    } else {
        Write-Host "HoloCore is installed. Connect a project later with:" -ForegroundColor Yellow
        Write-Host "  holocore setup C:\path\to\project --home `"$HomePath`""
    }

    $enableUpdates = $EnableAutoUpdate -or ((-not $Yes) -and (Read-YesNo "Enable lightweight daily update checks during active HoloCore sessions?" $false))
    if ($enableUpdates) { & $exePath auto-update --enable | Out-Host }

    Write-Host "HoloCore installation completed." -ForegroundColor Green
    Write-Host "Open a new terminal, then run: holocore status"
} finally {
    if (Test-Path $temporaryDir) { Remove-Item -LiteralPath $temporaryDir -Recurse -Force -ErrorAction SilentlyContinue }
}
