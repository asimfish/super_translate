# 安装与引擎准备

## 1. 克隆仓库并安装依赖

skill 与翻译引擎在同一个仓库里，克隆一次即可：

```bash
git clone https://github.com/asimfish/super_translate.git
cd super_translate

# 推荐：uv（仓库带 uv.lock，秒级同步）
uv sync

# 备选：标准 venv + pip
python3 -m venv .venv
.venv/bin/pip install -e .
```

验证安装：

```bash
.venv/bin/python -m pdf_zh_translator translate --help
```

## 2. 把 skill 装进 Agent

skill 目录是 `<仓库根>/skills/paper-translate/`。推荐 symlink（仓库更新时 skill 自动跟进）：

```bash
# Claude Code
mkdir -p ~/.claude/skills
ln -s /path/to/super_translate/skills/paper-translate ~/.claude/skills/paper-translate

# Cursor
mkdir -p ~/.cursor/skills
ln -s /path/to/super_translate/skills/paper-translate ~/.cursor/skills/paper-translate

# Codex（可选）
mkdir -p ~/.codex/skills
ln -s /path/to/super_translate/skills/paper-translate ~/.codex/skills/paper-translate
```

不方便 symlink 时也可以整目录复制，但更新仓库后要重新复制。装完重启对应 Agent，然后直接说：

```text
用 paper-translate 把 ~/Downloads/attention.pdf 翻译成中文。
```

## 3. 仓库根定位规则

skill 脚本按以下顺序定位仓库根：

1. 环境变量 `SUPER_TRANSLATE_HOME`（显式指定，最可靠）；
2. 沿脚本真实路径（解析 symlink 后）向上三级，即 `skills/paper-translate/scripts/ → 仓库根`。

symlink 安装下第 2 条自动生效，无需配置。只有把 skill 目录**复制**到别处使用时才必须设置：

```bash
export SUPER_TRANSLATE_HOME=/path/to/super_translate
```

## 4. 配置 API key

任选其一（推荐写进 shell profile 或仓库根 `.env`）：

```bash
export PAPER_CHINA_DEEPSEEK_API_KEY=sk-...   # 与 Web 模式共用，推荐
export DEEPSEEK_API_KEY=sk-...               # CLI 内置回退
export PDF_TRANSLATOR_API_KEY=sk-...         # 通用（配合 --api-mode generic/openai-compatible）
```

没有 key 也可以用 `--dry-run` 验证排版效果，或用 `--api-mode cache-only` 重放已有缓存。

## 5. 自检

```bash
bash ~/.claude/skills/paper-translate/scripts/check_env.sh
```

全部通过后即可开始翻译。
