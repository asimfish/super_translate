"""Tests for API access scopes and workspace token isolation."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.dialects import sqlite

from app.api.papers import _get_paper_or_404, list_papers, start_translation
from app.core.access import (
    LOCAL_ACCESS_SCOPE,
    REMOTE_UNAUTHENTICATED_SCOPE,
    api_access_decision,
    configured_token_scopes,
    workspace_token_scopes,
)


def _sql(statement) -> str:
    return str(
        statement.compile(
            dialect=sqlite.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )


class _Result:
    def __init__(self, paper=None, papers=None):
        self._paper = paper
        self._papers = papers or []

    def scalar_one_or_none(self):
        return self._paper

    def scalars(self):
        values = self._papers

        class _Scalars:
            def all(self):
                return values

        return _Scalars()


class _Db:
    def __init__(self):
        self.queries = []
        self.scalar_queries = []
        self.added = []
        self.committed = False
        self.refreshed = None

    async def execute(self, query):
        self.queries.append(query)
        result = MagicMock()
        result.rowcount = 1
        result.scalar_one_or_none.return_value = MagicMock()
        result.scalars.return_value.all.return_value = []
        return result

    async def scalar(self, query):
        self.scalar_queries.append(query)
        return 0

    def add(self, item):
        self.added.append(item)

    async def commit(self):
        self.committed = True

    async def refresh(self, item):
        self.refreshed = item


def test_workspace_token_scopes_parse_named_entries():
    assert workspace_token_scopes("team-a:tok-a, team_b=tok-b, tok-c") == (
        ("tok-a", "team-a"),
        ("tok-b", "team_b"),
        ("tok-c", "workspace_3"),
    )


def test_configured_token_scopes_keep_api_token_as_local_scope():
    assert configured_token_scopes("admin-token", "team:team-token") == (
        ("admin-token", LOCAL_ACCESS_SCOPE),
        ("team-token", "team"),
    )


def test_api_access_decision_accepts_workspace_token_scope():
    decision = api_access_decision(
        authorization="Bearer tok-a",
        client_host="203.0.113.10",
        api_token="",
        workspace_tokens="team-a:tok-a",
        allow_unauthenticated_remote=False,
    )
    assert decision.allowed is True
    assert decision.authenticated is True
    assert decision.scope == "team-a"


def test_api_access_decision_rejects_missing_workspace_token():
    decision = api_access_decision(
        authorization="",
        client_host="127.0.0.1",
        api_token="",
        workspace_tokens="team-a:tok-a",
        allow_unauthenticated_remote=True,
    )
    assert decision.allowed is False
    assert decision.status_code == 401


def test_api_access_decision_rejects_remote_without_token_by_default():
    decision = api_access_decision(
        authorization="",
        client_host="203.0.113.10",
        api_token="",
        workspace_tokens="",
        allow_unauthenticated_remote=False,
    )
    assert decision.allowed is False
    assert decision.status_code == 403


def test_api_access_decision_remote_allow_uses_remote_scope():
    decision = api_access_decision(
        authorization="",
        client_host="203.0.113.10",
        api_token="",
        workspace_tokens="",
        allow_unauthenticated_remote=True,
    )
    assert decision.allowed is True
    assert decision.scope == REMOTE_UNAUTHENTICATED_SCOPE


@pytest.mark.asyncio
async def test_get_paper_or_404_filters_by_access_scope():
    paper = MagicMock()
    db = AsyncMock()
    db.execute.return_value = _Result(paper=paper)

    assert await _get_paper_or_404("abcd12345678", db, "team-a") is paper

    query = db.execute.await_args.args[0]
    sql = _sql(query)
    assert "papers.id = 'abcd12345678'" in sql
    assert "papers.access_scope = 'team-a'" in sql


@pytest.mark.asyncio
async def test_list_papers_filters_by_access_scope():
    db = _Db()

    response = await list_papers(db, "team-a")

    assert response.total == 0
    assert "papers.access_scope = 'team-a'" in _sql(db.scalar_queries[0])
    assert "papers.access_scope = 'team-a'" in _sql(db.queries[0])


@pytest.mark.asyncio
async def test_start_translation_update_filters_by_access_scope():
    db = _Db()
    background_tasks = MagicMock()

    with patch("app.api.papers._schedule_background_task") as mock_schedule:
        response = await start_translation("abcd12345678", background_tasks, db, "team-a")

    assert response["ok"] is True
    assert db.committed is True
    assert "papers.access_scope = 'team-a'" in _sql(db.queries[0])
    assert mock_schedule.call_count == 1
