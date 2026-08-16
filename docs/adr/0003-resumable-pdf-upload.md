# ADR-0003: Proxy-safe resumable PDF uploads

## Status

Accepted

## Context

Forwarding services can terminate or lose the response for a large multipart
request even when the application has already committed the PDF. Retrying the
request then creates duplicates. Raising only the FastAPI or Caddy body limit
does not control limits or connection lifetimes in third-party intermediaries.

## Decision

Files of at least 8 MiB use a resumable protocol:

1. The browser creates a stable 128-bit upload ID and initializes a session.
2. It sends bounded 4 MiB chunks with a SHA256 header, retrying each chunk.
3. The server binds session lookup to `access_scope`, writes each chunk
   atomically, and reports durable chunk indexes.
4. Completion assembles into the papers filesystem, verifies the PDF header,
   EOF marker, byte count, and complete SHA256, then commits the paper and
   session in one database transaction.
5. Cross-process file locks and tenant-plus-content-hash lookup make completion
   idempotent. A repeated completion returns the existing paper.

Chunk storage and session records live under `data/`, so ordinary backups and
container volume mounts preserve in-flight uploads. Stale incomplete sessions
are bounded per tenant and expire after 24 hours.

## Alternatives considered

**Raise body and timeout limits.** Rejected as the sole fix because external
forwarders remain outside our control, response loss still creates duplicates,
and every retry retransmits the full PDF.

**Upload directly to S3-compatible object storage.** Viable for a larger hosted
service, but rejected for the current release because it adds credentials,
deployment dependencies, CORS policy, and lifecycle management to a tool that
must still work on one local machine.

**Store chunks only in process memory.** Rejected because restarts and multiple
workers would lose or disagree on progress.

## Consequences

The API has init, status, chunk, and completion routes in addition to the legacy
multipart route for small files. Disk usage can temporarily approach twice the
PDF size during assembly. SHA256 computation adds bounded client and server CPU
work, while retries transfer only the missing chunks.
