"""清洗管线 Filter — 每个 Filter 独立、可单测、可插拔。"""

import html as html_mod
import re
from typing import Callable

from lxml import html as lxml_html
from lxml.html import HtmlElement
from markdownify import markdownify as md_convert

from silkworm.models import FrameworkProfile

FilterFunc = Callable[[str, FrameworkProfile], str]

# 模块级代码块占位符存储 — 用于 protect → markdownify → restore 模式
_code_block_store: dict[str, str] = {}
# 前缀不含 _，避免 markdownify 转义导致 placeholder 无法恢复
_CODECOOK_PREFIX = "%%%%SILKWORMCODEBLOCK"


def filter_main_content(raw_html: str, profile: FrameworkProfile) -> str:
    """Filter 1: 主区域提取 — 按 selector 提取，trafilatura 兜底。"""
    try:
        tree = lxml_html.fromstring(raw_html)
    except Exception:
        return raw_html

    selectors = [s.strip() for s in profile.main_selector.split(",") if s.strip()]
    for selector in selectors:
        elements = tree.cssselect(selector)
        if elements:
            content = lxml_html.tostring(elements[0], encoding="unicode")
            return content

    try:
        import trafilatura
        result = trafilatura.extract(raw_html, output_format="html")
        if result:
            return result
    except ImportError:
        pass

    return raw_html


def filter_nav_remnants(html_content: str, profile: FrameworkProfile) -> str:
    """Filter 2: 残留导航兜底清除。"""
    try:
        tree = lxml_html.fromstring(html_content)
    except Exception:
        return html_content

    for selector in profile.remove_selectors:
        for el in tree.cssselect(selector):
            el.drop_tree()

    return lxml_html.tostring(tree, encoding="unicode")


def filter_boilerplate(html_content: str, profile: FrameworkProfile) -> str:
    """Filter 3: 样板文字清除。"""
    if not profile.boilerplate_phrases:
        return html_content

    result = html_content
    for phrase in profile.boilerplate_phrases:
        result = result.replace(phrase, "")
    return result


def filter_scripts(html_content: str, _profile: FrameworkProfile) -> str:
    """Filter 4: 脚本/嵌入清除。"""
    try:
        tree = lxml_html.fromstring(html_content)
    except Exception:
        return html_content

    for tag in ("script", "style", "noscript", "iframe", "svg", "button", "title"):
        for el in tree.cssselect(tag):
            el.drop_tree()

    return lxml_html.tostring(tree, encoding="unicode")


def filter_tab_content(html_content: str, _profile: FrameworkProfile) -> str:
    """Filter 5: 标记 tab 面板 — Bootstrap .nav-tabs + .tab-content 结构检测。

    提取 tab 按钮文字作为标签插入对应面板前，然后移除按钮条。
    使 Linux/Windows 等切换内容在 Markdown 中可区分。"""
    try:
        tree = lxml_html.fromstring(html_content)
    except Exception:
        return html_content

    for nav_tabs in tree.cssselect(".nav-tabs"):
        tab_labels: list[str] = []
        for btn in nav_tabs.cssselect("[role='tab'], button, .nav-link"):
            text = btn.text_content().strip()
            if text:
                tab_labels.append(text)
        if not tab_labels:
            continue

        tab_content = nav_tabs.getnext()
        while tab_content is not None and not (
            tab_content.tag == "div"
            and "tab-content" in (tab_content.get("class", "") or "").split()
        ):
            tab_content = tab_content.getnext()
        if tab_content is None:
            continue

        panels = tab_content.cssselect("[role='tabpanel'], .tab-pane")
        for i, panel in enumerate(panels):
            if i < len(tab_labels) and tab_labels[i]:
                label_el = lxml_html.fromstring(
                    f"<p><strong>{tab_labels[i]}:</strong></p>"
                )
                panel.insert(0, label_el)
        nav_tabs.drop_tree()

    return lxml_html.tostring(tree, encoding="unicode")


_CODE_BLOCK_PATTERN = re.compile(
    r'<pre([^>]*)>(.*?)</pre>', re.DOTALL | re.IGNORECASE
)
# 匹配 HTML 文本中的 fenced code block（```lang\ncode\n```），用于保护非 <pre>/<code> 中的代码
_FENCED_CODE_PATTERN = re.compile(
    r'```(\S*)\s*\n(.*?)```', re.DOTALL
)


