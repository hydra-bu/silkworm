"""URL 收集 — 从导航树展平为 URL 列表，支持 prompt 过滤 + 优先级排序。"""

import logging
from urllib.parse import urljoin

logger = logging.getLogger(__name__)


def flatten_nav_tree(
    tree: dict,
    base_url: str = "",
    prompt: str | None = None,
    exclude: list[str] | None = None,
) -> list[str]:
    """从导航树递归展平 URL 列表，core 优先于 secondary。

    Args:
        tree: analyze_nav_tree 返回的导航树 {sections: [...]}
        base_url: 用于相对 URL 拼接
        prompt: 可选，只返回匹配章节下的 URL（不区分大小写子串匹配）
        exclude: 可选，排除指定标题的章节（不区分大小写子串匹配）

    Returns:
        按 core → secondary 顺序排列的 URL 列表（core 在前）
    """
    sections = tree.get("sections", []) if isinstance(tree, dict) else []
    if not sections:
        return []

    if exclude:
        exclude_lower = [e.strip().lower() for e in exclude if e.strip()]
        sections = _exclude_sections(sections, exclude_lower)

    if prompt:
        prompt_lower = prompt.strip().lower()
        sections = _filter_sections_by_prompt(sections, prompt_lower)
        if not sections:
            logger.info("prompt '%s' 未匹配任何章节，降级全量", prompt)
            sections = tree.get("sections", [])

    core_urls: list[str] = []
    secondary_urls: list[str] = []
    for section in sections:
        _collect_urls_by_priority(section, core_urls, secondary_urls, base_url)
    return core_urls + secondary_urls


def _exclude_sections(sections: list[dict], exclude_lower: list[str]) -> list[dict]:
    """递归排除匹配标题的章节。"""
    kept: list[dict] = []
    for section in sections:
        title = (section.get("title") or "").lower()
        # 当前节点匹配排除规则 → 丢弃整个分支
        if any(excl in title for excl in exclude_lower):
            continue
        children = section.get("children", [])
        if children:
            filtered_children = _exclude_sections(children, exclude_lower)
            kept_section = dict(section)
            kept_section["children"] = filtered_children
            kept.append(kept_section)
        else:
            kept.append(section)
    return kept


def _filter_sections_by_prompt(
    sections: list[dict], prompt_lower: str
) -> list[dict]:
    """按 prompt 过滤章节（递归匹配 title）。"""
    matched: list[dict] = []
    for section in sections:
        title = (section.get("title") or "").lower()
        if prompt_lower in title:
            matched.append(section)
            continue
        children = section.get("children", [])
        if children:
            filtered_children = _filter_sections_by_prompt(children, prompt_lower)
            if filtered_children:
                matched_section = dict(section)
                matched_section["children"] = filtered_children
                matched.append(matched_section)
    return matched


def _collect_urls_by_priority(
    node: dict, core: list[str], secondary: list[str], base_url: str
) -> None:
    """递归收集 URL，按 priority 分别放入 core/secondary 列表。"""
    url = node.get("url", "")
    if url:
        absolute = urljoin(base_url, url)
        is_core = node.get("priority", "secondary") == "core"
        target = core if is_core else secondary
        if absolute not in target:
            target.append(absolute)

    for child in node.get("children", []):
        _collect_urls_by_priority(child, core, secondary, base_url)


def priority_stats(tree: dict, prompt: str | None = None, exclude: list[str] | None = None) -> dict:
    sections = tree.get("sections", []) if isinstance(tree, dict) else []
    if not sections:
        return {"core": 0, "secondary": 0}

    if exclude:
        exclude_lower = [e.strip().lower() for e in exclude if e.strip()]
        sections = _exclude_sections(sections, exclude_lower)

    if prompt:
        prompt_lower = prompt.strip().lower()
        sections = _filter_sections_by_prompt(sections, prompt_lower)
        if not sections:
            sections = tree.get("sections", [])

    counts = [0, 0]
    for section in sections:
        _count_priority(section, counts)
    return {"core": counts[0], "secondary": counts[1]}


def _count_priority(node: dict, counts: list) -> None:
    url = node.get("url", "")
    if url:
        if node.get("priority", "secondary") == "core":
            counts[0] += 1
        else:
            counts[1] += 1
    for child in node.get("children", []):
        _count_priority(child, counts)
