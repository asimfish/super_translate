## 变更说明

<!-- 一两句话说明这个 PR 做了什么、为什么 -->

关联 issue：<!-- #123，没有可删除 -->

## 变更类型

- [ ] Bug 修复（fix）
- [ ] 新功能（feat）
- [ ] 测试 / 基准（test）
- [ ] 文档（docs）
- [ ] 重构 / 性能 / 其他

## 测试证据

<!-- 粘贴关键输出，QA 类改动请附 inspect/QA 报告要点 -->

- [ ] `uv run ruff check app pdf_zh_translator tests scripts` 通过
- [ ] `bash scripts/smoke_test.sh` 通过
- [ ] 相关单元测试通过（`uv run pytest tests/test_xxx.py -q`），或说明为什么不适用
- [ ] 涉及版式/翻译行为的改动：附至少一篇真实论文的前后对比或 QA 报告

## 检查清单

- [ ] 没有提交 `data/`、`*.db`、翻译产物 PDF、API key 或个人配置
- [ ] 涉及环境变量的改动已同步 `.env.example`
- [ ] 涉及 CLI 接口的改动已同步 `skills/paper-translate/references/cli.md`
- [ ] 用户可见的变更已补充到 `CHANGELOG.md` 的 Unreleased
