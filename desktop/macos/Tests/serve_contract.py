"""Real FastAPI routes with an isolated deterministic translation worker."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

import fitz
import uvicorn
from fastapi.responses import JSONResponse, RedirectResponse

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
root = Path(sys.argv[1]).resolve()
port = int(sys.argv[2])
root.mkdir(parents=True, exist_ok=True)
os.chdir(root)  # No production .env or data directory is read.
os.environ.update(
    {
        "PAPER_CHINA_BASE_DIR": str(root),
        "PAPER_CHINA_API_TOKEN": "desktop-local-fixture",
        "PAPER_CHINA_WORKSPACE_TOKENS": (
            "other:desktop-other-fixture,local:desktop-slow-fixture,local:desktop-reconnect-fixture"
        ),
        "PAPER_CHINA_DEEPSEEK_API_KEY": "fixture-never-used",
        "PAPER_CHINA_CREDENTIAL_ENCRYPTION_KEY": base64.urlsafe_b64encode(bytes(32)).decode(),
        "PAPER_CHINA_RATE_LIMIT_PER_MINUTE": "1200",
        "PAPER_CHINA_RATE_LIMIT_PER_HOUR": "6000",
        "PAPER_CHINA_TRANSLATION_BACKEND": "deepseek",
        "PAPER_CHINA_RESUME_QUEUED_TRANSLATIONS_ON_STARTUP": "false",
        "PAPER_CHINA_FEISHU_WEBHOOK_URL": "",
        "PAPER_CHINA_DB_PATH": "data/fixture.db",
        "PAPER_CHINA_PAPERS_DIR": "data/papers",
        "PAPER_CHINA_TRANSLATIONS_DIR": "data/translations",
    }
)

from app.api import papers, provider_credentials  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.core.database import async_session  # noqa: E402
from app.core.provider_credentials import encrypt_api_key, load_provider_credential  # noqa: E402
from app.core.users import create_user  # noqa: E402
from app.main import app  # noqa: E402
from app.models.paper import Paper, TranslationJob  # noqa: E402
from app.models.provider_credential import ProviderCredential  # noqa: E402
from app.services.provider_model_catalog import (  # noqa: E402
    ProviderModelCatalog,
    curated_provider_models,
)

events: list[dict] = []
lost_ids: set[str] = set()
reconnect_failures: set[str] = set()
original_lifespan = app.router.lifespan_context


@asynccontextmanager
async def fixture_lifespan(application):
    async with original_lifespan(application):
        await create_user("desktop_fixture", "fixture-password", access_scope="local")
        async with async_session() as db:
            db.add(
                ProviderCredential(
                    access_scope="local",
                    provider="deepseek",
                    model="deepseek-v4-pro",
                    encrypted_api_key=encrypt_api_key("fixture-never-used", "local", "deepseek"),
                )
            )
            await db.commit()
        yield


async def fixture_catalog(provider, *_args, **_kwargs):
    return ProviderModelCatalog(
        provider=provider,
        models=curated_provider_models(provider),
        source="fixture",
        available_models=("deepseek-flash", "deepseek-v4-pro") if provider == "deepseek" else (),
    )


async def finish_translation(paper_id: str, job_id: str) -> None:
    async with async_session() as db:
        paper = await db.get(Paper, paper_id)
        original_name = paper.original_filename
    await asyncio.sleep(1 if original_name == "cancel-fixture.pdf" else 0.2)
    async with async_session() as db:
        paper = await db.get(Paper, paper_id)
        job = await db.get(TranslationJob, job_id)
        if job.cancel_requested:
            paper.translation_status = "pending"
            paper.translation_progress = 0
            job.status = "cancelled"
        else:
            output = settings.translations_path / f"{paper_id}.zh.pdf"
            with fitz.open() as document:
                page = document.new_page()
                page.insert_text(
                    (72, 100),
                    "悬窗译文：这是接口验证使用的合成 PDF。",
                    fontname="china-s",
                    fontsize=14,
                )
                document.save(output)
            paper.translated_filename = output.name
            paper.dual_filename = output.name
            paper.translation_status = "completed"
            paper.translation_progress = 1
            paper.translation_stage = "完成"
            job.status = "completed"
            job.progress = 1
            if original_name == "qa-failure.pdf":
                paper.translation_status = "failed"
                paper.translation_error = "译后检查未通过：表格结构错位（第 17 页）。译文已保留。"
                paper.translation_stage = "等待系统修复"
                job.status = "failed"
                job.error = paper.translation_error
                output.with_suffix(".qa.json").write_text(
                    json.dumps(
                        {
                            "status": "failed",
                            "issues": [
                                {
                                    "page": 17,
                                    "code": "table_structure_mismatch",
                                    "severity": "error",
                                    "message": "Table rule ink is missing on page 17",
                                }
                            ],
                        }
                    )
                )
        job.finished_at = datetime.now()
        await db.commit()


def schedule_fixture(_function, *args):
    return asyncio.create_task(finish_translation(args[0], args[10]))


@app.middleware("http")
async def fixture_events(request, call_next):
    path = request.url.path
    event = {
        "method": request.method,
        "path": path,
        "query": request.url.query,
        "authorized_header": bool(request.headers.get("Authorization")),
    }
    events.append(event)
    if path == "/__desktop_test/events":
        return JSONResponse(events)
    if (
        request.headers.get("Authorization") == "Bearer desktop-reconnect-fixture"
        and not reconnect_failures
    ):
        reconnect_failures.add("first-connection")
        return JSONResponse({"detail": "fixture temporary outage"}, status_code=503)
    if request.headers.get("Authorization") == "Bearer desktop-redirect-fixture":
        return RedirectResponse(f"http://127.0.0.1:{port}/__desktop_test/leak", status_code=307)
    if request.headers.get("Authorization") == "Bearer desktop-slow-fixture":
        await asyncio.sleep(0.8)
    response = await call_next(request)
    if path == "/health":
        response.headers["X-SuperTranslate-Desktop-Test"] = "1"
    if request.method == "POST" and path.endswith("/translate") and response.status_code == 200:
        paper_id = path.split("/")[-2]
        async with async_session() as db:
            paper = await db.get(Paper, paper_id)
            backend = request.query_params.get("backend") or settings.translation_backend
            credential = await load_provider_credential(db, paper.access_scope, backend)
            event["effective_model"] = credential.model if credential else ""
        if paper.original_filename == "lost-start-response.pdf" and paper_id not in lost_ids:
            lost_ids.add(paper_id)
            return JSONResponse(
                {"detail": "fixture lost response after successful submission"}, status_code=503
            )
    return response


app.router.lifespan_context = fixture_lifespan
provider_credentials.get_provider_model_catalog = fixture_catalog
papers._schedule_background_task = schedule_fixture
uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