def _map_code_language(class_attr: str, profile: FrameworkProfile) -> str:
    """根据 class 属性映射代码语言。"""
    classes = class_attr.split()
    for cls in classes:
        if cls in profile.code_lang_class_map:
            return profile.code_lang_class_map[cls]
    return ""


def _extract_code_text(code_el: HtmlElement) -> str:
    """Extract code text preserving line breaks and indentation.

    Uses DOM structure inference to handle multiple code block formats
    without relying on framework-specific class names:

    1. Line-level spans (code-line, token-line, etc.):
       Each direct child <span> contains a complete line of code.
       Detected by: joining child texts with \\n matches text_content()
       when both are normalized (\\n removed), meaning child .tail
       contains no significant interleaving text.

    2. Token-level spans (highlight.js, etc.):
       Children are syntactic tokens within a line. \\n-joined children
       DON'T match text_content() because inter-token whitespace lives
       in .tail attributes and is captured by text_content().

    3. <br/>-separated lines (simple HTML): fallback.
    """
    children = list(code_el)
    if children:
        child_texts = []
        for child in children:
            if child.tag == 'br':
                continue  # <br/> is a line separator, not content
            text = child.text_content().rstrip('\n\r')
            child_texts.append(text)

        if child_texts:
            line_joined = '\n'.join(child_texts)
            flat = code_el.text_content()
            # Key signal: if children segment the text into complete lines,
            # their concatenation (with \\n removed) equals flat (with \\n removed).
            # If children are token-level, the .tail text (spaces between
            # tokens) appears in flat but not in line_joined, causing mismatch.
            if line_joined.replace('\n', '') == flat.replace('\n', ''):
                return line_joined

    # Fallback: convert <br/> to newlines
    raw = lxml_html.tostring(code_el, encoding="unicode")
    raw = re.sub(r"^<code[^>]*>(.*)</code>$", r"\1", raw, flags=re.DOTALL)
    raw = re.sub(r"<br\s*/?>", "\n", raw)
    plain = re.sub(r"<[^>]+>", "", raw)
    plain = html_mod.unescape(plain)
    return plain


def _make_fenced_block(lang: str, code_text: str) -> str:
    """Build a fenced code block, choosing a ``~~~`` delimiter when the code
    text itself contains a ``` triple-backtick so the inner fence does not
    break the outer Markdown fence (e.g. an MD tutorial page showing Python
    code inside a code block)."""
    fence = "~~~" if "```" in code_text else "```"
    return f"{fence}{lang}\n{code_text}\n{fence}"


