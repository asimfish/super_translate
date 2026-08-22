# Provider Model Catalog

Last verified: 2026-08-23

The API settings UI shows a curated offline catalog first. When a user has
stored a provider key, the server also queries that provider's authenticated
`/models` endpoint and caches the account-specific result for six hours.
Anthropic pagination is followed within a single bounded timeout and response
budget. Retired and non-text models are filtered out; compatible specialized
models are labeled separately rather than presented as translation defaults.

The API keeps the original `models: string[]` field for existing clients and
adds model guidance metadata. The UI groups choices as latest, quality,
balanced, economy, specialized, legacy, or account-only, and shows both the
official catalog verification date and the last account refresh time.

## Official Sources

- DeepSeek: https://api-docs.deepseek.com/quick_start/pricing/
- Kimi: https://platform.kimi.com/docs/models
- OpenAI: https://developers.openai.com/api/docs/models
- Anthropic: https://platform.claude.com/docs/en/about-claude/models/overview
- GLM: https://docs.bigmodel.cn/api-reference/%E6%A8%A1%E5%9E%8B-api/%E5%AF%B9%E8%AF%9D%E8%A1%A5%E5%85%A8

## Maintenance Rules

1. Add only models accepted by the provider's text Chat Completions or Messages
   endpoint used by this application.
2. Do not add deprecated aliases, audio, image, embedding, search, realtime, or
   invitation-only models to the offline catalog. Label compatible coding-only
   models as specialized and do not make them translation defaults.
3. Add request-payload regression coverage when a model family changes sampling,
   reasoning, or output-token parameters.
4. Preserve authenticated discovery so account entitlements can augment the
   curated catalog without exposing provider keys.
5. Keep pagination, response bytes, redirects, and total refresh time bounded;
   provider credentials stay in headers and must never appear in URLs or logs.
