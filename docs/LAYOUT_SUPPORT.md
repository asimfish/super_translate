# 期刊、会议与书籍版式支持

版式采用源 PDF 的实际栏宽、字体、对象位置和跨页结构。注册表中的名称用于选择策略，
识别出一个出版社不代表该出版社的所有版式都经过翻译验收。

## 实现与扩展

- `pdf_zh_translator/layout_families.json` 是随程序分发、带版本号和校验规则的注册表。
  可增加出版社/会议页眉页脚特征、策略和样本；PDF 中的文字不能加载插件或执行规则。
- 跨页取样包含前 16 页和分布于全篇的 4 个位置。正文宽度参与栏数判断，封面、缩进代码、
  图表单元格不能仅靠左边距决定整份文档的栏数。每页仍按自己的实际几何排版。
- 策略接入提取、结构分类和最终译文排版。图注仅能借用没有邻近文字或图形的空白。
  现有 `min_caption_font_size` 是学习文件的兼容字段，实际字体下限仍由翻译参数控制。
- 书籍保留章节字号和代码源对象，目录逐行排版，独立页码列保持原坐标，条目粗细取自
  原字体。扉页标题可以利用正文框范围内经检查空白的宽度。
- 书籍正文的 input/output 自然语言释义参与翻译；等宽字体代码和明确标记的算法保留。
- 会议摘要前的多排作者/邮箱按源对象保留，不因署名位置较低而混入正文。
- Elsevier 单/双栏文章分开处理标题、作者联系信息、科学脚注和 LaTeX 示例。遇到
  嵌入字体清除原文时误删邻近元数据的情况，恢复源字形外观，并保留准确可复制的文本。
- Nature 的署名、DOI、附录表单和比较矩阵继续使用其专项适配器。
- STIX 文本字体与数学字体分别判断。`STIXGeneral` 和 `STIXTwoText` 的英文正文不会被
  整段当作公式冻结；真正数学字体、希腊字母/运算符和上标仍有保护与回归测试。

未知身份保持通用源几何策略，并在翻译报告中提示未验证。扫描件需要 OCR；竖排、RTL、
特殊印刷字体、复杂跨页大表和完整长书不因一个短样本通过而被宣称全面验收。

## 支持目录的证据层级

| 层级 | 含义 |
|---|---|
| 已注册 | 有身份特征或几何回退策略，尚不等于真实翻译通过 |
| 真实源识别通过 | 固定来源、校验值和选页，对身份、栏数和原文页面做检查 |
| 真实模型生成 | 使用配置的供应商，记录输入、输出及引擎校验值 |
| 自动质检通过 | 页数、可选择中文、漏翻、图片、碰撞等检查通过；书籍另查代码和目录页码 |
| 视觉复核 | 查看所测页面；剩余风险和未测范围仍记录在报告中 |

注册范围包括 Nature、Elsevier、Springer、Wiley、ACS、RSC、PLOS、Frontiers、Science、
Cell；ACM、IEEE、ACL、NeurIPS、ICML、ICLR、LNCS；学术书籍及通用单/双栏回退。
这些是路由规则范围，不能当成所有品牌的排版质量认证。实际证据见
[本次验收记录](CLIENTS_RELEASE_20261027.md)。

## 增加一个版式或复现缺陷

1. 获取有明确来源/使用许可的原 PDF，记录版本、SHA-256、页码和布局特征。
2. 在 `benchmarks/layout-families/manifest.json` 增加样本及独立预期。PDF 保存在私有
   样本目录，仓库只存来源与校验值；不要把用户论文或整本书直接提交。
3. 先用失败页面证明缺陷。能够修复共性问题时修正共性规则，避免依赖论文标题、固定 DOI
   后缀或某一页的硬编码坐标。
4. 调整 JSON 策略；只有确实需要新结构处理时才增加适配器。现有学习命令生成的 JSON 是
   分析候选，需审阅、加入注册表并验收后生效，不会自动覆盖运行策略。
5. 跑聚焦测试、真实模型翻译、自动质检与视觉复核。更新支持层级，保留失败和警告。

```bash
# 准备原文选页，核对来源 SHA；已有文件不被静默覆盖
.venv/bin/python scripts/check_layout_families.py prepare \
  --source-root /path/to/private/sources --input-root /path/to/inputs \
  --report /path/to/prepare.json

# 识别检查；未生成译文的样本只记为 detection_only
.venv/bin/python scripts/check_layout_families.py check \
  --source-root /path/to/private/sources --input-root /path/to/inputs \
  --report /path/to/detection.json

# 指定样本必须有译文并通过质检，缺文件、换原文、篡改页码/代码都会失败
.venv/bin/python scripts/check_layout_families.py check \
  --ids book acl attention resnet elsevier_sc elsevier_dc \
  --source-root /path/to/private/sources --input-root /path/to/inputs \
  --translated-root /path/to/outputs --require-render --report /path/to/render-qa.json
```

准备与检查还会对原 PDF 和选页 PDF 逐页渲染的指纹做独立比较，不能只凭准备文件的
收据通过。对书籍的代码、目录页码，直接比较原位置上的可复制字符。

模板来源：[ACL 官方样式](https://github.com/acl-org/acl-style-files)、
[Elsevier CAS 官方单/双栏类](https://ctan.org/tex-archive/macros/latex/contrib/els-cas-templates)、
[Think Python 作者版本](https://greenteapress.com/wp/think-python-2e/)。
字体依据：[STIX 项目](https://github.com/stipub/stixfonts)同时提供文本字体和数学字体。