def filter_protect_code(html_content: str, _profile: FrameworkProfile) -> str:
    """保护代码块：用占位符替换 <pre>，防止 md_convert 破坏代码文本。

    新排序: protect → md_convert (markdownify) → restore。
    避免 markdownify 对代码块内 _ 转义，同时正确处理
    Fern `<table>` 结构换行。
    """
    global _code_block_store
    _code_block_store = {}

    # 包装在已知容器中，避免 lxml.fromstring 返回根元素本身的序列化问题
    wrapped = f"<div class='__sw_wrap'>{html_content}</div>"
    try:
        tree = lxml_html.fromstring(wrapped)
    except Exception:
        return html_content

    idx = [0]  # mutable counter across both loops

    for pre in list(tree.cssselect("pre")):
        # Skip <pre> elements nested inside another <pre>.
        # After filter_line_numbered_code reconstructs <pre><code> from Fern
        # tables, the new <pre> may end up inside a stale ancestor <pre>
        # (lxml re-parents <table> out of <pre>, but the wrapper div chain
        # remains). Processing both produces duplicate code in the output.
        walk = pre.getparent()
        while walk is not None:
            if walk.tag == "pre":
                break
            walk = walk.getparent()
        if walk is not None:
            continue

        placeholder = f"{_CODECOOK_PREFIX}{idx[0]}%%"
        lang = _detect_code_language(pre)
        code_text = _extract_code_text_preserving_lines(pre)
        if lang in ("bash", "sh", "shell", "zsh"):
            code_text = re.sub(r"^[$%]\s*", "", code_text, flags=re.MULTILINE)
        md_block = _make_fenced_block(lang, code_text.strip())
        _code_block_store[placeholder] = md_block
        span = lxml_html.Element("span")
        span.text = placeholder
        pre.getparent().replace(pre, span)
        idx[0] += 1

    # Also protect standalone <code> elements not inside <pre>
    # (some frameworks — e.g. Fern — use <code> without wrapping <pre>)
    # IMPORTANT: Only protect <code> inside block-level containers (div, td, th).
    # Inline <code> inside phrasing elements (p, li, h1-6) must NOT be protected,
    # otherwise inline code like `<code>docker-compose</code>` in a <p> gets
    # incorrectly turned into a fenced code block instead of `inline backtick`.
    _PHRASING_PARENTS = frozenset({
        "p", "h1", "h2", "h3", "h4", "h5", "h6",
        "li", "a", "span", "strong", "em", "b", "i", "u",
        "small", "sub", "sup", "label", "button",
        "figcaption", "dt", "dd",
    })
    for code in tree.cssselect("code"):
        parent = code.getparent()
        if parent is not None and parent.tag in _PHRASING_PARENTS:
            # This is inline code inside a phrasing element — skip,
            # let markdownify handle it as `inline backtick`
            continue
        # Any <code> nested inside a <td>/<th> must render as inline
        # `backtick`, never a fenced block. A fenced block inside a markdown
        # table cell is invalid and destroys the surrounding row (e.g.
        # <td><code>apiVersion</code></td> or a multi-line <code> in a cell).
        node = parent
        _in_cell = False
        while node is not None:
            if node.tag in ("td", "th"):
                _in_cell = True
                break
            node = node.getparent()
        if _in_cell:
            continue
        # Inline <code> (single line, e.g. a code token inside a table cell
        # like <td><code>EventListener</code></td>) must render as `inline
        # backtick`, NOT a fenced block — otherwise the surrounding markdown
        # table row is destroyed. Only protect multi-line <code> (block-level
        # code without a <pre> wrapper that markdownify would otherwise mangle
        # by collapsing newlines to spaces).
        if "\n" not in (code.text_content() or ""):
            continue
        # Skip if this <code> is inside a <pre> (already handled above)
        inside_pre = False
        walk = parent
        while walk is not None:
            if walk.tag == "pre":
                inside_pre = True
                break
            walk = walk.getparent()
        if inside_pre:
            continue

        placeholder = f"{_CODECOOK_PREFIX}{idx[0]}%%"
        code_text = code.text_content().strip()
        md_block = _make_fenced_block("", code_text)
        _code_block_store[placeholder] = md_block
        span = lxml_html.Element("span")
        span.text = placeholder
        code.getparent().replace(code, span)
        idx[0] += 1

    result = lxml_html.tostring(tree, encoding="unicode")
    # 移除包装容器
    result = result.removeprefix('<div class="__sw_wrap">').removesuffix("</div>")

    # Step 2: Regex protection for fenced code blocks in text content
    # Some frameworks (e.g. Fern) render code as raw `` ``` `` blocks inside
    # plain <div> text nodes rather than <pre><code> elements.
    # These would not be caught by the lxml step above, so we catch them here.
    def _fence_replacer(m: re.Match) -> str:
        placeholder = f"{_CODECOOK_PREFIX}_txt_{idx[0]}%%"
        lang = m.group(1) or ""
        code = m.group(2)
        md_block = _make_fenced_block(lang, code)
        _code_block_store[placeholder] = md_block
        idx[0] += 1
        return placeholder

    result = _FENCED_CODE_PATTERN.sub(_fence_replacer, result)
    return result


