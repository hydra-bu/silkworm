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
