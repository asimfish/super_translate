# 从已有翻译缓存重新排版

升级引擎后，可以使用已有译文重新生成 PDF，不必再次请求模型。此功能需要原始 PDF 和与之匹配的 `translation-cache.jsonl`，不能代替首次翻译。

## 在网页中使用

1. 打开论文，展开阅读器工具栏的“翻译选项”。
2. 勾选“从缓存离线渲染（不调用模型）”。
3. 已翻译过的论文可以直接点击“翻译”，系统会复制本篇最近的缓存作为新任务的输入。也可以选择本地 JSONL 文件覆盖本次任务的缓存。
4. 进度面板与普通翻译一致；生成后仍会检查译文和排版，只有通过检查才显示完成。原来的 PDF 和译文草稿会保留。

“导出本篇缓存”可以下载最近的缓存供备份或迁移。缓存包含原文和译文，请按论文内容管理访问权限；导出不会包含 API key 或工作进程配置。

离线渲染不需要配置 API key。缓存缺失条目时会失败并报告缺失内容，**不会自动调用供应商，也不会编造译文**。修改了原文、翻译语言或分块方式后，旧缓存可能不完整；此时应补充匹配的缓存，或关闭离线选项重新进行在线翻译。

支持 Web 在线任务生成的样式分段缓存，以及 CLI 导出并填写的整段缓存。上传文件必须是 UTF-8 JSONL，最多 20 MiB / 100,000 条；每条包含 `key`、`source`、`translation` 字符串，key 必须与源文本匹配，数学占位符必须完整保留。不接受服务器文件路径。

## API

以下命令在调用者当前目录读取文件。远程服务需使用自己的登录 token；本机访问是否需要 token 取决于服务器的访问配置。

```bash
# 上传缓存并启动离线任务
curl -X POST \
  -H "Authorization: Bearer $PAPER_CHINA_USER_TOKEN" \
  -F 'cache_file=@paper.translation-cache.jsonl' \
  "http://127.0.0.1:8001/api/papers/$PAPER_ID/translate?cache_only=true"

# 复用本篇最近的缓存，无需上传文件
curl -X POST \
  -H "Authorization: Bearer $PAPER_CHINA_USER_TOKEN" \
  "http://127.0.0.1:8001/api/papers/$PAPER_ID/translate?cache_only=true"

# 下载本篇最近的缓存
curl -H "Authorization: Bearer $PAPER_CHINA_USER_TOKEN" \
  "http://127.0.0.1:8001/api/papers/$PAPER_ID/translation-cache" \
  -o paper.translation-cache.jsonl
```

返回的 `job_id` 与普通翻译任务一致，使用现有状态、任务历史和下载接口查看结果。任务的缓存快照持久保存，服务器恢复队列时仍会使用同一份输入；不同用户不能读取、导出或启动彼此的论文缓存。正在翻译或等待修复的论文仍需先取消当前任务，再发起新任务。
