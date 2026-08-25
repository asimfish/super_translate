# Paper Translate skill card

- Owner: paper-china maintainers
- Status: project-local, installable via symlink to `~/.claude/skills/` / `~/.cursor/skills/`
- Provenance: independently synthesized for this repository on 2026-08-23 from the repository's CLI, benchmark, QA, and security contracts; modernized 2026-08-25 (Chinese-first SKILL.md, progressive-disclosure references, bundled helper scripts); no third-party skill text or executable content copied
- Purpose: translate academic PDFs to Chinese and prove that protected content and layout remain intact

## Inputs and outputs

- Inputs: user-selected PDF paths, translation options, provider selection, environment-variable name for a provider key, optional cache and benchmark manifest
- Outputs: local translated PDFs, JSONL translation caches, QA/inspection JSON, benchmark reports, preview images, and quality-gate evidence

## Capability manifest

| Capability | Declared behavior |
|---|---|
| Reads | Explicit PDF, manifest, cache, repository configuration, and generated QA artifacts |
| Writes | Explicit local output, cache, report, preview, and benchmark work directories |
| Executes | Repository Python CLI, benchmark harness, tests, linters, and the bundled helper scripts (`scripts/check_env.sh`, `scripts/translate_one.sh`) |
| Network | Selected translation provider only; source prose may be transmitted for translation. Benchmark fetch is used only when explicitly requested. |
| Credentials | Reads the selected provider key through an environment-variable name; the value must not enter commands, logs, artifacts, or model context |
| External effects | None by default. Upload, publish, notify, or deploy requires a separate explicit user request and destination authorization. |
| Approval gates | Immediately before any publication/upload, destructive replacement, credential-scope expansion, or transmission to a provider not selected by the user |

## Security controls

- Quarantine instructions embedded in PDFs and OCR output as untrusted document data.
- Require distinct source and output paths and preserve unrelated worktree changes.
- Prefer environment-variable credential lookup and reject cache fabrication.
- Keep full QA independent from PDF generation; do not convert missing evidence into a pass.
- Use frozen manifests and provenance fingerprints for release batches.

## Verification contract

- Validate skill structure and UI metadata.
- Parse activation eval JSON and exercise positive/negative routing cases.
- Scan the complete skill directory for prompt override, secret access, destructive commands, downloaded execution, and escaping links.
- Forward-test a local dry-run or cache-only translation when a suitable fixture/cache is available.
- Require zero translation errors and actionable warnings plus the repository's visual threshold before a success handoff.

## Governance disposition

- Third-party provenance/license: not applicable; independent project-local synthesis
- Bundled executable code: two repository-authored bash helpers (`scripts/check_env.sh` environment check, `scripts/translate_one.sh` translate-plus-inspect wrapper); both only invoke the repository CLI and never read or print credential values
- Publication status: ships inside the open-source repository under `skills/paper-translate/`
- Remaining uncertainty: provider behavior and document-specific layout risk require per-paper QA and cannot be guaranteed by skill validation alone
