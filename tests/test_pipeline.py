from silkworm.models import PageRecord
from silkworm.pipeline import PipelineOrchestrator


_KUBE_HTML = """<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"></head>
<body>
<nav class="navbar">Skip nav</nav>
<main data-pagefind-body>
  <h1>Kubernetes Concepts</h1>
  <div class="td-content">
    <p>This is a paragraph about Kubernetes architecture and concepts.</p>
    <h2>Architecture Overview</h2>
    <p>Kubernetes has a master-slave architecture with etcd for storage.</p>
    <pre><code class="language-bash">kubectl get pods</code></pre>
    <h2>Key Components</h2>
    <ul>
      <li>API Server</li>
      <li>Scheduler</li>
      <li>Controller Manager</li>
    </ul>
    <p>Was this page helpful? Send us feedback.</p>
    <p>Last modified on July 2026.</p>
  </div>
</main>
<footer>Copyright 2026</footer>
</body>
</html>"""


class TestPipelineOrchestrator:
    def test_basic_pipeline_kubernetes(self):
        orchestrator = PipelineOrchestrator()
        record = PageRecord(
            url="https://kubernetes.io/docs/concepts/",
        )
        result = orchestrator.run(_KUBE_HTML, record)

        assert result.record.framework == "kubernetes"
        assert result.record.status == "passed"
        assert result.final_report.passed is True
        assert result.final_report.total_score > 0.6
        assert len(result.steps) > 0
        assert result.retry_count == 0

    def test_output_contains_content(self):
        orchestrator = PipelineOrchestrator()
        record = PageRecord(url="https://k8s.test/docs/test/")
        result = orchestrator.run(_KUBE_HTML, record)

        assert "# Kubernetes Concepts" in result.content
        assert "Architecture Overview" in result.content
        assert "```bash" in result.content
        assert "kubectl get pods" in result.content

    def test_quality_report_in_record(self):
        orchestrator = PipelineOrchestrator()
        record = PageRecord(url="https://k8s.test/docs/test/")
        orchestrator.run(_KUBE_HTML, record)

        assert record.quality_report is not None
        assert isinstance(record.quality_report, dict)
        assert "passed" in record.quality_report
        assert "total_score" in record.quality_report

    def test_retry_on_bad_content(self):
        orchestrator = PipelineOrchestrator(max_retries=1, quality_threshold=0.9)
        bad_html = """<html><body>
        <div class="td-content">
        <h1>Title</h1>
        <p>Short.</p>
        <h5>Deep jump</h5>
        <p>Even shorter.</p>
        <p>Edit this page</p>
        <p>Was this page helpful?</p>
        </div></body></html>"""

        record = PageRecord(url="https://test.example.com/docs/")
        result = orchestrator.run(bad_html, record)

        assert result.retry_count >= 1
        assert result.final_report.passed is True or result.record.status == "manual_review"

    def test_manual_review_after_exhausted_retries(self):
        orchestrator = PipelineOrchestrator(max_retries=0, quality_threshold=1.5, quality_min_length=1)
        # h1→h4 跳跃（2 次），用 <main> 确保 filter_main_content 匹配
        # 避免 trafilatura 兜底剥离标题结构
        bad_html = """<html><body><main>
        <h1>Big</h1>
        <h4>Medium jump</h4>
        <h1>Big Again</h1>
        <h4>Medium again</h4>
        <p>Edit this page</p>
        <p>Was this page helpful?</p>
        <p>Copyright 2024</p>
        </main></body></html>"""

        record = PageRecord(url="https://test.example.com/docs/bad/")
        result = orchestrator.run(bad_html, record)

        assert result.record.status == "manual_review"
        assert result.final_report.passed is False


class TestPipelineOrchestratorGeneric:
    def test_generic_framework_fallback(self):
        orchestrator = PipelineOrchestrator()
        html = """<html><body>
        <article><h1>Page Title</h1><p>Some content here.</p>
        <p>A bit more content to make it long enough for the quality check to pass.</p>
        <p>Still going to fill up the minimum length threshold of 500 characters.</p>
        """ + "a" * 500 + """</article></body></html>"""

        record = PageRecord(url="https://unknown-site.example.com/docs/")
        result = orchestrator.run(html, record)

        assert result.record.framework == "generic"
        assert result.retry_count == 0


_GITBOOK_MD = """# API

> For the complete documentation index, see [llms.txt](https://unsloth.ai/docs/llms.txt).

## Open a terminal and load a GGUF model

```bash
unsloth run --model unsloth/gemma-4-26B-A4B-it-GGUF:UD-Q4_K_XL
```

{% columns %}

{% column %}

Column text.

{% endcolumn %}

{% endcolumns %}

# Agent Instructions

GET https://unsloth.ai/docs/docs.md?ask=<question>

Instructions for AI agents visiting this page.
"""


