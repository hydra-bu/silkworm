"""Output 单元测试。"""

from datetime import datetime, timezone
from pathlib import Path

from silkworm.models import PageRecord
from silkworm.silk.output import build_frontmatter, resolve_output_path, write_silk


class TestBuildFrontmatter:
    def test_basic_frontmatter(self):
        record = PageRecord(
            url="https://example.com/docs/intro",
            content_hash="abc123",
            framework="mkdocs-material",
            fetched_at=datetime(2026, 7, 9, 10, 0, 0, tzinfo=timezone.utc),
        )
        fm = build_frontmatter(record)
        assert "source_url: https://example.com/docs/intro" in fm
        assert "content_hash: abc123" in fm
        assert "framework: mkdocs-material" in fm
        assert "2026-07-09" in fm

    def test_frontmatter_without_fetched_at(self):
        record = PageRecord(url="https://example.com/page")
        fm = build_frontmatter(record)
        assert "source_url: https://example.com/page" in fm


class TestResolveOutputPath:
    def test_basic_path(self):
        path = resolve_output_path(Path("silk"), "k8s", "https://kubernetes.io/docs/concepts/")
        assert str(path) == "silk/k8s/docs/concepts/index.md"

    def test_strips_html_extension(self):
        path = resolve_output_path(Path("silk"), "test", "https://example.com/page.html")
        assert str(path) == "silk/test/page.md"

    def test_root_path(self):
        path = resolve_output_path(Path("silk"), "site", "https://example.com")
        assert str(path).endswith("index.md")

    def test_path_without_trailing_slash(self):
        path = resolve_output_path(Path("silk"), "site", "https://example.com/guide")
        assert str(path) == "silk/site/guide.md"

    def test_domain_as_site_name(self):
        path = resolve_output_path(Path("silk"), "docs.pydantic.dev", "https://docs.pydantic.dev/latest/")
        assert str(path) == "silk/docs.pydantic.dev/latest/index.md"


class TestWriteSilk:
    def test_writes_markdown_file(self, tmp_path: Path):
        record = PageRecord(
            url="https://example.com/docs/intro",
            content_hash="abc",
            framework="mkdocs",
            fetched_at=datetime.now(timezone.utc),
        )
        output_path = write_silk(tmp_path, "test-site", record, "# Hello\n\nContent")
        assert output_path.exists()
        content = output_path.read_text(encoding="utf-8")
        assert "source_url: https://example.com/docs/intro" in content
        assert "# Hello" in content

    def test_creates_parent_directories(self, tmp_path: Path):
        record = PageRecord(url="https://example.com/a/b/c")
        deep_path = tmp_path / "silk"
        output_path = write_silk(deep_path, "site", record, "content")
        assert output_path.parent.exists()
