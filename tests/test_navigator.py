"""Navigator 模块测试：extractor / analyzer / crawler / discovery"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from silkworm.config import LLMConfig
from silkworm.feed.navigator.analyzer import analyze_nav_tree
from silkworm.feed.navigator.crawler import flatten_nav_tree
from silkworm.feed.navigator.discovery import discover_navigation
from silkworm.feed.navigator.extractor import extract_nav_html


NAV_HTML = """<nav class="td-sidebar-nav">
  <ul class="td-sidebar-nav__section">
    <li class="td-sidebar-nav__section-title">
      <a href="/docs/concepts/">Concepts</a>
      <ul>
        <li><a href="/docs/concepts/architecture/">Architecture</a></li>
        <li><a href="/docs/concepts/containers/">Containers</a></li>
      </ul>
    </li>
    <li class="td-sidebar-nav__section-title">
      <a href="/docs/tasks/">Tasks</a>
      <ul>
        <li><a href="/docs/tasks/configure/">Configure</a></li>
      </ul>
    </li>
  </ul>
</nav>"""

NAV_TREE = {
    "sections": [
        {
            "title": "Concepts",
            "url": "/docs/concepts/",
            "children": [
                {"title": "Architecture", "url": "/docs/concepts/architecture/", "children": []},
                {"title": "Containers", "url": "/docs/concepts/containers/", "children": []},
            ],
        },
        {
            "title": "Tasks",
            "url": "/docs/tasks/",
            "children": [
                {"title": "Configure", "url": "/docs/tasks/configure/", "children": []},
            ],
        },
    ]
}


# ── extract_nav_html ──────────────────────────────────────

class TestExtractNavHtml:
    def test_known_sidebar(self):
        html = f"<html><body>{NAV_HTML}</body></html>"
        nav = extract_nav_html(html)
        assert nav is not None
        assert "/docs/concepts/" in nav
        assert "/docs/tasks/" in nav

    def test_generic_nav_fallback(self):
        html = '<html><body><nav><a href="/p1">One</a><a href="/p2">Two</a><a href="/p3">Three</a><a href="/p4">Four</a><a href="/p5">Five</a></nav></body></html>'
        nav = extract_nav_html(html)
        assert nav is not None
        assert "One" in nav

    def test_no_nav_returns_none(self):
        nav = extract_nav_html("<html><body><p>no nav</p></body></html>")
        assert nav is None

    def test_truncates_large_nav(self):
        many = "".join(f'<li><a href="/p{i}">P{i}</a></li>' for i in range(2000))
        html = f"<html><body><nav><ul>{many}</ul></nav></body></html>"
        nav = extract_nav_html(html)
        assert nav is not None
        assert len(nav) <= 51000


# ── flatten_nav_tree ──────────────────────────────────────

class TestFlattenNavTree:
    def test_flatten_full_tree(self):
        urls = flatten_nav_tree(NAV_TREE, base_url="https://k8s.io")
        expected = [
            "https://k8s.io/docs/concepts/",
            "https://k8s.io/docs/concepts/architecture/",
            "https://k8s.io/docs/concepts/containers/",
            "https://k8s.io/docs/tasks/",
            "https://k8s.io/docs/tasks/configure/",
        ]
        assert urls == expected

    def test_empty_tree(self):
        assert flatten_nav_tree({}) == []

    def test_prompt_match_case_insensitive(self):
        urls = flatten_nav_tree(NAV_TREE, prompt="concepts")
        assert any("concepts" in u for u in urls)
        assert not any("tasks" in u for u in urls)

    def test_prompt_no_match_fallback_all(self):
        urls = flatten_nav_tree(NAV_TREE, prompt="nonexistent")
        assert len(urls) == 5

    def test_prompt_empty_returns_all(self):
        urls = flatten_nav_tree(NAV_TREE, prompt=None)
        assert len(urls) == 5

    def test_exclude_top_level_section(self):
        urls = flatten_nav_tree(NAV_TREE, exclude=["tasks"])
        assert len(urls) == 3
        assert any("concepts" in u for u in urls)
        assert not any("tasks" in u for u in urls)

    def test_exclude_nested_child(self):
        tree = {
            "sections": [
                {
                    "title": "Concepts",
                    "url": "/docs/concepts/",
                    "children": [
                        {"title": "Architecture", "url": "/docs/concepts/architecture/", "children": []},
                        {"title": "Containers", "url": "/docs/concepts/containers/", "children": []},
                    ],
                },
            ]
        }
        urls = flatten_nav_tree(tree, exclude=["architecture"])
        assert len(urls) == 2
        assert "/docs/concepts/" in urls
        assert "/docs/concepts/containers/" in urls
        assert "/docs/concepts/architecture/" not in urls

    def test_exclude_empty_returns_all(self):
        urls = flatten_nav_tree(NAV_TREE, exclude=None)
        assert len(urls) == 5

    def test_exclude_no_match_returns_all(self):
        urls = flatten_nav_tree(NAV_TREE, exclude=["nonexistent"])
        assert len(urls) == 5

    def test_exclude_case_insensitive(self):
        urls = flatten_nav_tree(NAV_TREE, exclude=["TASKS"])
        assert len(urls) == 3
        assert not any("tasks" in u for u in urls)

    def test_exclude_with_prompt(self):
        """exclude 在 prompt 之前执行，排除的章节不会出现在 prompt 结果中"""
        urls = flatten_nav_tree(NAV_TREE, exclude=["tasks"], prompt="concepts")
        assert len(urls) == 3

    def test_exclude_multiple_sections(self):
        urls = flatten_nav_tree(NAV_TREE, exclude=["concepts", "tasks"])
        assert urls == []


# ── analyze_nav_tree ──────────────────────────────────────

class TestAnalyzeNavTree:
    @patch("silkworm.feed.navigator.analyzer.OpenAI")
    def test_parse_llm_json_response(self, mock_openai):
        mock_instance = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps(NAV_TREE)
        mock_instance.chat.completions.create.return_value = MagicMock(choices=[mock_choice])
        mock_openai.return_value = mock_instance

        result = analyze_nav_tree(
            "<nav>...</nav>",
            LLMConfig(base_url="http://test/v1", api_key="sk-test", model="m"),
        )
        assert result is not None
        assert len(result["sections"]) == 2

    @patch("silkworm.feed.navigator.analyzer.OpenAI")
    def test_handles_markdown_fence_json(self, mock_openai):
        content = f"```json\n{json.dumps(NAV_TREE)}\n```"
        mock_instance = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = content
        mock_instance.chat.completions.create.return_value = MagicMock(choices=[mock_choice])
        mock_openai.return_value = mock_instance

        result = analyze_nav_tree(
            "<nav>...</nav>",
            LLMConfig(base_url="http://test/v1", api_key="sk-test", model="m"),
        )
        assert result is not None
        assert len(result["sections"]) == 2

    @patch("silkworm.feed.navigator.analyzer.OpenAI")
    def test_api_error_returns_none(self, mock_openai):
        mock_instance = MagicMock()
        mock_instance.chat.completions.create.side_effect = Exception("API error")
        mock_openai.return_value = mock_instance

        result = analyze_nav_tree(
            "<nav>...</nav>",
            LLMConfig(base_url="http://test/v1", api_key="sk-test", model="m"),
        )
        assert result is None

    @patch("silkworm.feed.navigator.analyzer.OpenAI")
    def test_invalid_json_returns_none(self, mock_openai):
        mock_instance = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = "not json at all"
        mock_instance.chat.completions.create.return_value = MagicMock(choices=[mock_choice])
        mock_openai.return_value = mock_instance

        result = analyze_nav_tree(
            "<nav>...</nav>",
            LLMConfig(base_url="http://test/v1", api_key="sk-test", model="m"),
        )
        assert result is None

    @patch("silkworm.feed.navigator.analyzer.OpenAI")
    def test_no_api_key_returns_none(self, mock_openai):
        result = analyze_nav_tree(
            "<nav>...</nav>",
            LLMConfig(base_url="http://test/v1", api_key="", model="m"),
        )
        assert result is None


# ── discover_navigation ───────────────────────────────────

class TestDiscoverNavigation:
    @patch("silkworm.feed.navigator.discovery.httpx.AsyncClient")
    @patch("silkworm.feed.navigator.analyzer.OpenAI")
    def test_full_discovery_chain(self, mock_openai, mock_client_class):
        # Both _fetch_and_extract and _sitemap_fallback create own clients
        mock_client_class.return_value = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = AsyncMock()
        mock_client_class.return_value.__aexit__.return_value = None
        mock_client = mock_client_class.return_value.__aenter__.return_value
        mock_client.get = AsyncMock()
        mock_client.head = AsyncMock()
        mock_client.aclose = AsyncMock()

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.text = f"<html><body>{NAV_HTML}</body></html>"
        mock_response.raise_for_status = MagicMock()
        mock_client.get.return_value = mock_response

        mock_instance = MagicMock()
        mock_choice = MagicMock()
        mock_choice.message.content = json.dumps(NAV_TREE)
        mock_instance.chat.completions.create.return_value = MagicMock(choices=[mock_choice])
        mock_openai.return_value = mock_instance

        result, pri = asyncio_run(discover_navigation(
            "https://kubernetes.io/docs/",
            prompt="concepts",
            llm_config=LLMConfig(base_url="http://test/v1", api_key="sk-test", model="m"),
        ))
        assert result is not None
        assert any("concepts" in u.lower() for u in result)
        assert pri is not None
        assert "core" in pri or "secondary" in pri

    @patch("silkworm.feed.navigator.discovery.httpx.AsyncClient")
    def test_graceful_fallback_on_no_nav(self, mock_client_class):
        mock_client = AsyncMock()
        mock_client.get = AsyncMock()
        mock_client.head = AsyncMock()
        mock_client.aclose = AsyncMock()
        # _sitemap_fallback creates its own client instance (client is None)
        mock_client_class.return_value = AsyncMock()
        mock_client_class.return_value.__aenter__.return_value = mock_client
        mock_client_class.return_value.__aexit__.return_value = None

        mock_response = AsyncMock()
        mock_response.status_code = 200
        mock_response.text = "<html><body><p>alone</p></body></html>"
        mock_response.raise_for_status = MagicMock()
        mock_client.get.return_value = mock_response

        # sitemap fallback => head returns 404
        mock_response2 = AsyncMock()
        mock_response2.status_code = 404
        mock_response2.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError("404", request=MagicMock(), response=MagicMock())
        )
        mock_client.head.return_value = mock_response2

        result, pri = asyncio_run(discover_navigation(
            "https://unknown.dev/docs/",
            llm_config=LLMConfig(base_url="http://test/v1", api_key="sk-test", model="m"),
        ))
        assert isinstance(result, list)
        assert pri is None  # sitemap fallback has no priority info


def asyncio_run(coro):
    """Helper to run async test coroutine."""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)
