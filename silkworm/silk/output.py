"""Silk 输出 — Markdown 落盘 + Frontmatter + 目录组织。"""

from datetime import datetime, timezone
from pathlib import Path

from silkworm.models import PageRecord

_DEFAULT_FRONTMATTER_TEMPLATE = """---
source_url: {source_url}
fetched_at: {fetched_at}
content_hash: {content_hash}
framework: {framework}
---
"""


def build_frontmatter(record: PageRecord) -> str:
    """构建 Markdown 文件头 frontmatter。"""
    fetched_str = (
        record.fetched_at.isoformat()
        if record.fetched_at
        else datetime.now(timezone.utc).isoformat()
    )
    return _DEFAULT_FRONTMATTER_TEMPLATE.format(
        source_url=record.url,
        fetched_at=fetched_str,
        content_hash=record.content_hash or "",
        framework=record.framework or "unknown",
    )


def resolve_output_path(
    silk_dir: Path, site_name: str, url: str
) -> Path:
    """根据 URL 确定输出文件路径，镜像站点导航层级。"""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    original_path = parsed.path

    if not original_path or original_path == "/":
        return silk_dir / site_name / "index.md"

    path = original_path.rstrip("/")

    # 剥离端点后缀：官方 markdown 端点（X.md）与 HTML 端点（X.html）
    # 都对应同一页面 X，统一输出为 X.md，避免 X.md.md 重复路径
    if path.endswith(".html"):
        path = path[:-5]
    elif path.endswith(".md"):
        path = path[:-3]
    elif original_path.endswith("/"):
        path = path + "/index"

    path = path.lstrip("/")
    return silk_dir / site_name / f"{path}.md"


def write_silk(
    silk_dir: Path,
    site_name: str,
    record: PageRecord,
    markdown_content: str,
) -> Path:
    """将 Markdown 内容输出到 silk 目录。"""
    output_path = resolve_output_path(silk_dir, site_name, record.url)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    frontmatter = build_frontmatter(record)
    output_path.write_text(
        frontmatter + markdown_content, encoding="utf-8"
    )

    return output_path
