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
        result = orchestrator.run(_KUBE_HTML, record)

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