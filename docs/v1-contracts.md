# HoloCore v1 compatibility contracts

These contracts define the compatibility boundary being stabilized for HoloCore v1. They apply to stable v1 releases; experimental internals that are not listed here remain implementation details.

## Command and integration contract

- Plain user-facing CLI commands use `memory-*` and `code-map-*` names.
- Existing `animus-*` and `atlas-*` command names remain callable compatibility aliases throughout v1.
- Generated Claude commands, Codex skills, and MCP prompts advertise only current plain names.
- MCP tools remain JSON-RPC tools in the `holocore_*` namespace. Legacy Atlas and Animus tool names remain callable aliases but are not newly advertised.
- New Python code imports `MemoryStore`, `MemoryCollector`, and `MemoryRetriever` from `memories.py`, `memory_collection.py`, and `memory_retrieval.py`. The former `animus*` modules are import-only v1 compatibility shims.
- Machine-readable CLI output remains valid JSON when `--json` is selected. Additive fields are allowed; removing or changing an existing field requires a major-version migration note.

## Persistent-data contract

- HoloCore Home and project data are never deleted by update or uninstall.
- New project configuration writes the `memories` field. The former `animus` field and `animus.db` filename remain readable throughout v1 so existing Homes do not require a risky database rename.
- A database with a newer unsupported schema is rejected without mutation.
- Every supported Memories schema upgrade runs through numbered migrations in one transaction.
- Before a persistent database upgrade, HoloCore creates a SQLite backup and verifies source integrity.
- After migration, HoloCore verifies integrity and records the applied migration versions.
- Interrupted setup is recoverable from `<Home>/Runtime/setup-operation.json`; completed idempotent steps are not repeated during resume.

## Release and installer contract

- One immutable tagged release supplies the CLI executable, Setup executable, installer, and manifest.
- The manifest binds the release tag, product version, artifact names, and SHA-256 hashes.
- Missing, malformed, mismatched, or unverifiable release metadata stops installation before replacement.
- Tagged builds must match the package version and execute the built artifacts before publication.
- Native uninstall removes application files and shell integration while preserving HoloCore Home and projects.

## Supported upgrade window

The exact stable upgrade window will be frozen before the final v1.0 tag. Current migration tests cover the historical Memories schema chain. Final release acceptance must additionally cover representative complete Homes from the supported prior releases.
