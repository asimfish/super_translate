# Web UI 截图与 Demo 复现说明

产出日期：2026-08-25（截图与初版 demo）；2026-08-26 高清重录 demo（v2，见文末）。
所有素材在本机离线生成，未使用任何真实 LLM API key。

## 产物清单

| 文件 | 内容 | 规格 |
|---|---|---|
| `library.png` | 论文库：Attention（已完成，含"译文/双语"入口）+ BERT / word2vec（待翻译） | 2880×1800 (1440×900@2x)，134 KB |
| `upload.png` | 上传界面：拖拽区 + 3 篇待上传队列（含文件大小） | 同上，134 KB |
| `reader.png` | 双栏对照阅读器：左原文右译文，第 1 页摘要与作者栏可见 | 同上，256 色量化，477 KB |
| `providers.png` | API 设置弹窗：DeepSeek 已配置（演示 key `sk-demo-xxxx`），其余 4 家未配置 | 同上，242 KB |
| `demo.mp4` | 主产物（主页 `<video>` 用）：库 → 打开 Attention → 双栏同步滚动（引言/架构）→ 公式页停留 → 返回库 | 1600×1000，H.264 CRF 20，12fps，24.4s，4.7 MB |
| `demo.gif` | 兼容产物（README/GitHub 用），同内容紧凑版 | 1280×800，10fps，128 色 bayer 抖动，14.1s，7.1 MB |

录屏原件与母版：`.local-work/webui-v2/`（gitignore）——`frames/` 59 张 2880×1800
无损 PNG 帧 + `mp4.ffconcat` / `gif.ffconcat` 时间轴 + `demo-master-2880.mp4`
（2880×1800 CRF 14 母版，12.8 MB，可重新调参转码而无需重录）。
截图中不含任何真实 API key；providers 界面填写的是假 key `sk-demo-xxxx`。

## 环境与安装（需在交付说明中报告）

- 仓库 `~/Code/super_translate`，分支 main，`.venv`（Python 3.13.11）
- `uv sync --extra dev` 新增安装：**playwright 1.62.0**（及 pytest 9.1.1、pyee 13.0.1 等 dev 依赖）
- `playwright install chromium`：使用本机已有的 Chromium 缓存（Playwright 1.62 配套版本），清理了旧版 chromium-1228

## 启动命令

```bash
cd ~/Code/super_translate
PAPER_CHINA_CREDENTIAL_ENCRYPTION_KEY="$(openssl rand -base64 32 | tr '+/' '-_')" \
  .venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8010
```

- `PAPER_CHINA_CREDENTIAL_ENCRYPTION_KEY` 必须设置，否则「API 设置」保存凭证会 503（`app/core/provider_credentials.py` 用它做 AES-GCM 加密）。
- 首次启动自动建 `data/`（papers/translations/upload_sessions + SQLite）。回环访问免登录（`LOCAL_ACCESS_SCOPE`）。
- 不需要设置任何 `*_API_KEY` 环境变量。

## cache-only 方案（Web 端离线翻译如何走通）

Web 端**没有** CLI 的 `--api-mode cache-only` 等价开关，但翻译栈天然支持全缓存命中：

1. 原理：`app/services/translator.py::_translate_sync_native` 用
   `CachedTranslator(VendorTranslator, cache_path)`，其中
   `cache_path = <output_dir>/<stored_stem>.translation-cache.jsonl`。
   若所有翻译请求都命中缓存，`VendorTranslator`（真实 API）永远不会被调用。
2. 门槛一（凭证校验）：`start_translation` 要求 provider 有凭证。通过「API 设置」
   界面给 DeepSeek 存假 key `sk-demo-xxxx` 即可通过（全命中时该 key 不会被使用）。
3. 门槛二（目录清空）：`start_translation` 开跑时会 `cleanup_output_dir(output_dir)`
   清空输出目录，**无法提前预放缓存**。利用它写完 `.worker_spec.json` 到 worker
   子进程加载缓存之间约 1.5s 的窗口，用 watcher（`/tmp/st-webui-work/watcher.py`，
   50ms 轮询）在 spec 出现后立刻把合并缓存复制为
   `data/translations/<paper_id>/<stored_stem>.translation-cache.jsonl`。
