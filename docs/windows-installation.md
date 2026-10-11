# Install HoloCore on Windows

## Fastest installation

Open PowerShell and run:

```powershell
irm https://raw.githubusercontent.com/HassanMahomed/HoloCore/main/install.ps1 | iex
```

The installer:

1. Downloads the latest `holocore-windows-x64.exe` from the official GitHub release.
2. Verifies the published SHA-256 checksum when it is available.
3. Installs HoloCore under `%LOCALAPPDATA%\Programs\HoloCore` and adds that folder to the user PATH.
4. Creates the selected HoloCore Home. The default is `%USERPROFILE%\HoloCore`.
5. Detects Claude Code, Codex, Cursor, Gemini CLI, and OpenCode.
6. Asks before adding HoloCore to each detected client and before connecting a first project.
7. Offers optional, lightweight automatic updates.

Open a new terminal after installation, then run:

```powershell
holocore status
```

The installer does not require Python. The EXE, checksum, and installer script are also attached to each tagged [GitHub release](https://github.com/HassanMahomed/HoloCore/releases).

## Non-interactive installation

Download the installer first when passing options:

```powershell
$installer = Join-Path $env:TEMP "holocore-install.ps1"
Invoke-WebRequest https://raw.githubusercontent.com/HassanMahomed/HoloCore/main/install.ps1 -OutFile $installer
& $installer -Yes -Project "C:\Projects\MyApp" -Home "D:\HoloCore" -EnableAutoUpdate
```

`-Yes` accepts connection prompts for every detected client. It does not silently connect clients that were not detected.

## Connecting AI clients

Detection is read-only:

```powershell
holocore detect-clients
holocore --json detect-clients
```

To scan again and connect a project:

```powershell
cd C:\Projects\MyApp
holocore connect
```

HoloCore preserves unrelated client configuration. If an existing JSON or TOML file is invalid, HoloCore leaves it unchanged and reports the problem.

Claude Code and Codex may display a one-time trust or approval prompt after their project configuration changes. Review and approve the local HoloCore connection in that client.

## Upgrading

Run the same one-line installer again, or use:

```powershell
holocore update
```

Every upgrade rescans supported clients and reconciles registered projects. This connects clients installed after the original HoloCore setup, subject to the normal interactive approval prompt. Use `holocore update --yes` to approve every detected client non-interactively.

The installer keeps the previous executable as `holocore.exe.previous` for recovery.

## Lightweight automatic updates

```powershell
holocore auto-update --enable
holocore auto-update
holocore auto-update --check-now
holocore auto-update --disable
```

This is not a Windows Scheduled Task and does not run a resident process. When HoloCore records activity during a real AI session, it checks the GitHub release API at most once every 24 hours. If the computer is offline, the failure is quiet and HoloCore retries later. An enabled update is launched only after the session ends so the active AI client is not disrupted.

State is stored under `<HoloCore Home>\Runtime\auto-update.json`.

## Uninstalling the command

Run:

```powershell
holocore uninstall
```

HoloCore project data and HoloCore Home are intentionally preserved. Remove `%LOCALAPPDATA%\Programs\HoloCore` and its user PATH entry only if you also want to remove the standalone Windows files.
