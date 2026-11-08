# HoloCore roadmap

| Version | Focus | Status |
|---|---|---|
| v0.16 | Better semantic Code Map and community knowledge generation | Implemented |
| v0.17 | Complete memory UI and research/architecture workflows | Implemented |
| v0.18 | Recoverable setup, migrations, and native Windows lifecycle | Implemented |
| v0.19 | Plain-language migration and release supply-chain hardening | Implemented |
| v1.0 | Stable contracts, verified artifacts, and complete acceptance coverage | Released |
| v1.0.1 | Native Windows uninstall experience | Released |
| v1.1 | Local Control Centre, operational visibility, and durable storage/recovery foundations | In progress |
| v1.2 | Obsidian integration, memory review, and task context packs | Planned |
| v1.3 | Notion selected-page sync, agent handoffs, and cross-project sharing | Planned |

## v1.0 and v1.0.1 release status

The v1.0 release-candidate verification is complete and v1.0.0 is stable and released. The candidate-only promotion instructions below are historical; they are not outstanding release gates. v1.0.1 is also released and adds the native Windows uninstall experience described in [v1.0.1](v1.0.1.md).

## v1.1 — Control Centre and reliability foundation

The active v1.1 work brings setup, health, performance, backup, and recovery into a local desktop Control Centre, backed by a shared local Python service boundary and reliable storage behavior. No network API is implied. The detailed scope and acceptance criteria are in [v1.1](v1.1.md).

Planned outcomes:

- One Control Centre view for configured AI connections, capture health, processing status, performance and quality measures, and recovery actions.
- Connection health that distinguishes configured from connected and reports the last successful capture, with a user-invoked Test action.
- Incremental processing, caching, and clear busy/pause behavior across supported capture and indexing work.
- A performance and quality dashboard for latency, token use, freshness, and relevance, with metric definitions and source timestamps.
- Backup and restore controls, including scheduled backups, restore preview, and portable exports.
- SQLite robustness for raw transcript separation, bounded writes, checkpointing, lock recovery, integrity checks, consistent backup/restore, and scale tests.

## v1.2 — Obsidian-first knowledge workflows

Planned work starts with an Obsidian plugin and a reviewable memory workflow. Users will be able to inspect proposed knowledge and resolve duplicates, conflicts, and stale items with provenance before promotion. Task context packs will gather bounded, relevant context for bug investigation, continuing work, and explaining a code area.

Incremental processing and caching improvements continue where v1.1 measurements show remaining cost or freshness problems.

## v1.3 — Notion and collaborative handoffs

Planned work adds an explicitly selected-page Notion connector and synchronization, plus agent handoffs that preserve decisions, affected files, tests, blockers, and next steps. Cross-project sharing will remain scoped and reviewable, with provenance and explicit conflict handling. Further efficiency work will follow measured bottlenecks.

## Roadmap status and scope

“In progress” identifies the active release line, not a claim that every listed feature is implemented or accepted. Features described as planned are targets for that phase and become available only when implemented, documented, and verified against their acceptance criteria. Version assignments may be refined as implementation and scale evidence become available.
