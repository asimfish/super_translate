# Super Translate — production image
#
# Constraints baked into this image (see README "Deployment Notes"):
# * Single uvicorn worker: translation queue/cancel state is process-local.
# * fonts-noto-cjk is mandatory: translated Chinese text needs a CJK font,
#   and pdf_zh_translator discovers Noto CJK under /usr/share/fonts.
# * All state lives in /app/data — mount a volume there.

FROM python:3.12-slim AS builder

# Override for restricted networks, e.g.
#   docker build --build-arg PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/ .
ARG PIP_INDEX_URL=https://pypi.org/simple
ENV PIP_INDEX_URL=${PIP_INDEX_URL} \
    UV_INDEX_URL=${PIP_INDEX_URL} \
    UV_DEFAULT_INDEX=${PIP_INDEX_URL}

WORKDIR /app

RUN pip install --no-cache-dir uv

COPY pyproject.toml README.md ./
COPY app ./app
COPY pdf_zh_translator ./pdf_zh_translator

RUN uv pip install --system --no-cache .

FROM python:3.12-slim AS runner

# Noto CJK fonts for translated text; tini for clean signal handling.
RUN apt-get update \
    && apt-get install -y --no-install-recommends fonts-noto-cjk tini curl \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 1001 appuser

WORKDIR /app

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --chown=appuser:appuser app ./app
COPY --chown=appuser:appuser pdf_zh_translator ./pdf_zh_translator

# App state (SQLite DB, uploaded papers, translated outputs) lives here.
RUN mkdir -p /app/data && chown -R appuser:appuser /app/data
VOLUME ["/app/data"]

USER appuser

ENV PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

ENTRYPOINT ["tini", "--"]
# Single worker is required; scale translations inside the process instead.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
