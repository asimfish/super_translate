# ADR 0009: Native Zotero translation client

Status: Accepted for the plugin preview.

## Context

Researchers need to translate an existing PDF without leaving Zotero, track an asynchronous server task, and preserve the original attachment and annotations. The existing HTTP service already owns authentication, provider credentials, model catalogs, translation and quality checks.

## Candidates

1. Embed the existing website in a browser panel. This reuses UI but cannot reliably associate local attachments or recover imports, and adds cookie and navigation boundaries.
2. Use Zotero's supported ItemPaneManager, MenuManager, Reader and PreferencePanes APIs with a separate HTTP client. This requires a small adapter but supports native library permissions, durable tasks and attachment import.

## Decision

Choose option 2. A dependency-free protocol/controller module is tested independently; the Zotero adapter owns files, UI, login storage and attachment transactions. JSON is the API and durable state format. No engine or service changes are required. Initial install compatibility is limited to Zotero 10.0, matching the actual native runtime (10.0.4); the initially inspected application metadata had reported 9.0.6 before startup. Other major versions require their own native verification before widening the manifest.

## Boundaries

- Only selected local PDFs are uploaded. HTTPS is required except loopback HTTP. Credentials stay in Zotero's password manager; redirect following, cookies and automatic POST retries are disabled.
- Tasks are partitioned by service origin and credential fingerprint, and keyed by original attachment identity, PDF digest and output variant. Restart only resumes status reads; ambiguous submissions require an explicit retry.
- Server model selection is account-wide, and the settings explicitly state this before saving. The plugin does not promise per-job model isolation unsupported by the service API.
- Results are downloaded as authenticated bytes, validated as PDFs and imported under the source parent using Zotero's API. An opaque task tag identifies completed imports. Before import, a durable intent records the result title and SHA-256; if Zotero stops between its import transaction and tagging, recovery verifies parent, title and exact PDF digest before reusing the attachment. Nested import transactions are unsupported. The source PDF digest is checked again before attaching a result. Original files and annotations are never overwritten.
- Quality failures and repair-pending states remain visible. Plugin integration does not certify all 20 papers or change the translation acceptance criteria.
- Disabling removes registered UI and timers; existing result attachments remain ordinary Zotero PDFs. Task history and saved credentials are retained until the user logs out or removes the profile data.