4. 缓存构造（`/tmp/st-webui-work/attention-cache-merged.jsonl`，207 条）：
   - r3 缓存（/tmp）42/115 命中 + r2 缓存（Desktop classic20_final_r2）99/115 命中，
     合并 101/115；两者分块粒度与当前 main 的提取逻辑均有差异。
   - 剩余 14 块人工补译（作者栏、Encoder/Decoder 段、Multi-Head Attention、
     Residual Dropout、Label Smoothing、WMT En-Fr 结果段、致谢等），
     占位符 ⟦n⟧ multiset 与原文严格一致（`placeholders_preserved` 校验）。
   - 用「探针 vendor」脚本（`/tmp/st-webui-work/probe_web_path.py`）精确复刻 Web
     翻译栈（含 style-segment 拆分路径）验证：**vendor 零调用、无 still-english
     重试**，QA `verify_translation_issues` 仅 7 条 warning（无 error），
     可安全走完 completed 状态。
5. 全流程（上传 → 点翻译 → 已完成）实测 75 秒，其中含译后 QA 检查。

CLI 备用路径（未采用，但已验证可行）：

```bash
.venv/bin/python -m pdf_zh_translator translate attention.pdf out.pdf \
  --api-mode cache-only --cache-file merged.jsonl --preserve-graphics-text
```

注意要用 `python -m pdf_zh_translator`（不是 `pdf_zh_translator.cli`，后者无
main guard 会静默退出）。

### v2 重建速记（2026-08-26，/tmp 被清空后）

- 合并缓存无需重新人工补译：上次 watcher 投放进任务目录的成品缓存仍在
  `data/translations/40a9518c9905/<stem>.translation-cache.jsonl`（207 条，
  占位符全部合法），直接回收存档为
  `.local-work/webui-v2/attention-cache-merged.jsonl`，探针复验仍 **全命中、
  vendor 零调用**。若 data/ 也丢，才需按上文方法用 r2 缓存（99/115）+ 人工补译
  16 块重建。
- **watcher v2**（`.local-work/webui-v2/watcher.py`）：去掉了初版按 spec 路径
  去重的 seen 集合——重新翻译时 `cleanup_output_dir` 后 spec 路径不变，初版会
  跳过补投导致缓存丢失；v2 每轮轮询只要目标缓存文件不存在就补投。
- 凭证加密 key 现持久化在 `.local-work/webui-v2/cred-key.txt`，启动命令改为
  `PAPER_CHINA_CREDENTIAL_ENCRYPTION_KEY="$(cat .local-work/webui-v2/cred-key.txt)"`。
  若换了加密 key，DB 里旧凭证会解密失败（翻译接口 400），需先
  `DELETE /api/provider-credentials/deepseek` 再重新保存假 key。
- v2 重跑「重新翻译」全流程 45s 完成，缓存命中率 100%。

## 截图/录屏脚本位置

- 截图脚本（2026-08-25 初版，/tmp 已被系统清空，方法见本文档即可复现）：
  API 设置弹窗、上传队列、reader、library 四张图均为 Playwright chromium
  headless，viewport 1440×900，deviceScaleFactor=2 直接 `page.screenshot`。
- demo 录制脚本（v2，现存）：`.local-work/webui-v2/record_frames.py`
- reader.png 压缩：PIL `quantize(colors=256, method=MEDIANCUT)`（886→477 KB）

## demo 录制方法 v2（高清，2026-08-26）

初版直接用 Playwright `record_video` 输出 1280×800，被反馈模糊。根因有二：
(a) **Playwright 录屏只吐 CSS 逻辑像素帧**——`deviceScaleFactor=2` 对录屏无效，
`record_video_size` 设 2880×1800 只会把 1440×900 的帧 letterbox 进大画布（实测
左上角 1/4 有内容，其余灰边）；(b) GIF 为压体积用了 960px/48 色。

v2 改为**全分辨率截图序列 + ffmpeg concat 时间轴**（`record_frames.py`）：

1. viewport 1440×900、DSF=2，先把滚动路径预滚一遍让 pdf.js 渲染并缓存所有页
   canvas（防懒加载空白帧），再回到顶部正式录制；
2. 滚动段按 smoothstep 缓动逐步设置 `scrollTop`，每步 `page.screenshot` 一张
   2880×1800 无损 PNG（12fps 时间轴）；停留段只截 1 帧，在 ffconcat 清单里用
   `duration` 拉长——59 张帧覆盖 21.6s（mp4）/12.9s（gif）两条时间轴；
