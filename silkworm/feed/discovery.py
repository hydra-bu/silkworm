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


def dedup_markdown_endpoints(urls: list[str]) -> list[str]:
    """同页去重：`.md` 端点归一为裸 URL（`X.md` → `X`）。

    通用规则：现代文档站的官方 markdown 端点（URL 追加 .md）与页面
    本体指向同一内容，重复抓取/输出浪费 2 倍处理量且产生重复文件
    （X.md 与 X.md.md）。统一归一为裸 URL — 抓取阶段会根据框架
    Profile 声明自动探测官方 markdown 端点并使用原生内容。
    """
    seen: set[str] = set()
    result: list[str] = []
    for u in urls:
        canonical = u[:-3] if u.endswith(".md") else u
        if canonical in seen:
            continue
        seen.add(canonical)
        result.append(canonical)
    return result


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


async def discover_llms_txt_url(base_url: str, client: httpx.AsyncClient) -> str | None:
    """自动发现 llms.txt（现代文档站通用约定：站点根/子路径的纯文本页面索引）。"""
    base = base_url.rstrip("/")
    common_paths = [
        f"{base}/llms.txt",
        urljoin(base_url, "/llms.txt"),
    ]
    for path in dict.fromkeys(common_paths):
        try:
            resp = await client.head(path, follow_redirects=True, timeout=15.0)
            if resp.status_code == 200:
                return path
        except httpx.HTTPError:
            continue
    return None


async def discover_from_llms_txt(
    client: httpx.AsyncClient,
    llms_txt_url: str,
    allow_patterns: list[str] | None = None,
    deny_patterns: list[str] | None = None,
) -> list[str]:
    """从 llms.txt 发现页面 URL。

    通用解析：逐行提取 http(s) URL，忽略注释行（#）与非 URL 行。
    兼容 "URL - 描述" 与纯 URL 两种格式。
    """
    allow_patterns = allow_patterns or []
    deny_patterns = deny_patterns or []

    try:
        resp = await client.get(llms_txt_url, follow_redirects=True, timeout=30.0)
        resp.raise_for_status()
    except httpx.HTTPError:
        return []

    url_re = re.compile(r"(https?://[^\s)\]<>\"',]+)")
    urls: list[str] = []
    seen: set[str] = set()
    for line in resp.text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = url_re.search(line)
        if not m:
            continue
        # 占位符 URL（行内含 < 或 >，如 ?ask=<question>）不是真实页面，丢弃。
        # 必须在 url_re 截取前检查 — 正则的 < > 排除会把占位符截断成残缺 URL。
        if "<" in line or ">" in line:
            continue
        normalized = normalize_url(m.group(1))
        if normalized in seen:
            continue
        seen.add(normalized)
        if is_allowed(normalized, allow_patterns, deny_patterns):
            urls.append(normalized)
    return dedup_markdown_endpoints(urls)
