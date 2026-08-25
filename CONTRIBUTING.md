# 贡献指南

感谢关注 SuperTranslate。这是一个保版式学术论文 PDF 翻译系统，对翻译质量和版式回归极为敏感，请在动手前读完本文（5 分钟）。

## 开发环境

要求 Python >= 3.10（CI 用 3.13）。推荐 [uv](https://docs.astral.sh/uv/)，仓库自带 `uv.lock`：

```bash
git clone https://github.com/asimfish/super_translate.git
cd super_translate
uv sync --extra dev
```

不用 uv 的话：

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

启动本地 Web 服务验证环境：

```bash
uv run uvicorn app.main:app --host 127.0.0.1 --port 8001
```

## 测试怎么跑

```bash
# 全量测试（不含真实浏览器 e2e）
uv run pytest -q -m "not e2e"

# 单个文件 / 单个用例（改哪跑哪，最常用）
uv run pytest tests/test_pdf_layout_preserve.py -q
uv run pytest tests/test_translators.py -k cache -q

# e2e 测试需要一次性安装 chromium
uv run playwright install chromium
uv run pytest -q -m e2e

# 轻量冒烟（CI 同款，几十秒）
bash scripts/smoke_test.sh
```

约定：

- 修引擎（`pdf_zh_translator/`）必须带针对性的回归用例，版式类修复优先用 `tests/fixtures/` 里的真实论文页做正例，必要时补相邻反例。
- QA / 几何启发式的改动，除了新用例还要确认既有 fixtures 不被误伤（跑全量）。
- 提交前最低要求：`ruff` 干净 + 冒烟通过 + 改动相关的测试文件通过。

## 代码风格

- `ruff` 是唯一门槛：`uv run ruff check app pdf_zh_translator tests scripts`（行宽 100，规则 E/F/I/N/W，配置见 `pyproject.toml`）。
- 注释用中文，只解释「为什么」，不复述代码在做什么。
- 代码简单直接，别为将来可能的需求加抽象层。
- 新文件名一律小写下划线（`layout_fix.py`，不要 `LayoutFix.py`）。
- 不提交 `data/`、`*.db`、翻译产物 PDF、`.env`、API key。

## 术语库

翻译提示词依赖 `pdf_zh_translator/corpus.json` 术语库，改动后必须通过一致性检查（CI 门槛）：

```bash
uv run python -m pdf_zh_translator corpus-lint --strict
```

批量术语变更走 `corpus-review → corpus-promote` 流程，见 `skills/paper-translate/references/cli.md`。

## 提交与 PR 流程

提交信息格式沿用现有历史：`type: 一句话小写描述`（英文），type 取 `feat` / `fix` / `test` / `docs` / `perf` / `style` / `revert`。例如：

```
fix: preserve formula paragraph scale at column end
```

流程：

1. Fork 后从 `main` 拉分支（`fix/xxx`、`feat/xxx`）。
2. 改动 + 测试 + 本地跑通上面的检查。
3. 发 PR，按模板填写测试证据；涉及翻译/版式行为的改动，请附至少一篇真实论文的前后对比或 QA 报告要点。
4. CI（lint + 冒烟）必须绿；review 意见逐条回应。

用户可见的变更请在 PR 里同步补一行 `CHANGELOG.md` 的 `[Unreleased]`。

## 许可证

本项目为 AGPL-3.0-or-later（依赖 pdf2zh 与 PyMuPDF 均为 AGPL）。提交贡献即表示你同意以相同许可证发布你的代码。
