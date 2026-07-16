# Silkworm

**站点文档抓取工具 — Feed it pages, get back silk.**

Silkworm transforms documentation websites into clean, structured Markdown. Named after the silkworm lifecycle — **Feed** → **Cocoon** → **Spin** → **Silk** — it automates the entire pipeline from page discovery to polished output.

## Quick Start

```bash
# Install
uv tool install silkworm

# Scrape a documentation site
silkworm feed https://docs.example.com
```

Output lands in `silk/` as organized Markdown files with YAML frontmatter.

## Pipeline

```
┌──────────┐    ┌──────────┐    ┌──────────┐    ┌──────────┐
│  🍃 Feed  │───→│  🏠 Cocoon│───→│  🧵 Spin  │───→│ ✨ Silk  │
│ discovery │    │ raw HTML │    │  clean   │    │ Markdown │
└──────────┘    └──────────┘    └──────────┘    └──────────┘
```

### 🍃 Feed — Discovery & Fetching

- **Navigation analysis**: LLM-powered extraction of nav trees from documentation sites
- **Sitemap fallback**: Graceful degradation to sitemap-based URL discovery
- **Concurrent fetching**: Async HTTPX-based page retrieval with configurable concurrency
- **Content hashing**: SHA256 deduplication to avoid re-processing identical pages

### 🏠 Cocoon — Raw HTML Storage

Raw HTML is stored locally for offline processing and re-processing without network access.

```
cocoon/
├── docs.example.com/
│   ├── _manifest.json    # 采集清单
│   ├── getting-started.html
│   └── ...
└── _manifest.json
```

### 🧵 Spin — Cleaning Pipeline

16 composable filters transform raw HTML into clean content:

| Filter | Purpose |
|--------|---------|
| `main_content` | Extract main content area (CSS selector / trafilatura) |
| `nav_remnants` | Remove leftover navigation elements |
| `boilerplate` | Strip known boilerplate phrases |
| `scripts` | Remove script, style, iframe, SVG elements |
| `tab_content` | Flatten Bootstrap/Fern tab panels |
| `line_numbers` | Strip Fern line-number gutters |
| `code_lang_labels` | Remove Fern code-block language labels |
| `protect_code` | Protect code blocks before Markdown conversion |
| `flatten_nested_tables` | Collapse nested tables |
| `html_to_md` | HTML → Markdown via markdownify |
| `restore_code` | Restore protected code blocks |
| `overrides` | Domain-specific heuristic fixes (emoji, feedback, On this page) |
| `unescape_underscores` | Fix `\_` → `_` inside code blocks |

### ✨ Silk — Markdown Output

Output mirrors the original site's URL structure:

```
silk/
└── docs.example.com/
    └── getting-started.md   # with YAML frontmatter
```

## Supported Frameworks

| Framework | Auto-detection |
|-----------|---------------|
| Kubernetes | ✅ Fingerprint |
| MkDocs Material | ✅ Fingerprint |
| MkDocs | ✅ Fingerprint |
| Docusaurus | ✅ Fingerprint |
| Sphinx | ✅ Fingerprint |
| Generic | ✅ Fallback |

## CLI

```bash
# Full pipeline: discover → fetch → clean → output
silkworm feed <url> [--prompt "filter text"] [--exclude "section"]

# Re-process cached HTML
silkworm spin <cocoon-dir>

# Manage raw HTML cache
silkworm cocoon list
silkworm cocoon status
silkworm cocoon clean
```

## Configuration

Create `silkworm.yaml` in the project root:

```yaml
global:
  cocoon_dir: cocoon
  silk_dir: silk
  max_retries: 2
  quality_min_length: 500
  quality_score_threshold: 0.6
  llm:
    base_url: http://localhost:8000/v1
    api_key: YOUR_API_KEY_HERE
    model: gpt-4
    temperature: 0.1
    timeout: 600.0
```

## Quality Assurance

Silkworm includes a multi-stage quality system:

1. **Rule-based checks** — length, code block integrity, heading jumps, noise phrases, link density
2. **LLM review** — configurable gray-zone scoring with LLM override
3. **Automatic retry** — adaptive strategy (widen selector, trafilatura, add boilerplate, skip filter)

## Development

```bash
# Setup
uv sync --dev

# Run tests
uv run pytest

# With coverage
uv run pytest --cov=silkworm --cov-report=html
```

## License

MIT