def filter_restore_code(md_content: str, _profile: FrameworkProfile) -> str:
    """恢复代码块：将占位符替换回格式化的 Markdown 代码块。

    特殊处理：如果占位符出现在列表项中（由 3 空格缩进标识），
    将代码块移到行首（0 缩进），使围栏标记 ``` 位于行首，
    而非嵌套在列表项内。
    """
    global _code_block_store
    result = md_content
    for placeholder, code_block in _code_block_store.items():
        # Placeholder indented as a list-item continuation line. markdownify
        # uses 2 spaces for unordered / 3 for ordered items, and deeper
        # indentation for nested lists. Move the code block to column 0 so the
        # fence marker is at line start regardless of list depth or style.
        pattern = re.compile(r'^[ \t]+' + re.escape(placeholder) + r'$', re.MULTILINE)
        result = pattern.sub(lambda m: code_block, result)
        # Handle any remaining placeholders not in list context.
        # Wrap with blank lines on both sides so the restored fence is a
        # proper block: otherwise the closing ``` glues to adjacent text
        # (e.g. an admonition label like **Note**) and corrupts the block.
        result = result.replace(placeholder, f"\n\n{code_block}\n\n")
    _code_block_store = {}
    # Collapse excessive blank lines that the wrapping above may create.
    result = re.sub(r'\n{3,}', '\n\n', result)
    # Ensure every closing fence is followed by a blank line, so adjacent
    # text (e.g. **Note**) doesn't glue to the fence.
    # This covers edge cases where the placeholder was indented (list item)
    # and replaced without the \n\n wrapping above.
    result = re.sub(r'(?<=```)\n(?=[^\n])', r'\n\n', result)
    result = re.sub(r'(?<=~~~)\n(?=[^\n])', r'\n\n', result)
    return result


def _detect_code_language(pre: HtmlElement) -> str:
    """从 pre / code / 父元素 class 检测代码语言。"""
    for cls in pre.classes:
        if cls.startswith("language-"):
            return cls[len("language-"):]
    code = pre.cssselect("code")
    if code:
        for cls in code[0].classes:
            if cls.startswith("language-"):
                return cls[len("language-"):]
    parent = pre.getparent()
    if parent is not None:
        for cls in parent.classes:
            if cls.startswith("language-"):
                return cls[len("language-"):]
    return ""


def _extract_code_text_preserving_lines(pre: HtmlElement) -> str:
    """从 <pre> 提取代码文本，对 Fern `<table>` 结构保留换行。"""
    table = pre.cssselect("table")
    if table:
        lines = []
        for tr in table[0].cssselect("tr"):
            tds = tr.cssselect(".code-block-line-content, td:last-child")
            if tds:
                lines.append(tds[0].text_content().rstrip('\n'))
        return "\n".join(lines)
    code = pre.cssselect("code")
    if code:
        return _extract_code_text(code[0])
    return pre.text_content()


def _inside_table_cell(node: HtmlElement) -> bool:
    """Return True if *node* is nested anywhere inside a <td>/<th>."""
    walk = node.getparent()
    while walk is not None:
        if walk.tag in ("td", "th"):
            return True
        walk = walk.getparent()
    return False


def filter_remove_code_lang_labels(html_content: str, _profile: FrameworkProfile) -> str:
    """Filter: Remove .code-language-label elements from code blocks.

    Some frameworks (e.g. Fern) render <Tabs> with a language label
    (bash, powershell, etc.) as <span class="code-language-label"> above
    each code block. These labels leak into Markdown as stray plain text.
    Must run BEFORE protect_code so the labels are gone before markdownify.
    """
    try:
        tree = lxml_html.fromstring(html_content)
    except Exception:
        return html_content

    for el in tree.cssselect(".code-language-label"):
        el.drop_tree()

    return lxml_html.tostring(tree, encoding="unicode")


def filter_flatten_nested_tables(html_content: str, _profile: FrameworkProfile) -> str:
    """Filter 6b: a <table> nested inside a <td>/<th> cannot be represented in
    markdown — markdownify inlines it and corrupts the outer row (e.g. leaking
    '| --- | --- |' separators into the cell). Flatten each nested table into a
    single-line '; '-joined list of its rows so the outer table stays valid and
    the sub-field info is preserved inline."""
    tree = lxml_html.fromstring(html_content)
    for nested in tree.cssselect("table"):
        parent = nested.getparent()
        if parent is None or not _inside_table_cell(nested):
            continue
        rows: list[str] = []
        for tr in nested.cssselect("tr"):
            cells = [c.text_content().strip() for c in tr.cssselect("td, th")]
            cells = [c for c in cells if c]
            if cells:
                rows.append(" ".join(cells))
        span = lxml_html.Element("span")
        span.text = "; ".join(rows)
        parent.replace(nested, span)
    return lxml_html.tostring(tree, encoding="unicode")


