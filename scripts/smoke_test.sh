#!/usr/bin/env bash
# 轻量冒烟测试：验证 CLI、术语库、Web 应用导入与 dry-run 排版链路。
# 不联网、不跑重型渲染测试；CI 与本地共用。
# 用法: bash scripts/smoke_test.sh   （在仓库根执行；可用 PYTHON=... 覆盖解释器）
set -euo pipefail
cd "$(dirname "$0")/.."

PY=${PYTHON:-.venv/bin/python}
if [ ! -x "$PY" ]; then
  # ${PY} 加大括号：bash 3.2 在 $VAR 后紧跟全角字符时会解析出错
  echo "错误: 找不到 ${PY}，请先执行 uv sync（或设置 PYTHON=解释器路径）" >&2
  exit 1
fi

echo "[1/4] CLI 参数解析"
"$PY" -m pdf_zh_translator translate --help >/dev/null
"$PY" -m pdf_zh_translator inspect --help >/dev/null

echo "[2/4] 术语库一致性（corpus-lint --strict）"
"$PY" -m pdf_zh_translator corpus-lint --strict

echo "[3/4] Web 应用导入"
"$PY" -c "from app.main import app; print('FastAPI app:', app.title)"

echo "[4/4] dry-run 翻译单页样张 + 输出校验"
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
"$PY" -m pdf_zh_translator translate \
  tests/fixtures/gears_p1_title.pdf "$tmp/out.zh.pdf" --dry-run --quiet
"$PY" - "$tmp/out.zh.pdf" <<'EOF'
import sys

import fitz

doc = fitz.open(sys.argv[1])
assert doc.page_count >= 1, "dry-run 输出没有页面"
text = "".join(page.get_text() for page in doc)
assert text.strip(), "dry-run 输出没有文本"
print(f"dry-run 输出 {doc.page_count} 页，含 {len(text)} 字符")
EOF

echo "冒烟测试通过"
