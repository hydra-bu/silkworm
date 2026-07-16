"""Discovery 单元测试。"""


from silkworm.feed.discovery import is_allowed, normalize_url


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
