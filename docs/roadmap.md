# HoloCore roadmap

| Version | Focus | Status |
|---|---|---|
| v0.16 | Better semantic Code Map and community knowledge generation | Implemented |
| v0.17 | Complete memory UI and research/architecture workflows | Implemented |
| v0.18 | Recoverable setup, migrations, and native Windows lifecycle | Implemented |
| v0.19 | Plain-language migration and release supply-chain hardening | Implemented |
| v1.0 | Stable contracts, verified artifacts, and complete acceptance coverage | Released |
| v1.0.1 | Native Windows uninstall experience | Released |
| v1.1 | Local Control Centre, operational visibility, and durable storage/recovery foundations | Released |
| v1.2 | Conversation cleanup, memory review, incremental processing, and task context packs | Planned |
| v1.3 | Notion selected-page sync, agent handoffs, and cross-project sharing | Planned |
| v1.4 | Windows MSI packaging and managed-install lifecycle | Future consideration |

## v1.0 and v1.0.1 release status

The v1.0 release-candidate verification is complete and v1.0.0 is stable and released. The candidate-only promotion instructions below are historical; they are not outstanding release gates. v1.0.1 is also released and adds the native Windows uninstall experience described in [v1.0.1](v1.0.1.md).

## v1.1 — Control Centre and reliability foundation (released as v1.1.0)

v1.1.0 brings setup, project and knowledge browsing, connection visibility, backup and restore into a local desktop Control Centre, backed by a shared local Python service boundary and stronger SQLite behavior. It also ships the HoloCore Board Obsidian plugin. No network API is implied. See [v1.1.0 release notes](v1.1.0.md) and [v1.1 implementation record](v1.1.md).

Planned outcomes:

- **Shipped in v1.1.0:** Control Centre for projects, Knowledge, Memories, Code Map, AI connections, storage status, and backup/restore; connection test and capture timestamps; scheduled backups and portable exports; SQLite reliability improvements; unified Setup and Control Centre; Obsidian Board plugin.
- **Planned for v1.2:** conversation search and deletion with an impact preview; review of proposed, duplicate, conflicting, or stale memories; task context packs; incremental processing and pause/resume; measured performance and retrieval-quality dashboard; further scale and recovery coverage.

## v1.2 — Memory and context workflows

Planned work builds on the shipped Obsidian Board with a reviewable memory workflow. Users will be able to inspect proposed knowledge and resolve duplicates, conflicts, and stale items with provenance before promotion. Conversation search and deletion will preview related memories and Knowledge changes. Task context packs will gather bounded, relevant context for bug investigation, continuing work, and explaining a code area.

Incremental processing and a measured performance and retrieval-quality dashboard are planned here, with cached work invalidated when its inputs or processing version changes and clear busy/pause behavior.

Incremental processing and caching improvements continue where v1.1 measurements show remaining cost or freshness problems.

The [HoloCore Board](obsidian-board.md) Obsidian plugin, a read-only flow board of projects, AI sessions, memories, and changed files, shipped in v1.1.0 and is installed per vault by Setup.

## v1.3 — Notion and collaborative handoffs

Planned work adds an explicitly selected-page Notion connector and synchronization, plus agent handoffs that preserve decisions, affected files, tests, blockers, and next steps. Cross-project sharing will remain scoped and reviewable, with provenance and explicit conflict handling. Further efficiency work will follow measured bottlenecks.

## v1.4 — Windows installer packaging (future consideration)

Evaluate an MSI package with reliable upgrade, repair, rollback, and uninstall behavior. PowerShell remains the primary Windows installation path and the standalone Setup EXE remains the secondary option. MSI packaging does not itself establish a trusted publisher or remove SmartScreen warnings for unsigned direct downloads; signing and distribution remain separate decisions.

## Roadmap status and scope

“In progress” identifies the active release line, not a claim that every listed feature is implemented or accepted. Features described as planned are targets for that phase and become available only when implemented, documented, and verified against their acceptance criteria. Version assignments may be refined as implementation and scale evidence become available.
