# HoloCore v1 workflows

## Set up the first project

```powershell
cd C:\Projects\MyProject
holocore setup --home "C:\Brains\HoloCore Home"
```

For later projects, run `holocore setup` from the project root. HoloCore reuses the selected Home.

## Check readiness and locations

```powershell
holocore status
holocore paths
holocore projects
holocore connections
```

`status` reports project readiness, Code Map freshness, Memories health, local-model mode, and AI-client connection state.

## Search once through the bounded route

```powershell
holocore search "Why did we choose SQLite?"
```

The route is `check → Code Map → project Knowledge + Shared → Memories when history matters → exact sources`. Each selected source runs once.

## Work with the Code Map

```powershell
holocore code-map-refresh
holocore code-map-search "authentication"
holocore code-map-explain "LoginService"
holocore code-map-path "LoginService" "SessionStore"
holocore code-map-affected "SessionStore"
holocore code-map-groups
holocore code-map-view
```

The interactive view is self-contained and local. Code Map output is rebuildable evidence and is not automatically promoted into verified Knowledge.

## Work with Memories

```powershell
holocore remember "Decision: retain SQLite for local transactional storage" --sector project --source "design review"
holocore recall "SQLite decision"
holocore memory-timeline
holocore memory-groups
holocore memory-consolidate
```

Memories remain scoped to the active project and retain provenance. Forgetting a retained memory does not delete its original source file or transcript audit.

## Create and search Knowledge

```powershell
holocore archive-search "deployment policy"
holocore archive-create "wiki/deployment.md" "Verified deployment procedure."
```

The `archive-*` command names remain for v1 compatibility; the user-facing store is **Knowledge**. Use a `shared/` path only for a rule intentionally shared by projects.

## Review activity and hand work to another agent

```powershell
holocore activity runs
holocore activity affected "src/app.py"
holocore handoffs create <run-id> --title "Continue authentication work"
holocore handoffs list --status open
```

Activity stores normalized run/action/status/tool metadata and affected paths, not prompts or full tool payloads.

## Reconcile every project after an upgrade

```powershell
holocore sync-all --regenerate
```

This refreshes registered projects, upgrades project configuration, generates current `memory-*` and `code-map-*` interfaces, and removes obsolete HoloCore-generated legacy interfaces.

## Upgrade, roll back, or uninstall

```powershell
holocore update
& "$env:LOCALAPPDATA\Programs\HoloCore\install.ps1" -Rollback
holocore uninstall
```

Rollback and uninstall preserve HoloCore Home and project data. The Windows installer verifies checksums and Authenticode signatures before replacing executables.
