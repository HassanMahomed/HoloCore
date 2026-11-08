# Code signing policy

This policy applies to official HoloCore Windows releases built from the public [HassanMahomed/HoloCore](https://github.com/HassanMahomed/HoloCore) repository.

HoloCore intends to apply for free open-source code signing through SignPath Foundation:

> Free code signing provided by SignPath.io, certificate by SignPath Foundation.

Signing is not active until SignPath Foundation accepts the project and the trusted-build integration is complete. Until then, official Windows executables are published as unsigned community builds with SHA-256 checksums, a CycloneDX SBOM, and GitHub build-provenance attestations.

## Signing scope

Only these HoloCore-owned Windows release artifacts may be submitted:

- `holocore-windows-x64.exe`
- `holocore-setup-windows-x64.exe`

The setup executable embeds the engine executable produced by the same public workflow. HoloCore will not submit unrelated, third-party, locally substituted, or fork-produced binaries under its signing policy.

## Trusted build and release process

- Official artifacts are built by the repository's GitHub Actions Windows release workflow.
- Release signing is limited to numbered release-candidate and stable tags created from reviewed source on `main`.
- The workflow runs the complete test suite, executes both built artifacts, walks the native setup UI, generates checksums and an SBOM, exercises install/upgrade/rollback/uninstall, and publishes provenance attestations.
- Signing-service credentials and identifiers must remain in GitHub encrypted secrets and must never be committed or printed.
- A release is described as signed only after Windows signature verification succeeds.

## Roles

- Committer and reviewer: [HassanMahomed](https://github.com/HassanMahomed)
- Release approver: [HassanMahomed](https://github.com/HassanMahomed)

Repository and signing-service access must use multi-factor authentication. Contributions from other people require review before they can enter an official release.

## Privacy and system changes

HoloCore is local-first. It does not transfer information to networked systems unless the user or installer explicitly requests or enables the relevant action, such as ingesting a URL, checking GitHub for updates, or connecting to a user-selected service. Local Ollama detection is restricted to loopback.

The setup application previews selected folders, AI connections, local models, and planned changes before applying them. HoloCore supplies explicit upgrade, rollback, and uninstall operations. Uninstall removes application files and integrations while preserving the user's HoloCore Home and project data.

## Verification and incident response

Users should obtain releases only from the official GitHub Releases page and compare published SHA-256 checksums. If signing is active, users should also verify the Authenticode publisher and signature status.

Suspected signing-key misuse, compromised release artifacts, or policy violations must stop publication immediately. The maintainer will investigate, remove affected assets, notify SignPath when applicable, request revocation when required, and publish corrected artifacts only after the release path is secure.
