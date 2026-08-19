"""导航发现 — 整合 extract→analyze→flatten 流程，支持静默降级到 sitemap。"""

import logging

import httpx

from silkworm.config import LLMConfig
from silkworm.feed.discovery import (
    dedup_markdown_endpoints,
    discover_sitemap_url,
    discover_from_sitemap,
    discover_llms_txt_url,
    discover_from_llms_txt,
)
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
        logger.info("导航 HTML 提取失败，降级到索引发现")
        urls = dedup_markdown_endpoints(await _index_urls(url, client, allow, deny))
        return urls, None

    # Step 2: LLM 分析
    cfg = llm_config or LLMConfig()
    tree = analyze_nav_tree(nav_html, cfg)
    if tree is None:
        logger.info("LLM 导航分析失败，降级到索引发现")
        urls = dedup_markdown_endpoints(await _index_urls(url, client, allow, deny))
        return urls, None

    logger.info(
        "导航树解析成功: %d 个顶级章节", len(tree.get("sections", []))
    )

    # Step 3: 展平 + 过滤
    urls = flatten_nav_tree(tree, base_url=url, prompt=prompt, exclude=exclude)
    pri = priority_stats(tree, prompt=prompt, exclude=exclude)

    # Step 4: 索引补全 — 导航树只展开当前章节，深层页面会漏。
    # 用站点官方索引（llms.txt / sitemap）补入导航树未覆盖的 URL。
    # 仅在无 prompt/exclude 过滤时补全，避免违背用户的章节过滤意图。
    if not prompt and not exclude:
        indexed = dedup_markdown_endpoints(await _index_urls(url, client, allow, deny))
        nav_keys = set(dedup_markdown_endpoints(urls))
        missing = [u for u in indexed if u not in nav_keys]
        if missing:
            logger.info("导航树漏 %d 个索引 URL，已补全", len(missing))
            urls.extend(missing)
            pri = {**(pri or {}), "core": pri.get("core", len(urls)) - pri.get("secondary", 0) + len(missing), "secondary": pri.get("secondary", 0)}

    # Step 5: 同页去重 — 官方 markdown 端点（X.md）与页面本体（X）
    # 指向同一内容，仅保留 HTML 端点（抓取阶段会自动探测 md 端点）。
    urls = dedup_markdown_endpoints(urls)

    if not urls:
        logger.info("导航树展平为空，降级到索引发现")
        urls = dedup_markdown_endpoints(await _index_urls(url, client, allow, deny))
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


async def _index_urls(
    url: str,
    client: httpx.AsyncClient | None,
    allow: list[str],
    deny: list[str],
) -> list[str]:
    """站点官方索引发现：llms.txt 与 sitemap 合并去重（llms.txt 优先）。

    通用机制：两个索引源都是现代文档站的通用约定，
    哪个可达用哪个，都可达则合并。
    """
    own_client = False
    if client is None:
        client = httpx.AsyncClient(follow_redirects=True, timeout=30.0)
        own_client = True

    try:
        merged: list[str] = []
        seen: set[str] = set()

        llms_url = await discover_llms_txt_url(url, client)
        if llms_url:
            for u in await discover_from_llms_txt(
                client, llms_url, allow_patterns=allow, deny_patterns=deny
            ):
                if u not in seen:
                    seen.add(u)
                    merged.append(u)

        sitemap_url = await discover_sitemap_url(url, client)
        if sitemap_url:
            for u in await discover_from_sitemap(
                client, sitemap_url, allow_patterns=allow, deny_patterns=deny
            ):
                if u not in seen:
                    seen.add(u)
                    merged.append(u)

        logger.info("索引发现: llms.txt=%s sitemap=%s 合并 %d 个 URL",
                    bool(llms_url), bool(sitemap_url), len(merged))
        return dedup_markdown_endpoints(merged)
    finally:
        if own_client:
            await client.aclose()
