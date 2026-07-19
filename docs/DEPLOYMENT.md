# Deployment Guide

This guide walks through running Super Translate as a personal website:
Docker + Caddy (automatic HTTPS) on any small VPS.

## System model (read this first)

Super Translate is a **stateful, single-process** application:

- Translations run in background threads inside the web process, with a
  process-local queue and cancellation state. **Never run more than one
  uvicorn worker or more than one app replica.**
- All state lives in `/app/data`: a SQLite database (WAL mode), uploaded PDFs
  (`data/papers/`), and translated outputs (`data/translations/`). That
  directory must be on a persistent volume.
- One translation can take up to 30 minutes (`PAPER_CHINA_TRANSLATION_TIMEOUT_SECONDS`).
  The reverse proxy must tolerate long-running requests, and the host should
  not kill or migrate the container mid-run.

Because of this, a plain VPS (2 CPU / 4GB RAM / 20GB+ disk) with Docker is the
most reliable target. Platforms that scale to zero, migrate machines, or use
ephemeral filesystems (Vercel, Cloud Run default mode) will lose data or kill
long translations.

## 1. Prerequisites

- A VPS with Docker and Docker Compose installed.
- A domain name with an `A`/`AAAA` record pointing at the VPS
  (needed for automatic HTTPS; skip if you only use it inside a LAN).
- A DeepSeek / OpenAI / Moonshot API key.

## 2. Configure

```bash
git clone https://github.com/asimfish/super_translate.git
cd super_translate

cp .env.example .env
```

Edit `.env` and set at minimum:

```bash
# Translation backend key (any one of these)
PAPER_CHINA_DEEPSEEK_API_KEY=sk-...

# REQUIRED on a public server: strong random bearer token.
# Generate: python3 -c "import secrets; print(secrets.token_urlsafe(32))"
PAPER_CHINA_API_TOKEN=<paste the generated token>

# Public URL (used in notification links)
PAPER_CHINA_BASE_URL=https://translate.example.com
```

Then edit `Caddyfile` and replace `your-domain.example.com` with your domain.

Security notes:

- Without `PAPER_CHINA_API_TOKEN`, requests arriving through a reverse proxy
  look like loopback traffic to the app and would be treated as trusted local
  requests. **On any public deployment the token is mandatory.**
- Keep `PAPER_CHINA_ALLOW_UNAUTHENTICATED_REMOTE=false` (the compose file
  pins it).
- The web UI asks for the token once and stores it in browser local storage.

## 3. Launch

```bash
docker compose up -d --build
```

First start:

1. Open `https://your-domain` — the UI loads and prompts for the API token.
2. Upload a small PDF and run a translation end to end.
3. Check `docker compose logs -f app` while it runs.

Health check: `curl https://your-domain/health` returns
`{"status":"ok","version":...}` without authentication.

## 4. Chinese fonts (already handled)

Translated text needs a CJK font. The Docker image installs `fonts-noto-cjk`,
which the engine discovers automatically under `/usr/share/fonts`. To use a
different font, mount it into the container and set:

```yaml
environment:
  PDF_ZH_FONT_FILE: /fonts/MyFont.otf
volumes:
  - ./fonts:/fonts:ro
```

## 5. Backup and restore

Everything worth keeping is in the `app-data` volume:

```bash
# Backup
docker run --rm -v super_translate_app-data:/data -v "$PWD":/backup alpine \
  tar czf /backup/super-translate-backup.tgz -C /data .

# Restore
docker run --rm -v super_translate_app-data:/data -v "$PWD":/backup alpine \
  tar xzf /backup/super-translate-backup.tgz -C /data
```

Translated outputs are never cleaned up automatically; prune
`data/translations/` occasionally if disk fills up.

## 6. Upgrades

```bash
git pull
docker compose up -d --build
```

Queued translation jobs survive restarts (they are re-scheduled at startup);
jobs that were mid-run when the process stopped are marked failed and can be
retried from the UI.

## 7. Troubleshooting

| Symptom | Cause / fix |
|---------|-------------|
| `401 Invalid or missing API token` | UI/token mismatch. Re-enter the token from `.env` in the browser prompt. |
| `403 Remote API access requires PAPER_CHINA_API_TOKEN` | You exposed the app publicly without a token. Set `PAPER_CHINA_API_TOKEN` and restart. |
| Upload fails at ~100MB | `PAPER_CHINA_MAX_UPLOAD_SIZE` (bytes) and the `request_body max_size` in `Caddyfile` both cap uploads. |
| `No CJK font available` | The image should include `fonts-noto-cjk`; if you built a custom image, install it or set `PDF_ZH_FONT_FILE`. |
| Translation fails immediately with missing key | The backend selected in the UI has no API key in `.env`. |
| HTTP 429 from the translation provider | Set `PAPER_CHINA_TRANSLATION_CONCURRENCY=1` and keep `PAPER_CHINA_MAX_CONCURRENT_TRANSLATIONS=1`. |
| Long papers time out | Raise `PAPER_CHINA_TRANSLATION_TIMEOUT_SECONDS` (also raise the Caddy `response_header_timeout`). |

## Alternative platforms

- **Fly.io / Railway**: workable if you attach a persistent volume, pin a
  single machine, and disable scale-to-zero and autoscaling. Machine restarts
  will kill in-flight translations.
- **Local only (no server)**: skip Docker entirely — `pip install -e .`,
  `python -m uvicorn app.main:app --port 8001`, open `http://localhost:8001`.
  No token needed for loopback use.
