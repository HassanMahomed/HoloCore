# HoloCore roadmap to v1.0

| Version | Focus | Status |
|---|---|---|
| v0.16 | Better semantic Code Map and community knowledge generation | Implemented in source |
| v0.17 | Complete memory UI and research/architecture workflows | Implemented in source |
| v0.18 | Recoverable setup, migrations, and native Windows lifecycle | Implemented in source |
| v0.19 | Plain-language migration and release supply-chain hardening | Implemented in source |
| v1.0 | Stable contracts, signed artifacts, and complete acceptance coverage | Release candidate |

## v0.16 exit criteria

- Plain-language and CamelCase-aware Code Map search is deterministic.
- Dense feature areas remain distinct even when cross-area imports connect them.
- Every community exposes topics, key files, representative symbols, and structural evidence.
- Community knowledge is generated without being routed back into HoloCore as source material.
- Existing Code Map CLI and MCP contracts remain compatible.

## v0.17 direction

- Complete the Memories interface for browsing, filtering, inspecting provenance, and managing retained history.
- Add guided research and architecture workflows that join Knowledge, Code Map evidence, and scoped Memories.
- Keep generated findings distinct from verified durable Knowledge until a user explicitly promotes them.

## v0.17 exit criteria

- Memories can be searched, filtered by group and source, paginated, and inspected with provenance and privacy metadata.
- A user can explicitly forget one scoped memory without deleting original source files.
- Research and architecture briefs combine bounded Code Map, Knowledge, and Memories evidence.
- Brief generation is read-only and marks evidence confidence and missing source categories.
- A reviewed brief enters durable Knowledge only through an explicit user action.
- Console controls are keyboard-labeled, responsive, and respect reduced-motion preferences.

## v1.0 gate

- Freeze and publish CLI, MCP, storage, and upgrade compatibility contracts.
- Add explicit persistent-data migrations, backup, interruption recovery, and version fixture coverage.
- Harden installation, update, rollback, checksum/signature verification, and native uninstall.
- Execute clean-machine acceptance coverage against the exact release artifacts users install.

### Implemented stabilization foundations

- Pull requests and `main` pushes run the source suite and build both Windows artifacts.
- Tagged builds reject a package-version mismatch and execute the built CLI and Setup artifacts.
- A release manifest binds the tag, reported version, and SHA-256 hashes for both executables.
- PowerShell installation fails closed when the manifest or a checksum is missing or invalid.
- Native uninstall removes application files, PATH integration, shortcuts, cached installers, and rollback binaries while preserving user data.
- Memories database upgrades use explicit numbered migrations, a pre-migration SQLite backup, an integrity check, and a migration journal.
- Setup uses an atomic operation journal and resumes completed idempotent steps after interruption.

### v1.0 release-candidate verification

- Authenticode signing and installer signature enforcement are mandatory for tags.
- CycloneDX SBOM generation and GitHub build provenance are part of the release workflow.
- Tagged builds execute install, status, upgrade, rollback, uninstall, and data-preservation checks against the signed artifacts.
- The built native Setup executable walks all four pages at scaled DPI and verifies its retry state.
- Publish signed `v1.0.0-rc.1` and `v1.0.0-rc.2` prereleases first. Stable `v1.0.0` publication requires both immutable lifecycle runs to pass with no open P0 or P1 defects.