def filter_flatten_block_in_cells(html_content: str, _profile: FrameworkProfile) -> str:
    """Filter: a <pre> (block code) nested inside a <td>/<th> cannot be fenced in
    markdown — markdownify emits a ``` block that corrupts the outer table row
    (e.g. a CEL-function example column). Flatten each such <pre> into an inline
    <code> with internal whitespace collapsed to single spaces, so the cell
    renders as valid inline code and the outer table stays intact. Must run
    BEFORE filter_protect_code so the cell <pre> becomes inline <code> instead of
    a fenced placeholder."""
    tree = lxml_html.fromstring(html_content)
    for pre in tree.cssselect("pre"):
        if not _inside_table_cell(pre):
            continue
        code = lxml_html.Element("code")
        text = pre.text_content().strip()
        code.text = re.sub(r"\s+", " ", text)
        parent = pre.getparent()
        if parent is not None:
            parent.replace(pre, code)
    return lxml_html.tostring(tree, encoding="unicode")


def filter_html_to_md(html_content: str, _profile: FrameworkProfile) -> str:
    """Filter 6: HTML → Markdown 转换。"""
    return md_convert(
        html_content,
        heading_style="atx",
        bullets="-",
        strip=[],
    )


def filter_localize_assets(
    html_content: str, _profile: FrameworkProfile
) -> str:
    """Filter 7: 资源本地化 — 图片下载/链接重写（占位实现）。"""
    return html_content


_CODE_GUTTER_SELECTORS = [
    ".code-block-line-gutter",  # Fern table-based code blocks
    "td.gutter",                # Generic gutter column
    ".linenos",                 # Sphinx
    ".line-number",             # Common convention
    ".code-line-row-number",    # Hugo/Docsy
]


def _is_fern_code_table(tbl: HtmlElement) -> bool:
    """Check if a <table> is a Fern line-numbered code block."""
    if "code-block-line-group" in (tbl.get("class", "") or "").split():
        return True
    if tbl.cssselect(".code-block-line-content"):
        return True
    return False


def _detect_table_code_language(tbl: HtmlElement) -> str:
    """Detect language from table's ancestor chain or preceding <pre> sibling."""
    parent: HtmlElement | None = tbl.getparent()
    while parent is not None:
        for cls in parent.classes:
            if cls.startswith("language-"):
                return cls[len("language-"):]
        parent = parent.getparent()
    return ""


def filter_line_numbered_code(html_content: str, _profile: FrameworkProfile) -> str:
    """Filter: Strip line number / gutter elements from code blocks.

    Many doc frameworks (Fern, Sphinx, Hugo) render code with line numbers
    in separate HTML elements. These are UI decorations that leak into the
    Markdown output. This filter strips them before code block conversion.
    """
    try:
        tree = lxml_html.fromstring(html_content)
    except Exception:
        return html_content

    for selector in _CODE_GUTTER_SELECTORS:
        for el in tree.cssselect(selector):
            el.drop_tree()

    # Reconstruct Fern-style code blocks from tables into proper <pre><code>.
    # lxml moves <table> out of <pre> during parsing because the HTML spec
    # restricts <pre> to phrasing content only — <table> is flow content.
    # The resulting DOM varies across frameworks:
    #   - Simple: <pre></pre><table>...</table>
    #   - NVIDIA: <div.language-python><pre/><pre/><div.fern-scroll-area><table...>...</div>
    # Here we detect ALL such tables across the tree (not just pre siblings),
    # extract code text preserving leading whitespace, detect language from
    # ancestor chain, build valid <pre><code>, and remove orphaned empties.
    for tbl in list(tree.cssselect("table")):
        if not _is_fern_code_table(tbl):
            continue

        lang = _detect_table_code_language(tbl)

        lines: list[str] = []
        for tr in tbl.cssselect("tr"):
            tds = tr.cssselect("td:last-child")
            if tds:
                lines.append(tds[0].text_content().rstrip('\n'))

        if not lines:
            continue

        code_text = "\n".join(lines)

        new_pre = lxml_html.Element("pre")
        lang_cls = f"language-{lang}" if lang else "code-block-line-group"
        new_pre.set("class", f"code-block-line-group {lang_cls}")
        code_el = lxml_html.Element("code")
        if lang:
            code_el.set("class", lang_cls)
        code_el.text = code_text
        new_pre.append(code_el)

        parent = tbl.getparent()
        if parent is None:
            continue
        if parent.tag == "div" and "fern-scroll-area" in (parent.get("class", "") or "").split():
            pp = parent.getparent()
            if pp is not None:
                pp.replace(parent, new_pre)
            else:
                parent.replace(tbl, new_pre)
        else:
            parent.replace(tbl, new_pre)

    for pre in list(tree.cssselect("pre")):
        if len(pre) == 0 and (not pre.text or not pre.text.strip()):
            p = pre.getparent()
            if p is not None:
                p.remove(pre)

    return lxml_html.tostring(tree, encoding="unicode")


