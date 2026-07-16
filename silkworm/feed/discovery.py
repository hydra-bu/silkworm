"""站点发现 — sitemap.xml 解析 / 导航树 BFS。"""

import re
import xml.etree.ElementTree as ET
from urllib.parse import urljoin, urlparse

import httpx


def normalize_url(raw_url: str, base_url: str | None = None) -> str:
    """URL 归一化：去 fragment、尾部 /、统一小写 scheme+host。"""
    parsed = urlparse(raw_url)
    scheme = parsed.scheme.lower() or "https"
    hostname = parsed.hostname.lower() if parsed.hostname else ""
    path = parsed.path.rstrip("/") or "/"
    query = parsed.query  # 保留 query（某些文档站使用）
    fragment = ""

    normalized = f"{scheme}://{hostname}{path}"
    if query:
        normalized += f"?{query}"

    if base_url:
        normalized = urljoin(base_url, normalized)

    return normalized


def is_allowed(url: str, allow_patterns: list[str], deny_patterns: list[str]) -> bool:
    """检查 URL 是否被允许抓取。"""
    for pattern in deny_patterns:
        if re.search(pattern, url):
            return False
    if allow_patterns:
        if not any(re.search(p, url) for p in allow_patterns):
            return False
    return True


async def discover_from_sitemap(
    client: httpx.AsyncClient,
    sitemap_url: str,
    allow_patterns: list[str] | None = None,
    deny_patterns: list[str] | None = None,
) -> list[str]:
    """从 sitemap.xml 发现页面 URL。"""
    allow_patterns = allow_patterns or []
    deny_patterns = deny_patterns or []

    try:
        resp = await client.get(sitemap_url, follow_redirects=True, timeout=30.0)
        resp.raise_for_status()
    except httpx.HTTPError:
        return []

    urls: list[str] = []
    root = ET.fromstring(resp.text)

    ns = {"ns": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    for loc in root.iterfind(".//ns:loc", ns):
        raw = loc.text.strip() if loc.text else ""
        if not raw:
            continue
        normalized = normalize_url(raw)
        if is_allowed(normalized, allow_patterns, deny_patterns):
            urls.append(normalized)

    return urls


async def discover_sitemap_url(base_url: str, client: httpx.AsyncClient) -> str | None:
    """自动发现 sitemap URL 位置。"""
    common_paths = [
        urljoin(base_url, "/sitemap.xml"),
        urljoin(base_url, "/sitemap_index.xml"),
    ]
    for path in common_paths:
        try:
            resp = await client.head(path, follow_redirects=True, timeout=15.0)
            if resp.status_code == 200:
                return path
        except httpx.HTTPError:
            continue
    return None
