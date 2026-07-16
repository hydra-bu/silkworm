"""导航区域 HTML 提取 — 从页面中找出导航容器。"""

import re

from lxml import html

# 常见文档框架的导航容器选择器（优先级从高到低）
NAV_SELECTORS = [
    ".td-sidebar",                     # k8s (Hugo/docsy)
    "#td-sidebar",
    ".sidebar",
    "#sidebar",
    ".navigation",
    "#navigation",
    "nav.sidebar",
    "nav.docs-sidebar",
    ".docs-sidebar",
    ".menu",
    "#menu",
    "aside",
    'nav[role="navigation"]',
    ".md-nav",                         # mkdocs-material
    ".wy-nav-side",                    # mkdocs
    ".theme-doc-sidebar-container",    # docusaurus
]

# LLM 不需要看到 class/style/data-* 等非语义属性 — 逐个声明而非取反以避免误伤
_KEPT_ATTRS = {"href", "src", "alt", "aria-label", "role", "title", "id"}
_STRIP_ATTR = re.compile(r'\s+(class|style|data-\w+|tabindex|aria-\w+)(="[^"]*")?', re.I)


def _strip_attrs(raw: str) -> str:
    """去掉导航 HTML 中 LLM 无关的属性，大幅缩小体积。"""
    lines = raw.split("\n")
    out: list[str] = []
    for line in lines:
        out.append(_STRIP_ATTR.sub("", line))
    return "\n".join(out)


def extract_nav_html(raw_html: str, url: str = "") -> str | None:
    """从页面 HTML 中提取导航容器内部的 HTML。

    返回导航容器的 inner HTML（｜None = 未找到）。
    """
    try:
        tree = html.fromstring(raw_html)
    except Exception:
        return None

    for selector in NAV_SELECTORS:
        elements = tree.cssselect(selector)
        if elements:
            inner = html.tostring(elements[0], encoding="unicode", method="html")
            inner = _strip_attrs(inner)
            if len(inner) > 50000:
                inner = inner[:50000] + "\n<!-- truncated -->"
            return inner

    # 兜底：找任何包含 ≥5 个 <a> 的 <nav>
    for nav in tree.cssselect("nav"):
        links = nav.cssselect("a")
        if len(links) >= 5:
            inner = html.tostring(nav, encoding="unicode", method="html")
            inner = _strip_attrs(inner)
            if len(inner) > 50000:
                inner = inner[:50000] + "\n<!-- truncated -->"
            return inner

    return None
