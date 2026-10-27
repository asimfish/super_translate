# 模型目录与供应商接入

八家供应商：DeepSeek、Kimi、OpenAI、Anthropic、智谱 GLM、Google Gemini、通义千问 Qwen、豆包 Doubao。

在 API 设置中保存自己的密钥，搜索并选择模型后保存。新发布模型和豆包 `ep-...` 接入点可填入“自定义模型 / Endpoint ID”。更新目录不会自动更换已保存模型，也不会覆盖正在编辑的模型和密钥。

设置页可见时每分钟检查目录，后端成功结果缓存 5 分钟；“更新”立即请求官方接口。密钥变更和账号切换使用独立缓存。失败时保留上次成功结果，60 秒后允许重试；没有成功结果则显示内置参考。内置目录核对日期为 2026-10-10。

“账号接口列出”表示查询接口返回了该模型，并不保证账号已开通调用、具备余额或配额。“内置参考”不表示账号可用。模型接口不存在、无查询权限、网络故障和供应商限流会显示提示；豆包可直接使用控制台接入点 ID。当前适配文本翻译所需的 Chat Completions / Anthropic Messages；音频、图像生成、Embedding 和已知仅支持 Responses 的模型不进入自动列表。

| 供应商 | 翻译接口 | 目录更新 |
| --- | --- | --- |
| DeepSeek | `api.deepseek.com/chat/completions` | `/models` |
| Kimi | `api.moonshot.cn/v1/chat/completions` | `/v1/models` |
| OpenAI | `api.openai.com/v1/chat/completions` | `/v1/models` |
| Anthropic | `api.anthropic.com/v1/messages` | `/v1/models`，游标分页 |
| 智谱 | `open.bigmodel.cn/api/paas/v4/chat/completions` | `/models` 查询失败时显示参考目录 |
| Gemini | `generativelanguage.googleapis.com/v1beta/openai/chat/completions` | 原生 `/v1beta/models`，分页与 `generateContent` 能力过滤 |
| Qwen | `dashscope.aliyuncs.com/compatible-mode/v1/chat/completions` | 原生 `/api/v1/models`，文本生成筛选与页码分页 |
| 豆包 | `ark.cn-beijing.volces.com/api/v3/chat/completions` | 尝试 `/models`；接口未开放时使用参考目录与自定义 ID |

Qwen、豆包当前接入中国北京区域，密钥需匹配区域。供应商地址由服务器固定，页面不能传入任意 URL；模型发现禁止重定向，响应、分页、总超时和缓存规模均有限制。管理员的自定义 OpenAI/Kimi 代理不会被拿来向其他域名查询目录。没有密钥时不会发出账号目录查询。

接口：`GET /api/provider-credentials/models` 与 `POST /api/provider-credentials/models/{provider}/refresh`。仅返回模型、来源、刷新时间与提示，不返回密钥。`model_options[].availability` 区分 `listed`、`previously_listed`、`reference`、`custom`。

官方核对来源：

- [DeepSeek 当前接口与模型](https://api-docs.deepseek.com/)、[更新日志](https://api-docs.deepseek.com/updates/)
- [Kimi 官方平台](https://platform.kimi.com/)
- [OpenAI 模型目录](https://developers.openai.com/api/docs/models)、[模型列表接口](https://developers.openai.com/api/reference/resources/models/methods/list)
- [Anthropic 模型目录](https://platform.claude.com/docs/en/models/overview)、[模型列表接口](https://platform.claude.com/docs/en/api/models/list)
- [智谱模型概览](https://docs.bigmodel.cn/cn/guide/start/model-overview)
- [Gemini 模型目录](https://ai.google.dev/gemini-api/docs/models)、[模型查询接口](https://ai.google.dev/api/models)、[OpenAI 兼容接口](https://ai.google.dev/gemini-api/docs/openai)
- [Qwen 查询模型](https://help.aliyun.com/zh/model-studio/list-models)、[模型接口说明](https://help.aliyun.com/zh/model-studio/qwen-api-via-dashscope)
- [豆包 Seed 2.1 Pro 官方说明](https://docs.volcengine.com/docs/ark/seed-2-1-pro?lang=zh)、[上下文与输出参数](https://docs.volcengine.com/docs/ark/context-management?lang=zh)

验证命令：

```bash
.venv/bin/python -m pytest -q tests/test_provider_model_catalog.py tests/test_provider_credentials.py tests/test_translators.py tests/test_translator.py tests/test_progress_and_ui.py
PYTHONPATH=. .venv/bin/python desktop/macos/Tests/provider_settings_journey.py --artifacts /tmp/provider-settings-check
```

测试包含八家真实 HTTP 协议交互的本地供应商模拟服务，验证认证头、请求参数和中文响应解析；这不等同于八家付费账号均已真实调用。真实供应商验证范围以验收报告为准。