_COPYCODE_PATTERN = re.compile(
    r'!\[[^\]]*\]\([^)]*copycode[^)]*\)',
    re.IGNORECASE,
)
_FEEDBACK_PATTERN = re.compile(
    r'## Feedback\n{1,2}Yes\n{1,2}No\n{1,2}.*?(?=\n#{1,6} |\Z)',
    re.DOTALL,
)
_NOTE_BLOCK_PATTERN = re.compile(
    r'^#{4,6}\s+(Note|Caution|Warning|Important|Tip)(\s|:|$)?',
    re.MULTILINE | re.IGNORECASE,
)
_FEATURE_STATE_PATTERN = re.compile(
    r'^FEATURE STATE:\n(.+)$',
    re.MULTILINE,
)
# Broad emoji cleanup — Unicode emoji blocks (decorative only, not semantic)
_EMOJI_PATTERN = re.compile(
    '['
    '\U0001F300-\U0001F9FF'     # Misc Symbols, Emoticons, Transport, Supplemental
    '\U0001FA00-\U0001FAFF'     # Chess, Symbols Extended-A
    '\u2600-\u27BF'              # Misc symbols, Dingbats
    '\uFE0F'                     # Variation selector (emoji presentation)
    '\u2139'                     # ℹ info
    ']'
)
_ANCHOR_LINK = re.compile(r'\[#\]\(#[^)]*\)')  # Docusaurus anchor noise: [#](#section)
_ON_THIS_PAGE_PATTERN = re.compile(
    r'^#{1,4}\s+On this page\s*\n[\s\S]*?(?=\n#{1,4}\s|\Z)',
    re.MULTILINE | re.IGNORECASE,
)


def filter_overrides(md_content: str, _profile: FrameworkProfile) -> str:
    """Apply heuristic text overrides, but never touch the contents of fenced
    code blocks (``` or ~~~). Stripping emoji, copy-code links, feedback
    prompts, etc. inside code would corrupt the source being documented."""
    lines = md_content.split('\n')
    segments: list[tuple[bool, list[str]]] = []
    cur_is_code = False
    cur_lines: list[str] = []
    for line in lines:
        if re.match(r'\s*(```|~~~)', line):
            segments.append((cur_is_code, cur_lines))
            cur_lines = []
            cur_is_code = not cur_is_code
        cur_lines.append(line)
    segments.append((cur_is_code, cur_lines))

    out: list[str] = []
    for is_code, seg_lines in segments:
        seg = '\n'.join(seg_lines)
        if is_code:
            out.append(seg)
            continue
        seg = _COPYCODE_PATTERN.sub("", seg)
        seg = _FEEDBACK_PATTERN.sub("", seg)
        seg = _NOTE_BLOCK_PATTERN.sub(lambda m: f"> **{m.group(1).capitalize()}:**", seg)
        seg = _FEATURE_STATE_PATTERN.sub(r"> **FEATURE STATE:** \1", seg)
        seg = _EMOJI_PATTERN.sub("", seg)
        seg = _ANCHOR_LINK.sub("", seg)
        # "On this page" may appear as heading (## On this page) or plain text (Fern <div>)
        seg = _ON_THIS_PAGE_PATTERN.sub("", seg)
        seg = re.sub(
            r'^On this page\s*\n[\s\S]*?(?=\n#{1,6}\s|\Z)',
            '',
            seg,
            flags=re.MULTILINE | re.IGNORECASE,
        )
        out.append(seg)

    return '\n'.join(out).strip()


