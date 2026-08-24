#!/usr/bin/env bash
# 翻译一篇论文 PDF 并自动跑译后视觉检查。
# 用法: bash translate_one.sh INPUT.pdf [OUTPUT.pdf] [额外 translate 参数...]
# 例:   bash translate_one.sh paper.pdf                       # 输出 paper.zh.pdf
#       bash translate_one.sh paper.pdf out.pdf --dry-run     # 无 key 排版试跑
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "用法: bash translate_one.sh INPUT.pdf [OUTPUT.pdf] [额外 translate 参数...]" >&2
  exit 2
fi

# --- 定位仓库根（同 check_env.sh 规则）---
if [ -n "${SUPER_TRANSLATE_HOME:-}" ]; then
  REPO_ROOT="$SUPER_TRANSLATE_HOME"
else
  real_script=$(python3 -c "import os,sys; print(os.path.realpath(sys.argv[1]))" "${BASH_SOURCE[0]}")
  REPO_ROOT=$(dirname "$(dirname "$(dirname "$(dirname "$real_script")")")")
fi
PY="$REPO_ROOT/.venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "错误: ${REPO_ROOT} 下没有 .venv，请先在仓库根执行 uv sync" >&2
  exit 1
fi

INPUT="$1"
shift
if [ ! -f "$INPUT" ]; then
  echo "错误: 输入 PDF 不存在: ${INPUT}" >&2
  exit 1
fi

# 第二个位置参数（不以 - 开头）视为输出路径，否则默认写在输入旁: <名>.zh.pdf
if [ $# -ge 1 ] && [[ "$1" != -* ]]; then
  OUTPUT="$1"
  shift
else
  OUTPUT="${INPUT%.pdf}.zh.pdf"
fi

# --- API key 探测：只在需要联网翻译且用户没自带 key 参数时介入 ---
extra_args=("$@")
joined=" $* "
dry_run=0
case "$joined" in *" --dry-run "*) dry_run=1 ;; esac
needs_key=1
case "$joined" in *" --dry-run "*|*cache-only*|*" --api-key"*) needs_key=0 ;; esac

key_flags=()
if [ "$needs_key" -eq 1 ]; then
  if [ -n "${PAPER_CHINA_DEEPSEEK_API_KEY:-}" ]; then
    key_flags=(--api-key-env PAPER_CHINA_DEEPSEEK_API_KEY)
  elif [ -n "${DEEPSEEK_API_KEY:-}" ] || [ -n "${PDF_TRANSLATOR_API_KEY:-}" ]; then
    : # CLI 内置回退会读这两个变量
  else
    echo "错误: 未检测到 API key。请设置 PAPER_CHINA_DEEPSEEK_API_KEY（或 DEEPSEEK_API_KEY /" >&2
    echo "      PDF_TRANSLATOR_API_KEY），或改用 --dry-run 做排版试跑。" >&2
    exit 1
  fi
fi

# 注意：字符串里变量一律写 ${VAR}——macOS 自带 bash 3.2 在 $VAR 后紧跟全角字符时会解析出错
echo "== 翻译: ${INPUT} -> ${OUTPUT}"
cd "$REPO_ROOT"
"$PY" -m pdf_zh_translator translate "$INPUT" "$OUTPUT" \
  --preserve-graphics-text \
  "${key_flags[@]+"${key_flags[@]}"}" \
  "${extra_args[@]+"${extra_args[@]}"}"

# --- 译后视觉检查（QA 门槛）---
REPORT="${OUTPUT%.pdf}.inspect.json"
echo "== 视觉检查: 报告 ${REPORT}"
if "$PY" -m pdf_zh_translator inspect "$INPUT" "$OUTPUT" --json-out "$REPORT"; then
  echo "== 完成: ${OUTPUT}（视觉检查通过，报告 ${REPORT}）"
else
  if [ "$dry_run" -eq 1 ]; then
    echo "== dry-run 完成: ${OUTPUT}（占位译文的检查结果仅供排版参考，见 ${REPORT}）"
  else
    echo "== 未通过: 视觉检查发现 error 级问题，详见 ${REPORT}；请勿宣称翻译完成" >&2
    exit 1
  fi
fi
