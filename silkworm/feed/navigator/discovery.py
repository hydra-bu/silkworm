"""导航发现 — 整合 extract→analyze→flatten 流程，支持静默降级到 sitemap。"""

import logging

import httpx

from silkworm.config import LLMConfig
from silkworm.feed.discovery import (
    discover_sitemap_url,
    discover_from_sitemap,
)
from silkworm.feed.fetcher import fetch_page
from silkworm.feed.navigator.extractor import extract_nav_html
from silkworm.feed.navigator.analyzer import analyze_nav_tree
from silkworm.feed.navigator.crawler import flatten_nav_tree, priority_stats

logger = logging.getLogger(__name__)

# 默认降级配置
_DEFAULT_ALLOW = ["/docs/"]
_DEFAULT_DENY: list[str] = []


async def discover_navigation(
    url: str,
    prompt: str | None = None,
    exclude: list[str] | None = None,
    llm_config: LLMConfig | None = None,
    httpx_client: httpx.AsyncClient | None = None,
    allow_patterns: list[str] | None = None,
    deny_patterns: list[str] | None = None,
) -> tuple[list[str], dict | None]:
    """从页面导航树发现文档 URL。

    流程：提取导航 HTML → LLM 分析 → prompt 过滤 → exclude 排除 → 展平 URL。
    任何步骤失败则静默降级到 sitemap 发现。

    Returns:
        (url 列表, priority 统计 dict 或 None)
        priority 格式: {"core": N, "secondary": N}
        sitemap 降级时 priority 为 None。
    """
    allow = allow_patterns or _DEFAULT_ALLOW
    deny = deny_patterns or _DEFAULT_DENY
    client = httpx_client

    # Step 1: 提取导航 HTML
    nav_html = await _fetch_and_extract(url, client)
    if nav_html is None:
        logger.info("导航 HTML 提取失败，降级到 sitemap")
        urls = await _sitemap_fallback(url, client, allow, deny)
        return urls, None

    # Step 2: LLM 分析
    cfg = llm_config or LLMConfig()
    tree = analyze_nav_tree(nav_html, cfg)
    if tree is None:
        logger.info("LLM 导航分析失败，降级到 sitemap")
        urls = await _sitemap_fallback(url, client, allow, deny)
        return urls, None

    logger.info(
        "导航树解析成功: %d 个顶级章节", len(tree.get("sections", []))
    )

    # Step 3: 展平 + 过滤
    urls = flatten_nav_tree(tree, base_url=url, prompt=prompt, exclude=exclude)
    pri = priority_stats(tree, prompt=prompt, exclude=exclude)
    if not urls:
        logger.info("导航树展平为空，降级到 sitemap")
        urls = await _sitemap_fallback(url, client, allow, deny)
        return urls, None

    logger.info("导航树发现 %d 个 URL（核心 %d 个 | 次要 %d 个）",
                len(urls), pri.get("core", 0), pri.get("secondary", 0))
    return urls, pri


async def _fetch_and_extract(
    url: str, client: httpx.AsyncClient | None
) -> str | None:
    """获取页面并提取导航区域 HTML。"""
    if client is None:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30.0) as c:
            return await _do_fetch_extract(url, c)

    return await _do_fetch_extract(url, client)


async def _do_fetch_extract(url: str, client: httpx.AsyncClient) -> str | None:
    try:
        resp = await client.get(url, follow_redirects=True, timeout=30.0)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        logger.warning("导航页面抓取失败: %s", e)
        return None

    return extract_nav_html(resp.text, url)


async def _sitemap_fallback(
    url: str,
    client: httpx.AsyncClient | None,
    allow: list[str],
    deny: list[str],
) -> list[str]:
    """静默降级到 sitemap 发现。"""
    own_client = False
    if client is None:
        client = httpx.AsyncClient(follow_redirects=True, timeout=30.0)
        own_client = True

    try:
        sitemap_url = await discover_sitemap_url(url, client)
        if sitemap_url:
            return await discover_from_sitemap(
                client, sitemap_url, allow_patterns=allow, deny_patterns=deny
            )
        return []
    finally:
        if own_client:
            await client.aclose()
