"""Discovery 单元测试。"""

import pytest

from silkworm.feed.discovery import (
    dedup_markdown_endpoints,
    discover_from_llms_txt,
    is_allowed,
    normalize_url,
)


class TestNormalizeUrl:
    def test_remove_fragment(self):
        assert normalize_url("https://example.com/page#section") == "https://example.com/page"

    def test_strip_trailing_slash(self):
        assert normalize_url("https://example.com/page/") == "https://example.com/page"

    def test_preserves_root(self):
        assert normalize_url("https://example.com") == "https://example.com/"

    def test_lowercase_scheme(self):
        assert normalize_url("HTTPS://EXAMPLE.COM/Page") == "https://example.com/Page"

    def test_preserves_query(self):
        result = normalize_url("https://example.com/page?version=2")
        assert "version=2" in result


class TestIsAllowed:
    def test_allow_all_when_no_patterns(self):
        assert is_allowed("https://example.com/page", [], []) is True

    def test_allow_pattern_match(self):
        assert is_allowed("https://example.com/docs/intro", [r"/docs/"], []) is True

    def test_allow_pattern_no_match(self):
        assert is_allowed("https://example.com/blog/post", [r"/docs/"], []) is False

    def test_deny_pattern(self):
        assert is_allowed("https://example.com/api/v1", [], [r"/api/"]) is False

    def test_deny_overrides_allow(self):
        assert is_allowed(
            "https://example.com/docs/api/",
            [r"/docs/"],
            [r"/api/"],
        ) is False


class TestDedupMarkdownEndpoints:
    def test_md_and_bare_dedup_to_bare(self):
        assert dedup_markdown_endpoints(
            ["https://example.com/docs/a.md", "https://example.com/docs/a"]
        ) == ["https://example.com/docs/a"]

    def test_md_alone_kept(self):
        # 无裸端点配对时保留 .md（可能是站点唯一端点形态）
        assert dedup_markdown_endpoints(["https://example.com/docs/a.md"]) == [
            "https://example.com/docs/a.md"
        ]

    def test_distinct_pages_kept(self):
        assert dedup_markdown_endpoints(
            ["https://example.com/docs/a", "https://example.com/docs/b.md", "https://example.com/docs/c"]
        ) == ["https://example.com/docs/a", "https://example.com/docs/b.md", "https://example.com/docs/c"]

    def test_preserves_order(self):
        assert dedup_markdown_endpoints(
            ["https://example.com/docs/z.md", "https://example.com/docs/a", "https://example.com/docs/z"]
        ) == ["https://example.com/docs/z.md", "https://example.com/docs/a"]

    def test_non_md_suffix_untouched(self):
        assert dedup_markdown_endpoints(["https://example.com/docs/a.html"]) == [
            "https://example.com/docs/a.html"
        ]


class TestDiscoverFromLlmsTxt:
    def _client(self, text: str):
        class _Client:
            def __init__(self, body):
                self.body = body
                self.calls = []

            async def get(self, url, **kwargs):
                self.calls.append(url)
                return _Resp(self.body)

        class _Resp:
            def __init__(self, body):
                self.text = body

            def raise_for_status(self):
                pass

        return _Client(text)

    @pytest.mark.asyncio
    async def test_placeholder_urls_with_angle_brackets_dropped(self):
        client = self._client(
            "# Unsloth Docs\n"
            "\n"
            "- [API](https://unsloth.ai/docs/basics/api)\n"
            "GET https://unsloth.ai/docs/docs.md?ask=<question>\n"
            "POST https://unsloth.ai/api/v1?token=<api_token>\n"
        )
        urls = await discover_from_llms_txt(client, "https://unsloth.ai/llms.txt", [], [])
        assert urls == ["https://unsloth.ai/docs/basics/api"]

    @pytest.mark.asyncio
    async def test_md_endpoints_deduped_to_bare(self):
        client = self._client(
            "- [API](https://unsloth.ai/docs/basics/api.md)\n"
            "- [API md link](https://unsloth.ai/docs/basics/api)\n"
            "- [Install](https://unsloth.ai/docs/get-started/install.md)\n"
        )
        urls = await discover_from_llms_txt(client, "https://unsloth.ai/llms.txt", [], [])
        assert urls == [
            "https://unsloth.ai/docs/basics/api",
            "https://unsloth.ai/docs/get-started/install",
        ]

    @pytest.mark.asyncio
    async def test_allow_deny_applied(self):
        client = self._client(
            "- [A](https://example.com/docs/a)\n"
            "- [B](https://example.com/api/b)\n"
        )
        urls = await discover_from_llms_txt(
            client, "https://example.com/llms.txt", [r"/docs/"], [r"/api/"]
        )
        assert urls == ["https://example.com/docs/a"]


class TestDedupMarkdownEndpoints:
    def test_md_and_base_normalize_to_bare(self):
        from silkworm.feed.discovery import dedup_markdown_endpoints
        urls = [
            "https://x.com/docs/intro",
            "https://x.com/docs/intro.md",
            "https://x.com/docs/only-md.md",
        ]
        assert dedup_markdown_endpoints(urls) == [
            "https://x.com/docs/intro",
            "https://x.com/docs/only-md",
        ]

    def test_no_change_without_md(self):
        from silkworm.feed.discovery import dedup_markdown_endpoints
        urls = ["https://x.com/docs/a", "https://x.com/docs/b"]
        assert dedup_markdown_endpoints(urls) == urls

    def test_empty(self):
        from silkworm.feed.discovery import dedup_markdown_endpoints
        assert dedup_markdown_endpoints([]) == []
