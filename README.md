# PDF Chinese Translator

Translate an English PDF into a Chinese PDF while keeping the original page size,
images, vector graphics, and broad layout structure.

The tool reads text blocks and their bounding boxes from the PDF, sends those
blocks to your translation supplier API, removes the original text in-place, and
writes Chinese text back into the same regions.

## What It Preserves

- Original page dimensions
- Embedded images
- Most vector graphics and page structure
- Text block positions
- Approximate font size and text color

## Current Limits

- Scanned PDFs need OCR first; this tool handles PDFs with extractable text.
- The tool samples the local background color behind each text block before
  redrawing. It works best for papers and reports with relatively uniform
  backgrounds behind text.
- Dense tables, equations, captions, and multi-layer graphics may need manual QA.
- Perfect PDF reflow is not guaranteed because English and Chinese text have
  different lengths and line-breaking behavior.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

## Quick Dry Run

This verifies PDF rewriting without calling a supplier API:

```bash
pdf-zh-translator translate input.pdf output.zh.pdf --dry-run
```

## DeepSeek v4 Pro

DeepSeek is the default provider. Set your key through an environment variable;
do not put it in source code or commit it to the repository.

```bash
export DEEPSEEK_API_KEY="YOUR_DEEPSEEK_API_KEY"

pdf-zh-translator translate input.pdf output.zh.pdf
```

The default DeepSeek settings are:

- API base URL: `https://api.deepseek.com`
- Chat endpoint: `/chat/completions`
- Model: `deepseek-v4-pro`
- Thinking mode: disabled, because translation should return only deterministic JSON

To override explicitly:

```bash
pdf-zh-translator translate input.pdf output.zh.pdf \
  --api-mode deepseek \
  --api-url "https://api.deepseek.com" \
  --model deepseek-v4-pro
```

## Supplier API Modes

### Generic Batch API

Use this when your supplier accepts a JSON batch of strings.

```bash
export PDF_TRANSLATOR_API_KEY="YOUR_KEY"

pdf-zh-translator translate input.pdf output.zh.pdf \
  --api-mode generic \
  --api-url "https://supplier.example.com/translate" \
  --source-lang en \
  --target-lang zh
```

The default request body is:

```json
{
  "source_lang": "en",
  "target_lang": "zh",
  "texts": ["text block 1", "text block 2"]
}
```

Accepted response shapes include:

```json
{"translations": ["译文 1", "译文 2"]}
```

```json
{"data": {"translations": [{"text": "译文 1"}, {"text": "译文 2"}]}}
```

If your supplier uses a different JSON schema, edit
`pdf_zh_translator/translators.py` in `VendorTranslator._translate_generic()`
and `parse_translation_list()`.

### OpenAI-Compatible Chat API

Use this when your supplier exposes `/v1/chat/completions`.

```bash
export PDF_TRANSLATOR_API_KEY="YOUR_KEY"

pdf-zh-translator translate input.pdf output.zh.pdf \
  --api-mode openai-compatible \
  --api-url "https://supplier.example.com/v1" \
  --model "your-model-name"
```

If `--api-url` ends with `/v1`, the tool appends `/chat/completions`.

## Auth Options

By default the tool sends:

```text
Authorization: Bearer YOUR_KEY
```

For a custom header:

```bash
pdf-zh-translator translate input.pdf output.zh.pdf \
  --api-url "https://supplier.example.com/translate" \
  --auth-header "X-API-Key" \
  --auth-scheme "" \
  --api-key-env SUPPLIER_API_KEY
```

## Chinese Font

The default PDF font alias is `china-s`. If glyphs do not render correctly on
your machine, provide a local Chinese TTF/OTF font:

```bash
pdf-zh-translator translate input.pdf output.zh.pdf \
  --api-url "https://supplier.example.com/translate" \
  --font-name zhfont \
  --font-file /path/to/chinese-font.ttf
```

## Useful Layout Knobs

- `--batch-size`: fewer blocks per API call if the supplier has small context limits.
- `--font-scale`: reduce inserted Chinese font size relative to original text.
- `--min-font-size`: lower bound used when text does not fit its original box.
- `--margin`: padding used when clearing and rewriting text regions.

Example for a dense academic paper:

```bash
pdf-zh-translator translate paper.pdf paper.zh.pdf \
  --api-mode openai-compatible \
  --api-url "https://supplier.example.com/v1" \
  --model "your-model-name" \
  --batch-size 4 \
  --font-scale 0.82 \
  --min-font-size 4.5
```

## Development

Run unit tests that do not call external services:

```bash
python3 -m unittest discover -s tests
```

Run syntax checks:

```bash
python3 -m compileall pdf_zh_translator tests
```
