# Provider Model Catalog

Last verified: 2026-08-18

The API settings UI shows a curated offline catalog first. When a user has
stored a provider key, the server also queries that provider's authenticated
`/models` endpoint and caches the account-specific result for six hours.
Specialized, retired, and non-text models are filtered out.

## Official Sources

- DeepSeek: https://api-docs.deepseek.com/quick_start/pricing/
- Kimi: https://platform.kimi.com/docs/models
- OpenAI: https://developers.openai.com/api/docs/models
- Anthropic: https://platform.claude.com/docs/en/about-claude/models/overview
- GLM: https://docs.bigmodel.cn/api-reference/%E6%A8%A1%E5%9E%8B-api/%E5%AF%B9%E8%AF%9D%E8%A1%A5%E5%85%A8

## Maintenance Rules

1. Add only models accepted by the provider's text Chat Completions or Messages
   endpoint used by this application.
2. Do not add deprecated aliases, coding-only, audio, image, embedding, search,
   realtime, or invitation-only models to the offline catalog.
3. Add request-payload regression coverage when a model family changes sampling,
   reasoning, or output-token parameters.
4. Preserve authenticated discovery so account entitlements can augment the
   curated catalog without exposing provider keys.
