# Super Translate

> AI-Powered Academic Paper Translation & Reading System

[![Tests](https://img.shields.io/badge/tests-635%20passed-brightgreen)]()
[![Coverage](https://img.shields.io/badge/coverage-99%25-brightgreen)]()
[![Lint](https://img.shields.io/badge/lint-zero%20violations-brightgreen)]()
[![Python](https://img.shields.io/badge/python-3.12+-blue)]()

Super Translate is a web-based system for translating English academic papers into Chinese while preserving the original formatting, mathematical formulas, and document structure.

## Features

- **Smart Translation Engine** — Supports DeepSeek, OpenAI, and Google Translate backends with automatic fallback
- **Layout Preservation** — Maintains original page dimensions, images, vector graphics, and text block positions
- **Formula Protection** — Mathematical formulas, equations, and variables are preserved as-is
- **Figure/Text Safety** — Preserves figure internals while translating captions and surrounding prose
- **Citation Safety** — Reference markers [1], [2] and citation formatting remain unchanged
- **Real-time Progress** — Live translation progress with ETA and detailed status logs
- **Durable Translation Jobs** — Tracks each translation run with job history, cancellation state, heartbeat, progress, and restart failure recovery
- **Post-translation QA** — Supports single-pass or iterative checks for untranslated English, missing images, empty pages, text overlap, visual layout regressions, and machine-readable `*.qa.json` reports
- **OCR Fallback** — Optional scanned-PDF OCR path for image-only papers before translation
- **Conference Terminology Corpus** — 1000+ curated AI conference terms across NeurIPS, ICML, ICLR, CVPR, ACL, systems, agents, and safety tracks, with a `corpus-lint` consistency gate and post-translation terminology adherence checks
- **Golden Regression Evaluation** — Build and run PDF layout/quality regression sets for large paper batches
- **Template Layout Learning** — Learn ACM/IEEE/Springer/ACL-style layout profiles from representative PDFs
- **Editable Figure PPT Provenance** — Figure PPT assets must be finalized by `image-to-editable-ppt`/`editppt` and registered with auditable run, page, validation, and hash evidence
- **Dual View** — Side-by-side PDF viewer with synchronized scrolling and adjustable split
- **Batch Processing** — Translate multiple papers simultaneously
- **Feishu Notifications** — Get notified via Feishu/Lark webhook when translation completes
- **Responsive UI** — Modern dark theme with adaptive PDF scaling

## Quick Start

### 1. Install

```bash
# Clone the repository
git clone https://github.com/asimfish/super_translate.git
cd super_translate

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
pip install -e .
```

### 2. Configure API Key

```bash
# Set your DeepSeek API key (default backend)
export PAPER_CHINA_DEEPSEEK_API_KEY="your-api-key-here"

# Or use OpenAI
export PAPER_CHINA_OPENAI_API_KEY="your-openai-key"
```

### 3. Start the Server

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8001
```

Open **http://localhost:8001** in your browser.

## Translation Quality

Super Translate produces high-quality academic translations with:

- **Pure Chinese output** — No English-Chinese mixing, proper academic terminology
- **Smart terminology** — First occurrence: "神经网络（Neural Network）", subsequent: "神经网络"
- **Format preservation** — Bold, italic, and section headers are preserved
- **Clean layout** — Automatic control character cleanup, caption compaction, and overlap checks

## Configuration

All settings can be configured via environment variables with the `PAPER_CHINA_` prefix:

| Variable | Default | Description |
|----------|---------|-------------|
| `PAPER_CHINA_DEEPSEEK_API_KEY` | — | DeepSeek API key |
| `PAPER_CHINA_OPENAI_API_KEY` | — | OpenAI API key |
| `PAPER_CHINA_TRANSLATION_ENGINE` | `native` | Translation engine (`native` or `pdf2zh`) |
| `PAPER_CHINA_TRANSLATION_BACKEND` | `deepseek` | Default translation backend |
| `PAPER_CHINA_TRANSLATION_TIMEOUT_SECONDS` | `600` | Global timeout for each translation run |
| `PAPER_CHINA_MAX_CONCURRENT_TRANSLATIONS` | `3` | Max concurrent translation jobs |
| `PAPER_CHINA_TRANSLATION_CONCURRENCY` | `4` | Parallel supplier requests within one translation (lower to `1` for rate-limited API keys) |
| `PAPER_CHINA_API_TOKEN` | — | Optional bearer token for `/api/*` requests |
| `PAPER_CHINA_WORKSPACE_TOKENS` | — | Optional comma/newline-separated `workspace:token` entries for lightweight per-workspace isolation |
| `PAPER_CHINA_ALLOW_UNAUTHENTICATED_REMOTE` | `false` | Allow remote API access without token |
| `PAPER_CHINA_FEISHU_WEBHOOK_URL` | — | Feishu webhook for notifications |

Remote access is local-only by default unless `PAPER_CHINA_API_TOKEN` or
`PAPER_CHINA_WORKSPACE_TOKENS` is set. When a token is configured, the web UI
stores it in browser local storage and sends `Authorization: Bearer <token>` for
API, PDF preview, and downloads.

`PAPER_CHINA_API_TOKEN` maps to the default `local` scope for backward
compatibility. `PAPER_CHINA_WORKSPACE_TOKENS` can contain entries such as
`lab-a:token-a,lab-b:token-b`; papers uploaded with one workspace token are
listed, translated, downloaded, and edited only from that workspace scope.

## Architecture

```
super_translate/
├── app/
│   ├── api/          # FastAPI routes
│   ├── core/         # Config, database, rate limiting
│   ├── models/       # SQLAlchemy models
│   ├── services/     # Translation, layout fixing, notifications
│   └── static/       # Frontend (HTML, CSS, JS)
├── pdf_zh_translator/ # Core translation engine
└── tests/            # Test suite (635 tests)
```

## Deployment Notes

Super Translate targets local / single-machine / small-team use:

- **Run a single worker.** Concurrency limiting, in-flight cancellation, and the
  translation queue are process-local. Running multiple uvicorn workers would
  multiply the effective concurrency limit and split cancel state, so prefer one
  worker (scale by running more translations inside it, not more workers).
- **Queued jobs survive restarts.** Translation requests are persisted before
  execution; if the process restarts while a job is still `queued`, startup
  schedules it again with the original backend, QA, OCR, and layout options.
  Jobs that were already `running` are still marked failed on restart because
  the previous process may have died mid-write.
- **SQLite is tuned for this.** The database opens in WAL mode with a busy
  timeout and `synchronous=NORMAL`, so the 2-second status polling and frequent
  progress writes can run concurrently without "database is locked" errors. For
  heavy multi-user/public deployments, migrate to PostgreSQL + an external job
  queue.
- **Authentication supports token-scoped workspaces, not full accounts.** Set
  `PAPER_CHINA_API_TOKEN` for a single default scope, or
  `PAPER_CHINA_WORKSPACE_TOKENS` for lightweight per-workspace isolation. Serve
  over HTTPS before exposing the app off `localhost`; otherwise remote clients
  are rejected unless `PAPER_CHINA_ALLOW_UNAUTHENTICATED_REMOTE=true`.

## Development

```bash
# Run tests
.venv/bin/python -m pytest tests/ -v

# Run with coverage
.venv/bin/python -m pytest tests/ --cov=app --cov-report=term-missing

# Lint check
.venv/bin/ruff check app/ tests/

# Lint the terminology corpus for cross-field conflicts (CI gate)
.venv/bin/python -m pdf_zh_translator corpus-lint --strict

# Discover a 100-paper golden regression manifest
.venv/bin/python -m pdf_zh_translator golden-discover data/golden data/golden/manifest.json

# Learn a reusable paper template layout profile
.venv/bin/python -m pdf_zh_translator layout-learn ieee data/layout-profiles/ieee.json samples/*.pdf

# Prepare/register/audit editable figure PPT assets
.venv/bin/python -m pdf_zh_translator figure-ppt-extract papers/example.pdf data/editable_figures --paper-id example
.venv/bin/python -m pdf_zh_translator figure-ppt-source-audit data/editable_figures/example/figure_sources_manifest.json
.venv/bin/python -m pdf_zh_translator figure-ppt-batch-prepare data/editable_figures/example/figure_sources_manifest.json
.venv/bin/python -m pdf_zh_translator figure-ppt-source-audit data/editable_figures/example/figure_sources_manifest.json --require-prepared
.venv/bin/python -m pdf_zh_translator figure-ppt-batch-register data/editable_figures/example/figure_sources_manifest.json
.venv/bin/python -m pdf_zh_translator figure-ppt-source-audit data/editable_figures/example/figure_sources_manifest.json --require-registered
.venv/bin/python -m pdf_zh_translator figure-ppt-prepare figures/fig1.png data/editable_figures --figure-id fig1
.venv/bin/python -m pdf_zh_translator figure-ppt-register fig1 figures/fig1.png data/editable_figures/fig1/editppt-run data/editable_figures/fig1
.venv/bin/python -m pdf_zh_translator figure-ppt-audit data/editable_figures
```

## License

MIT
