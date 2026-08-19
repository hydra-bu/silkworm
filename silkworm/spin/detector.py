"""框架识别 — 基于指纹打分制检测文档框架。"""

from lxml import html

from silkworm.models import FingerprintRule, FrameworkProfile

# 内置框架指纹库
BUILTIN_FINGERPRINTS: dict[str, list[FingerprintRule]] = {
    "gitbook": [
        FingerprintRule(selector='meta[name="generator"]', value="gitbook", score=3),
        FingerprintRule(selector="div[class*='group/codeblock']", score=2),
        FingerprintRule(selector="script#_R_", score=1),
    ],
    "kubernetes": [
        FingerprintRule(selector='main[data-pagefind-body]', score=3),
        FingerprintRule(selector=".td-content", score=2),
    ],
    "mkdocs-material": [
        FingerprintRule(selector='meta[name="generator"]', value="mkdocs", score=3),
        FingerprintRule(selector=".md-content", score=2),
        FingerprintRule(selector=".md-nav", score=1),
    ],
    "mkdocs": [
        FingerprintRule(selector='meta[name="generator"]', value="mkdocs", score=3),
        FingerprintRule(selector=".wy-nav-content", score=2),
    ],
    "docusaurus": [
        FingerprintRule(selector='meta[name="generator"]', value="docusaurus", score=3),
        FingerprintRule(selector="#__docusaurus", score=3),
        FingerprintRule(selector=".theme-doc-markdown", score=2),
    ],
    "sphinx": [
        FingerprintRule(selector=".rst-content", score=2),
        FingerprintRule(selector='div[role="navigation"].sphinxsidebar', score=2),
        FingerprintRule(selector=".documentwrapper", score=1),
    ],
}

# 内置框架 Profile
BUILTIN_PROFILES: dict[str, FrameworkProfile] = {
    "mkdocs-material": FrameworkProfile(
        name="mkdocs-material",
        main_selector=".md-content",
        remove_selectors=[".md-nav", ".md-header", ".md-footer"],
        boilerplate_phrases=[
            "Edit this page",
            "Was this page helpful?",
            "Last update:",
            "Copyright",
        ],
        code_lang_class_map={
            "language-python": "python",
            "language-javascript": "javascript",
            "language-typescript": "typescript",
            "language-go": "go",
            "language-rust": "rust",
            "language-bash": "bash",
            "language-shell": "bash",
            "language-yaml": "yaml",
            "language-json": "json",
            "language-html": "html",
            "language-css": "css",
        },
    ),
    "mkdocs": FrameworkProfile(
        name="mkdocs",
        main_selector=".wy-nav-content",
        remove_selectors=[".wy-nav-side", ".wy-breadcrumbs"],
        boilerplate_phrases=["Edit on GitHub", "Copyright"],
        code_lang_class_map={
            "language-python": "python",
            "language-javascript": "javascript",
        },
    ),
    "docusaurus": FrameworkProfile(
        name="docusaurus",
        main_selector=".theme-doc-markdown, article",
        remove_selectors=[".theme-doc-sidebar-container", ".theme-doc-footer"],
        boilerplate_phrases=[
            "Edit this page",
            "Was this helpful?",
            "Last updated",
        ],
        code_lang_class_map={
            "language-python": "python",
            "language-js": "javascript",
            "language-javascript": "javascript",
            "language-ts": "typescript",
            "language-typescript": "typescript",
            "language-bash": "bash",
            "language-shell": "bash",
        },
    ),
    "sphinx": FrameworkProfile(
        name="sphinx",
        main_selector=".documentwrapper .section, .body",
        remove_selectors=[".sphinxsidebar", ".related", ".footer"],
        boilerplate_phrases=["Edit on GitHub", "Copyright"],
        code_lang_class_map={},
    ),
    "kubernetes": FrameworkProfile(
        name="kubernetes",
        main_selector="main[data-pagefind-body], .td-content",
        remove_selectors=["nav", ".navbar", "footer", ".footer", ".portal-button"],
        boilerplate_phrases=[
            "Was this page helpful?",
            "Thanks for the feedback",
            "Last modified",
            "Open an issue in the GitHub Repository",
        ],
        code_lang_class_map={
            "language-python": "python",
            "language-javascript": "javascript",
            "language-typescript": "typescript",
            "language-go": "go",
            "language-rust": "rust",
            "language-bash": "bash",
            "language-shell": "bash",
            "language-yaml": "yaml",
            "language-json": "json",
            "language-html": "html",
            "language-css": "css",
        },
    ),
    "gitbook": FrameworkProfile(
        name="gitbook",
        # GitBook 官方为每个页面提供 markdown 端点（URL 追加 .md），
        # 优先使用官方源可避免 RSC 客户端岛导致的正文/代码块丢失。
        markdown_suffixes=[".md"],
        main_selector="main, article",
        remove_selectors=[
            "nav", "header", "footer", "aside",
            ".breadcrumb", "[aria-label='breadcrumb']",
            "div.sr-only",
        ],
        boilerplate_phrases=[
            "For the complete documentation index",
            "Last updated",
            "Previous", "Next",
            "Edit this page",
            "Was this helpful?",
        ],
        markdown_strip_headings=["Agent Instructions"],
        code_lang_class_map={},
    ),
    "generic": FrameworkProfile(
        name="generic",
        main_selector="main, article, .content, #content, .documentation",
        remove_selectors=[
            "nav", ".nav", ".sidebar", "#fern-sidebar", "header", "footer",
            ".breadcrumb", "[aria-label='breadcrumb']",
        ],
        boilerplate_phrases=[
            "Was this useful?",
            "Was this helpful?",
            "Was this page helpful?",
            "Open issue",
            "Edit this page",
            "Last modified",
            "Feedback",
        ],
        code_lang_class_map={},
    ),
}


SCORE_THRESHOLD = 4


def detect_framework(html_content: str) -> str:
    """基于 HTML 内容检测文档框架，返回框架名称。"""
    try:
        tree = html.fromstring(html_content)
    except Exception:
        return "generic"

    scores: dict[str, int] = {}
    for fw_name, rules in BUILTIN_FINGERPRINTS.items():
        total = 0
        for rule in rules:
            elements = tree.cssselect(rule.selector)
            if not elements:
                continue
            if rule.value is not None:
                for el in elements:
                    content_attr = el.get("content", "")
                    if rule.value.lower() in content_attr.lower():
                        total += rule.score
                        break
            else:
                total += rule.score
        if total > 0:
            scores[fw_name] = total

    if not scores:
        return "generic"

    best = max(scores, key=lambda k: scores[k])
    if scores[best] >= SCORE_THRESHOLD:
        return best

    return "generic"


def get_profile(framework: str) -> FrameworkProfile:
    """获取框架对应的 Profile，未知框架返回 generic。"""
    return BUILTIN_PROFILES.get(framework, BUILTIN_PROFILES["generic"])
