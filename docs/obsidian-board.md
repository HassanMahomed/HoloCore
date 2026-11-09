# HoloCore Board for Obsidian

**Status:** In development for v1.2. Not part of a release yet.

The HoloCore Board is an Obsidian plugin that shows HoloCore's own data as one connected flow board. It shows nothing from the vault itself.

| Column | What a card is | Where it comes from |
|---|---|---|
| Projects | A registered HoloCore project | `worlds.json` in the HoloCore Home |
| AI sessions | One main AI client session (subagents are counted on the card) | The activity ledger (`agent_runs`) recorded by HoloCore's capture hooks |
| Memories | Up to four memories per session, plus up to three recent memories per project | Memories database shards whose provenance points at that session's transcript |
| Files changed | Files the session's tool calls touched | Activity ledger actions and their affected items |

Every line on the board is a recorded relationship, never an inferred one:

- **project → session:** the session ran in that project.
- **session → memory:** the memory was distilled from that session's transcript.
- **project → memory:** a recent project memory that did not come from a session shown on the board.
- **session → file:** the session changed that file.

Click a card to highlight everything it came from and everything that came from it. Use **Open** on a file card to open the file: files inside the vault open in Obsidian, and other files open in their default app.

The filters are project, period (24 hours, 7 days, 30 days, or all time) and **Hide noise**, which hides short or mechanical memory fragments. Sessions from folders that are not registered HoloCore projects (such as an AI client's own working folders) are left out, and the footer shows how many were hidden.

## How it reads data

The plugin runs `holocore board` and draws the JSON that command prints. It never opens the Memories database itself, and the command opens the database read-only. The plugin is desktop-only because it runs a local program.

```bash
holocore board --days 7 --limit 30
```

## Install and remove

HoloCore Setup lists the vaults Obsidian has opened on this computer, from Obsidian's own vault list. Each vault has a tick box, and all of them start unticked. A ticked vault gets the plugin in `.obsidian/plugins/holocore-board` and the plugin is turned on for that vault. Obsidian's restricted mode must be off for community plugins to load.

From the command line:

```bash
holocore obsidian-plugin vaults
```

```bash
holocore obsidian-plugin install --vault "C:\path\to\vault"
```

```bash
holocore obsidian-plugin uninstall --vault "C:\path\to\vault"
```

Installing again updates the plugin files and the HoloCore program path, and keeps the board settings chosen in Obsidian. Uninstalling HoloCore removes the plugin from every vault Obsidian lists where it is installed. Notes in the vault are never changed.

After installing, restart Obsidian or reload the vault, then click the HoloCore Board icon in the left ribbon or run **HoloCore Board: Open board** from the command palette.
