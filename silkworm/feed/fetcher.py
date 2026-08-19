"""原始抓取 — httpx 请求 + 原始内容落盘（HTML / 框架原生 Markdown）。"""

import hashlib
import logging
from datetime import datetime, timezone
from pathlib import Path

import httpx

from silkworm.models import PageRecord

logger = logging.getLogger(__name__)

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

# 官方 markdown 端点的最低有效内容长度（字节）。
# 短于此大概率是 404 兜底页/空壳，应降级回 HTML 管线。
_MIN_MD_CONTENT_BYTES = 200


def compute_content_hash(content: bytes) -> str:
    """计算内容的 SHA256 哈希。"""
    return hashlib.sha256(content).hexdigest()


def save_raw_html(cocoon_dir: Path, url: str, content: bytes) -> str:
    """保存原始 HTML 到 cocoon 存储区，返回相对路径。"""
    cocoon_dir.mkdir(parents=True, exist_ok=True)
    url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
    filename = f"{url_hash}.html"
    filepath = cocoon_dir / filename
    filepath.write_bytes(content)
    return str(filename)


def save_raw_md(cocoon_dir: Path, url: str, content: bytes) -> str:
    """保存原始 Markdown 到 cocoon 存储区，返回相对路径。"""
    cocoon_dir.mkdir(parents=True, exist_ok=True)
    url_hash = hashlib.sha256(url.encode()).hexdigest()[:16]
    filename = f"{url_hash}.md"
    filepath = cocoon_dir / filename
    filepath.write_bytes(content)
    return str(filename)


async def fetch_page(
    client: httpx.AsyncClient,
    url: str,
    cocoon_dir: Path,
    user_agent: str | None = None,
) -> PageRecord:
    """抓取单个页面并保存原始内容，返回 PageRecord。

    抓取策略（通用机制，由框架 Profile 驱动，不含任何站点硬编码）：
    1. 先抓 HTML（框架指纹检测需要）；
    2. 若检测到的框架 Profile 声明了 markdown_suffixes（官方 markdown
       端点），则追加对应后缀尝试抓取原生 markdown；
    3. markdown 端点可用且内容有效 → source="markdown"（管线走轻量
       normalize，且保留检测到的框架名，让 profile 的清洗配置生效）；
       否则降级为纯 HTML 管线。
    """
    headers = {
        "User-Agent": user_agent or _DEFAULT_UA,
        "Accept-Language": "en-US,en;q=0.9",
    }

    try:
        resp = await client.get(url, headers=headers, follow_redirects=True, timeout=30.0)
        content = resp.content
        http_status = resp.status_code
    except httpx.HTTPError as e:
        return PageRecord(
            url=url,
            http_status=0,
            status="failed",
            quality_report={"error": str(e)},
        )

    if http_status != 200:
        return PageRecord(
            url=url,
            canonical_url=str(resp.url),
            http_status=http_status,
            status="failed",
            quality_report={"error": f"HTTP {http_status}"},
        )

    content_hash = compute_content_hash(content)
    html_path = save_raw_html(cocoon_dir, url, content)

    record = PageRecord(
        url=url,
        canonical_url=str(resp.url),
        fetched_at=datetime.now(timezone.utc),
        content_hash=content_hash,
        http_status=http_status,
        raw_html_path=html_path,
        status="fetched",
    )

    # 框架指纹检测（HTML 源由 pipeline 兜底检测，这里检测结果用于
    # markdown 端点推导，并在 markdown 命中时写入 record.framework，
    # 避免 markdown 路径丢失框架上下文导致 profile 清洗配置失效）
    framework = _detect_framework(content)
    record.framework = framework

    # 框架声明式 markdown 原生端点探测
    md_url = _markdown_endpoint(url, framework)
    if md_url:
        md_record = await _try_fetch_markdown(client, record, md_url, cocoon_dir, headers)
        if md_record is not None:
            return md_record

    return record


def _detect_framework(html_content: bytes) -> str:
    """检测文档框架，失败回退 generic。"""
    from silkworm.spin.detector import detect_framework

    try:
        return detect_framework(html_content.decode("utf-8", errors="replace"))
    except Exception:
        return "generic"


def _markdown_endpoint(url: str, framework: str) -> str | None:
    """根据框架 Profile 声明，推导该页面的官方 markdown 端点 URL。

    通用规则：profile.markdown_suffixes 非空 → 在 URL 追加第一个后缀。
    框架检测失败时回退 generic profile（通常无后缀，返回 None）。
    URL 本身已以声明后缀结尾时不追加（避免 x.md.md 双重后缀）。
    """
    from silkworm.spin.detector import get_profile

    profile = get_profile(framework)
    if not profile.markdown_suffixes:
        return None
    suffix = profile.markdown_suffixes[0]
    if url.endswith(suffix):
        return url
    return url + suffix


async def _try_fetch_markdown(
    client: httpx.AsyncClient,
    record: PageRecord,
    md_url: str,
    cocoon_dir: Path,
    headers: dict,
) -> PageRecord | None:
    """尝试抓取官方 markdown 端点。成功返回增强后的 record，失败返回 None。"""
    try:
        resp = await client.get(md_url, headers=headers, follow_redirects=True, timeout=30.0)
    except httpx.HTTPError as e:
        logger.debug("markdown 端点抓取失败 %s: %s", md_url, e)
        return None

    if resp.status_code != 200:
        logger.debug("markdown 端点 HTTP %s (%s)", resp.status_code, md_url)
        return None

    body = resp.content
    if len(body) < _MIN_MD_CONTENT_BYTES:
        logger.debug("markdown 端点内容过短，降级 HTML (%s)", md_url)
        return None

    text = body.decode("utf-8", errors="replace")
    # 内容有效性：官方 markdown 端点不应以 <html 开头（防止 404 HTML 兜底页）
    if text.lstrip().lower().startswith(("<!doctype", "<html")):
        logger.debug("markdown 端点返回了 HTML，降级 (%s)", md_url)
        return None

    md_path = save_raw_md(cocoon_dir, record.url, body)
    record.source = "markdown"
    record.raw_md_path = md_path
    record.content_hash = compute_content_hash(body)
    logger.info("markdown 原生端点命中: %s → %s (%d bytes)", md_url, md_path, len(body))
    return record