3. 转码：
   - mp4：`ffmpeg -f concat -i mp4.ffconcat -vf "fps=12,scale=1600:-2:flags=lanczos,format=yuv420p" -c:v libx264 -crf 20 -preset slow -movflags +faststart demo.mp4`
   - gif：`ffmpeg -f concat -i gif.ffconcat -vf "fps=10,scale=1280:-1:flags=lanczos,split[s0][s1];[s0]palettegen=max_colors=128[p];[s1][p]paletteuse=dither=bayer:bayer_scale=5" -loop 0 demo.gif`
   - 母版：同 concat 源不缩放 CRF 14 → `demo-master-2880.mp4`（重新调参不用重录）

注意 pdf.js 渲染上限 `MAX_CANVAS_DPR = 1.5`（app.js），PDF 页 canvas 在 DSF=2
下实际按 1.5x 位图渲染，是当前清晰度上限（对 1600px 输出足够）。

## 发现的 UI / 引擎问题清单（建议开 issue）

> 2026-09-03 处置结果：1 → [#3](https://github.com/asimfish/super_translate/issues/3)；2 → [#4](https://github.com/asimfish/super_translate/issues/4)；
> 3、4、6、8 已直接修复（标题 NFKC 归一化、`python -m pdf_zh_translator.cli` 打印用法、卡片标题 `title` 提示、
> 时间戳 API 端带 `+00:00` + 前端按 UTC 解析）；5 → [#5](https://github.com/asimfish/super_translate/issues/5)；
> 7 属数据侧观察，术语库现已把人名/模型名/机构名列为不译项，无需代码改动。

1. **QA 对公式密集块误报「未翻译英文」并触发缓存失效**：word2vec 中
   `X=vector("biggest")−vector("big")+vector("small")` 这类占位符恢复后的公式段
   （>35 西文字符、无 CJK）会被 `_contains_untranslated_english_run` 判为未翻译，
   `_is_reference_or_formula_text` 未豁免。Web 端后果比 CLI 重：retry 前会
   `CachedTranslator.invalidate()` 剔除缓存条目并强制重打 API——离线/缓存场景
   直接失败，在线场景多花一次 API 调用且结果通常不变（该块本来就该保留公式）。
2. **Web 端无 cache-only / 离线渲染入口**：`start_translation` 固定清空
   output_dir，预放缓存无官方路径（本次靠时序窗口 watcher 绕过）。建议加
   「从缓存渲染」高级选项或允许指定已有缓存文件。
3. **PDF 标题提取不归一化排版连字**：word2vec 卡片标题显示 `Efﬁcient`（U+FB01
   连字）。`extract_title_from_pdf` 应做 NFKC 归一化。
4. **`python -m pdf_zh_translator.cli` 静默退出**：`cli.py` 无 `__main__` guard，
   直接跑该模块 exit 0 无任何输出，易误导（正确入口是 `python -m pdf_zh_translator`）。
5. **保存凭证后自动刷新模型列表**：假/无效 key 下弹红色错误 toast，随后回退
   「无法更新模型列表，已使用内置列表」。回退行为合理，但保存成功 + 立即报错的
   组合对新用户略困惑，可考虑把刷新失败降级为行内提示。
6. **论文库卡片标题截断无省略提示**：长标题（如 BERT 全名）直接换行截断，
   无 tooltip/title 属性，鼠标悬停看不到全名。
7. **译文页作者姓名音译不一致**（数据侧观察）：r2 时代缓存把 Llion Jones 译为
   「利昂·琼斯」，而术语惯例通常保留原文人名。非本次代码问题，但展示时可见。
8. **卡片时间戳显示 UTC 而非本地时区**（2026-08-26 重录时发现）：翻译于本地
   01:36（UTC+8）完成，卡片显示「翻译完成 8/25 17:37」；上传时间同样偏差 8 小时
   （8/25 11:44 实为本地 19:44）。前端渲染时间前应做时区转换。

## 数据落位说明

- 应用数据（上传的 3 篇 PDF、SQLite、译文产物）在仓库 `data/`（已 gitignore），
  由应用自身代码路径写入；翻译缓存文件由 watcher 写入
  `data/translations/40a9518c9905/`（attention 的任务目录）。
- 本次未修改任何 `app/` 代码；未执行任何 git 操作。
