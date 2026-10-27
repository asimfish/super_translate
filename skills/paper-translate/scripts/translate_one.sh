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
REPO_ROOT=$(python3 -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$REPO_ROOT")
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

# 在切换目录前固定调用方的输入/输出位置。
INPUT=$("$PY" -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$INPUT")
OUTPUT=$("$PY" -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$OUTPUT")

# 按实际协议选择凭证；兼容接口不得自动继承 DeepSeek 密钥。
extra_args=()
path_next=0
for arg in "$@"; do
  if [ "$path_next" -eq 1 ]; then
    arg=$("$PY" -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "$arg")
    path_next=0
  else
    case "$arg" in
      --cache-file|--font-file) path_next=1 ;;
      --cache-file=*|--font-file=*)
        flag="${arg%%=*}"
        value=$("$PY" -c 'import os,sys; print(os.path.abspath(sys.argv[1]))' "${arg#*=}")
        arg="$flag=$value" ;;
    esac
  fi
  extra_args+=("$arg")
done
mode=deepseek
key_env=""
api_url="${DEEPSEEK_API_URL:-}"
key_supplied=0
dry_run=0
next=""
for arg in "${extra_args[@]+"${extra_args[@]}"}"; do
  if [ "$next" = mode ]; then mode="$arg"; next=""; continue; fi
  if [ "$next" = key_env ]; then key_env="$arg"; next=""; continue; fi
  if [ "$next" = api_url ]; then api_url="$arg"; next=""; continue; fi
  case "$arg" in
    --api-mode) next=mode ;;
    --api-mode=*) mode="${arg#*=}" ;;
    --api-url) next=api_url ;;
    --api-url=*) api_url="${arg#*=}" ;;
    --api-key-env) next=key_env ;;
    --api-key-env=*) key_env="${arg#*=}" ;;
    --api-key|--api-key=*) key_supplied=1 ;;
    --dry-run) dry_run=1 ;;
  esac
done
key_flags=()
if [ "$dry_run" -eq 0 ] && [ "$mode" != cache-only ] && [ "$key_supplied" -eq 0 ]; then
  if [ -n "$key_env" ]; then
    if ! [[ "$key_env" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || [ -z "${!key_env:-}" ]; then
      echo "错误: --api-key-env 指定的环境变量未设置或名称无效" >&2
      exit 1
    fi
  elif [ "$mode" = deepseek ]; then
    case "$api_url" in
      ""|https://api.deepseek.com|https://api.deepseek.com/|https://api.deepseek.com/v1|https://api.deepseek.com/v1/) ;;
      *) echo "错误: 自定义接口地址请显式使用 --api-key-env YOUR_PROVIDER_KEY" >&2; exit 1 ;;
    esac
    if [ -n "${PAPER_CHINA_DEEPSEEK_API_KEY:-}" ]; then
      key_flags=(--api-key-env PAPER_CHINA_DEEPSEEK_API_KEY)
    elif [ -n "${DEEPSEEK_API_KEY:-}" ]; then
      key_flags=(--api-key-env DEEPSEEK_API_KEY)
    elif [ -z "${PDF_TRANSLATOR_API_KEY:-}" ]; then
      echo "错误: 请导出 DeepSeek API key 环境变量，或使用 --dry-run" >&2
      exit 1
    fi
  elif [ -n "${PDF_TRANSLATOR_API_KEY:-}" ]; then
    key_flags=(--api-key-env PDF_TRANSLATOR_API_KEY)
  else
    echo "错误: 自定义供应商请显式使用 --api-key-env YOUR_PROVIDER_KEY" >&2
    exit 1
  fi
fi

# 注意：字符串里变量一律写 ${VAR}——macOS 自带 bash 3.2 在 $VAR 后紧跟全角字符时会解析出错
echo "== 翻译: ${INPUT} -> ${OUTPUT}"
cd "$REPO_ROOT"
export SUPER_TRANSLATE_HOME="$REPO_ROOT"
"$PY" -m pdf_zh_translator translate "$INPUT" "$OUTPUT" \
  --preserve-graphics-text \
  "${key_flags[@]+"${key_flags[@]}"}" \
  "${extra_args[@]+"${extra_args[@]}"}"

# 完整门禁复用基准的 error / actionable warning / visual score 策略。
REPORT="${OUTPUT%.pdf}.strict.json"
echo "== 严格检查: 报告 ${REPORT}"
if "$PY" "$REPO_ROOT/skills/paper-translate/scripts/verify_one.py" "$INPUT" "$OUTPUT" "$REPORT"; then
  if [ "$dry_run" -eq 1 ]; then
    echo "== dry-run 排版检查通过: ${OUTPUT}（占位译文，不代表真实翻译验收）"
  else
    echo "== 完成: ${OUTPUT}（严格检查通过，报告 ${REPORT}）"
  fi
else
  if [ "$dry_run" -eq 1 ]; then
    echo "== dry-run 已生成: ${OUTPUT}（检查未通过，仅供排版排查，见 ${REPORT}）"
  else
    echo "== 未通过: 仍有 error、需处理的 warning 或视觉分数未达标，详见 ${REPORT}" >&2
    exit 1
  fi
fi
