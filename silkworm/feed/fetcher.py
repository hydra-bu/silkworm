"""原始抓取 — httpx 请求 + 原始 HTML 落盘。"""

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import httpx

from silkworm.models import PageRecord


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


async def fetch_page(
    client: httpx.AsyncClient, url: str, cocoon_dir: Path, user_agent: str | None = None
) -> PageRecord:
    """抓取单个页面并保存原始 HTML，返回 PageRecord。"""
    headers = {"User-Agent": user_agent or "Silkworm/0.1.0"}

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

    content_hash = compute_content_hash(content)
    html_path = save_raw_html(cocoon_dir, url, content)

    return PageRecord(
        url=url,
        canonical_url=str(resp.url),
        fetched_at=datetime.now(timezone.utc),
        content_hash=content_hash,
        http_status=http_status,
        raw_html_path=html_path,
        status="fetched",
    )
