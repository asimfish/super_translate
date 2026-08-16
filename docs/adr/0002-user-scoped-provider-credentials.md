# ADR-0002: User-Scoped Provider Credentials

## Status

Accepted

## Context

Multi-user deployments must let each account use its own translation quota
without exposing one user's API key to another user, browser code, durable job
files, logs, or PDF artifacts. The supported providers use two request
contracts: OpenAI-compatible chat completions and Anthropic Messages.

## Candidates

### Option A: Browser-only credentials sent with every translation request

Keep the key in browser storage and include it when a translation starts.

- Pros: no encrypted secret database and no server master key.
- Cons: browser storage is exposed to any same-origin script; restart recovery
  cannot rerun a job; every request and queue payload handles the plaintext key.

### Option B: Encrypted server-side credentials per access scope

Store one AES-GCM ciphertext per `(access_scope, provider)`. Bind the ciphertext
to that pair with authenticated additional data. Resolve it only while a job is
running, and pass the key to the isolated worker over a one-shot stdin pipe.

- Pros: durable restart recovery, tenant isolation, no browser key persistence,
  no plaintext key in job JSON, and a small auditable secret boundary.
- Cons: deployment must preserve and back up a 32-byte encryption master key;
  losing that key requires users to enter provider credentials again.

## Decision

Choose Option B. Provider base URLs are fixed in server code; users can change
only their key and model ID. This prevents the credentials feature from becoming
an arbitrary server-side request endpoint. API responses expose only configured
state, provider/model metadata, and a short key hint.

The legacy server-level provider keys remain available only to the `local`
administrator scope. Authenticated user scopes must configure personal keys.
Translation jobs persist their access scope, and startup migration backfills it
from the owning paper before recovery.

## Security Boundaries

- AES-256-GCM key: `PAPER_CHINA_CREDENTIAL_ENCRYPTION_KEY`, base64url encoded.
- Ciphertext AAD: application identifier, access scope, and provider.
- Secret transport: parent-to-worker stdin, closed immediately after writing.
- Durable files and job rows never contain plaintext provider keys.
- Provider credential reads always filter by both access scope and provider.
- API keys are never returned by an API response.

## Impact

- `app/core/provider_credentials.py` owns the catalog, encryption, and lookup.
- `app/api/provider_credentials.py` owns authenticated CRUD without key echo.
- `app/api/papers.py` enforces credential availability before queueing and
  resolves the same user's credential during execution.
- `app/services/worker.py` receives the ephemeral key through stdin.
- `pdf_zh_translator/translators.py` supports OpenAI-compatible, DeepSeek, and
  Anthropic request contracts.
