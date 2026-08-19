"""Fetcher 单元测试。"""

from pathlib import Path


from silkworm.feed.fetcher import compute_content_hash, save_raw_html


class TestComputeContentHash:
    def test_sha256_hash(self):
        h1 = compute_content_hash(b"hello")
        h2 = compute_content_hash(b"hello")
        assert h1 == h2
        assert len(h1) == 64  # SHA256 hex

    def test_different_content_different_hash(self):
        h1 = compute_content_hash(b"hello")
        h2 = compute_content_hash(b"world")
        assert h1 != h2


class TestSaveRawHtml:
    def test_saves_to_cocoon(self, tmp_path: Path):
        cocoon = tmp_path / "cocoon"
        path = save_raw_html(cocoon, "https://example.com/page", b"<html></html>")
        assert cocoon.exists()
        saved_file = cocoon / Path(path).name
        assert saved_file.exists()
        assert saved_file.read_bytes() == b"<html></html>"

    def test_returns_relative_path(self):
        cocoon = Path("/tmp/test_cocoon")
        path = save_raw_html(cocoon, "https://example.com", b"test")
        assert path.endswith(".html")
        assert "/" not in path


class TestMarkdownEndpoint:
    """_markdown_endpoint: 框架 Profile 后缀探测 + 防 x.md.md 双重后缀。"""

    def test_appends_suffix_for_suffix_declaring_framework(self):
        from silkworm.feed.fetcher import _markdown_endpoint

        assert _markdown_endpoint("https://unsloth.ai/docs/basics/api", "gitbook") == (
            "https://unsloth.ai/docs/basics/api.md"
        )

    def test_no_suffix_for_generic(self):
        from silkworm.feed.fetcher import _markdown_endpoint

        assert _markdown_endpoint("https://example.com/docs/x", "generic") is None

    def test_url_already_ending_in_suffix_not_doubled(self):
        from silkworm.feed.fetcher import _markdown_endpoint

        # 防御：发现阶段漏网的 .md URL 不应再追加 .md 成 x.md.md；
        # 它本身就是 markdown 端点，原样返回交由 markdown 处理
        assert _markdown_endpoint("https://unsloth.ai/docs/basics/api.md", "gitbook") == (
            "https://unsloth.ai/docs/basics/api.md"
        )