class TestPipelineMarkdownSource:
    def test_markdown_source_reuses_detected_framework(self):
        """markdown 源应使用 fetch 阶段检测到的 gitbook profile，
        markdown_strip_headings（Agent Instructions）与 boilerplate 短语才能生效。"""
        orchestrator = PipelineOrchestrator()
        record = PageRecord(
            url="https://unsloth.ai/docs/basics/api",
            source="markdown",
            framework="gitbook",
        )
        result = orchestrator.run(_GITBOOK_MD, record)

        assert result.record.framework == "gitbook"
        assert result.final_report.passed is True
        # gitbook profile 的 markdown_strip_headings 生效
        assert "# Agent Instructions" not in result.content
        assert "Instructions for AI agents" not in result.content
        # liquid 标签剥离
        assert "{% columns %}" not in result.content
        assert "{% column %}" not in result.content
        # boilerplate 短语行剥离
        assert "For the complete documentation index" not in result.content
        # 代码块保留
        assert "```bash" in result.content
        assert "unsloth run --model" in result.content

    def test_markdown_source_without_framework_falls_back_generic(self):
        orchestrator = PipelineOrchestrator()
        record = PageRecord(
            url="https://unsloth.ai/docs/basics/api",
            source="markdown",
        )
        result = orchestrator.run(_GITBOOK_MD, record)

        assert result.record.framework == "generic"
        # generic profile 无 markdown_strip_headings → Agent Instructions 保留
        assert "# Agent Instructions" in result.content
        # 代码块仍保留
        assert "```bash" in result.content

class TestPipelineMarkdownSource:
    GITBOOK_PROFILE = None

    def _gitbook_profile(self):
        from silkworm.spin.detector import get_profile
        return get_profile("gitbook")

    def test_markdown_path_uses_framework_profile(self):
        """source=markdown 且 framework=gitbook → 用 gitbook profile 清洗
        （markdown_strip_headings 移除 Agent Instructions 章节）。"""
        orchestrator = PipelineOrchestrator()
        record = PageRecord(
            url="https://unsloth.ai/docs/basics/api",
            source="markdown",
            framework="gitbook",
        )
        md = (
            "# API\n\n"
            "Official docs content about the API.\n\n"
            "```python\nprint('hello')\n```\n\n"
            "More content to satisfy the minimum length gate for this page.\n"
            + "x" * 400
            + "\n\n# Agent Instructions\n\nCite the docs. Do not hallucinate.\n"
        )
        result = orchestrator.run(md, record)
        assert "Agent Instructions" not in result.content
        assert "Cite the docs" not in result.content
        assert "```python" in result.content

    def test_markdown_hard_fail_when_code_lost(self):
        """原始有围栏但清洗后 0 围栏 → 质检不通过（防误报）。"""
        orchestrator = PipelineOrchestrator(quality_min_length=1, quality_threshold=0.0)
        record = PageRecord(url="https://x.com/docs/y", source="markdown")
        md = "# T\n\n```python\nx = 1\n```\n\n" + ("word " * 200) + "\n"
        # monkeypatch normalize_markdown 模拟代码块被误剥
        import silkworm.pipeline as p
        orig = p.normalize_markdown
        p.normalize_markdown = lambda raw, profile: "# T\n\n" + ("word " * 200) + "\n"
        try:
            result = orchestrator.run(md, record)
        finally:
            p.normalize_markdown = orig
        assert result.final_report.passed is False
        assert "code_preserved" in result.final_report.checks


class TestInferFrameworkFromUrl:
    def test_gitbook_md_url(self):
        from silkworm.pipeline import PipelineOrchestrator
        assert (
            PipelineOrchestrator._infer_framework_from_url(
                "https://unsloth.ai/docs/basics/api.md"
            )
            == "gitbook"
        )

    def test_md_url_with_query(self):
        from silkworm.pipeline import PipelineOrchestrator
        # query 剥离后路径仍以 .md 结尾 → 同样命中 gitbook
        assert (
            PipelineOrchestrator._infer_framework_from_url(
                "https://x.com/docs/a.md?token=abc"
            )
            == "gitbook"
        )

    def test_non_md_url_generic(self):
        from silkworm.pipeline import PipelineOrchestrator
        assert (
            PipelineOrchestrator._infer_framework_from_url(
                "https://x.com/docs/basics/api"
            )
            == "generic"
        )

    def test_non_http_generic(self):
        from silkworm.pipeline import PipelineOrchestrator
        assert PipelineOrchestrator._infer_framework_from_url("cocoon:abc") == "generic"

    def test_markdown_source_uses_inferred_framework(self):
        """source=markdown + gitbook .md URL → gitbook profile（剥离 Agent Instructions）。"""
        orchestrator = PipelineOrchestrator()
        record = PageRecord(url="https://unsloth.ai/docs/basics/api.md", source="markdown")
        md = (
            "# API\n\nBody content here.\n\n" + "x" * 400
            + "\n\n# Agent Instructions\n\nCite the docs.\n"
        )
        result = orchestrator.run(md, record)
        assert record.framework == "gitbook"
        assert "Agent Instructions" not in result.content
