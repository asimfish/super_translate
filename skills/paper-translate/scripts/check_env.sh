#!/usr/bin/env bash
# paper-translate 环境自检：定位仓库根、检查虚拟环境与 API key。
# 用法: bash check_env.sh
set -euo pipefail

# --- 定位仓库根：优先 SUPER_TRANSLATE_HOME，其次沿脚本真实路径向上三级 ---
resolve_repo_root() {
  if [ -n "${SUPER_TRANSLATE_HOME:-}" ]; then
    echo "$SUPER_TRANSLATE_HOME"
    return
  fi
  # 解析 symlink 后的脚本真实路径（skill 目录可能被软链到 ~/.claude/skills 等处）
  local real_script
  real_script=$(python3 -c "import os,sys; print(os.path.realpath(sys.argv[1]))" "${BASH_SOURCE[0]}")
  # scripts/ -> paper-translate/ -> skills/ -> 仓库根
  dirname "$(dirname "$(dirname "$(dirname "$real_script")")")"
}

REPO_ROOT=$(resolve_repo_root)
fail=0

echo "== paper-translate 环境自检 =="
echo "仓库根: $REPO_ROOT"

# --- 1. 仓库结构 ---
if [ -f "$REPO_ROOT/pyproject.toml" ] && [ -d "$REPO_ROOT/pdf_zh_translator" ]; then
  echo "[ok] 仓库结构正常（找到 pyproject.toml 与 pdf_zh_translator/）"
else
  echo "[缺] 未找到 SuperTranslate 仓库。请 git clone 后设置："
  echo "     export SUPER_TRANSLATE_HOME=/path/to/super_translate"
  exit 1
fi

# --- 2. 虚拟环境 ---
PY="$REPO_ROOT/.venv/bin/python"
if [ -x "$PY" ]; then
  echo "[ok] 虚拟环境: $PY ($("$PY" -V 2>&1))"
else
  echo "[缺] 没有 .venv。在仓库根执行： uv sync"
  echo "     （或 python3 -m venv .venv && .venv/bin/pip install -e .）"
  exit 1
fi

# --- 3. 引擎导入 ---
if "$PY" -c "import fitz, pdf_zh_translator" 2>/dev/null; then
  echo "[ok] 引擎可导入（PyMuPDF + pdf_zh_translator）"
else
  echo "[缺] 引擎导入失败。在仓库根重新执行： uv sync"
  fail=1
fi

# --- 4. API key（只报变量名，不打印值） ---
key_found=""
for name in PAPER_CHINA_DEEPSEEK_API_KEY DEEPSEEK_API_KEY PDF_TRANSLATOR_API_KEY; do
  if [ -n "${!name:-}" ]; then
    key_found="$name"
    break
  fi
done
if [ -n "$key_found" ]; then
  echo "[ok] 检测到 API key 环境变量: $key_found"
else
  echo "[提示] 未检测到 API key（PAPER_CHINA_DEEPSEEK_API_KEY / DEEPSEEK_API_KEY / PDF_TRANSLATOR_API_KEY）。"
  echo "       仍可用 --dry-run 验证排版，或 --api-mode cache-only 重放缓存。"
fi

if [ "$fail" -ne 0 ]; then
  exit 1
fi
echo "== 自检通过 =="