def filter_unescape_code_underscores(md_content: str, _profile: FrameworkProfile) -> str:
    """Unescape \\_ to _ inside fenced code blocks in final Markdown.

    Some HTML-to-Markdown conversion paths (e.g. untagged <code> inside inline
    phrasing elements) can leak escaped underscores into fenced code blocks.
    Since code blocks render literally, \\_ is never needed — but it produces
    visible backslashes in the output. This filter strips them.
    """
    lines = md_content.split('\n')
    result: list[str] = []
    in_code_block = False

    for line in lines:
        if line.startswith('```'):
            in_code_block = not in_code_block
            result.append(line)
        elif in_code_block:
            result.append(line.replace('\\_', '_'))
        else:
            result.append(line)

    return '\n'.join(result)


def filter_escape_cell_pipes_html(html_content: str, _profile: FrameworkProfile) -> str:
    r"""Escape literal '|' inside table cells at the HTML stage.

    markdownify never escapes a '|' inside table-cell content, so values such as
    union types `` `("a" | "b")` ``, CLI placeholders `` `<env|convex>` ``, the
    logical-OR `` `||` `` or `` `[ continue | stopAndFail ]` `` are parsed as column
    separators, corrupting the table (column-count mismatch).

    Fixing this *after* HTML→MD conversion is fragile: in Markdown there is no
    reliable way to tell a literal '|' inside a cell from a genuine column
    separator. So we escape here, while cell boundaries are still unambiguous in
    the DOM. We replace '|' with a backslash-escaped `` \| `` in non-code cell
    text. lxml leaves `` \| `` intact (it is not an HTML entity, so the serializer
    does not decode it), and markdown renders `` \| `` as a literal pipe instead
    of treating it as a column separator. We deliberately skip text inside
    <code>/<pre>: a '|' there is already safe inside a fenced/inline code span,
    and escaping it would corrupt the code.
    """
    tree = lxml_html.fromstring(html_content)
    for cell in tree.cssselect("td, th"):
        for node in cell.iter():
            # node.text belongs to node itself — skip if it is code/pre
            if node.tag not in ("code", "pre") and node.text and "|" in node.text:
                node.text = node.text.replace("|", "\\|")
            # node.tail is the text after node closes (belongs to node's parent) —
            # escape it unless the parent is code/pre
            parent = node.getparent()
            if (
                parent is not None
                and parent.tag not in ("code", "pre")
                and node.tail
                and "|" in node.tail
            ):
                node.tail = node.tail.replace("|", "\\|")
    return lxml_html.tostring(tree, encoding="unicode")


# 管线定义：按顺序执行的 Filter 列表
# 顺序说明: protect → md_convert → restore 模式确保 markdownify 不破坏代码块
CLEANING_PIPELINE: list[tuple[str, FilterFunc]] = [
    ("main_content", filter_main_content),
    ("nav_remnants", filter_nav_remnants),
    ("boilerplate", filter_boilerplate),
    ("scripts", filter_scripts),
    ("tab_content", filter_tab_content),
    ("line_numbers", filter_line_numbered_code),
    ("remove_code_lang_labels", filter_remove_code_lang_labels),
    ("flatten_block_in_cells", filter_flatten_block_in_cells),
    ("escape_cell_pipes_html", filter_escape_cell_pipes_html),
    ("protect_code", filter_protect_code),
    ("flatten_nested_tables", filter_flatten_nested_tables),
    ("html_to_md", filter_html_to_md),
    ("restore_code", filter_restore_code),
    ("localize_assets", filter_localize_assets),
    ("overrides", filter_overrides),
    ("unescape_code_underscores", filter_unescape_code_underscores),
]


def run_pipeline(
    raw_html: str, profile: FrameworkProfile, skip: set[str] | None = None
) -> tuple[str, list[str]]:
    """执行完整清洗管线，返回 (最终内容, 已执行步骤列表)。"""
    skip = skip or set()
    content = raw_html
    steps: list[str] = []

    for name, func in CLEANING_PIPELINE:
        if name in skip:
            continue
        content = func(content, profile)
        steps.append(name)

    return content, steps
