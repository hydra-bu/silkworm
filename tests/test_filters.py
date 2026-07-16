"""Filters 单元测试。"""

import re

from silkworm.models import FrameworkProfile
from silkworm.spin.filters import (
    _extract_code_text,
    _make_fenced_block,
    filter_boilerplate,
    filter_html_to_md,
    filter_line_numbered_code,
    filter_main_content,
    filter_nav_remnants,
    filter_overrides,
    filter_protect_code,
    filter_restore_code,
    filter_scripts,
    run_pipeline,
)


def _run_code_blocks(html: str, profile) -> str:
    """Helper: protect → md_convert → restore 序列模拟旧 filter_code_blocks。"""
    protected = filter_protect_code(html, profile)
    md = filter_html_to_md(protected, profile)
    return filter_restore_code(md, profile)

GENERIC_PROFILE = FrameworkProfile(name="generic")
MKDOCS_PROFILE = FrameworkProfile(
    name="mkdocs-material",
    main_selector=".md-content",
    remove_selectors=[".md-nav"],
    boilerplate_phrases=["Edit this page", "Was this page helpful?"],
    code_lang_class_map={"language-python": "python", "language-bash": "bash"},
)


class TestFilterMainContent:
    def test_extract_by_selector(self):
        html = '<html><body><div class="md-content"><p>Main</p></div><nav>Skip</nav></body></html>'
        result = filter_main_content(html, MKDOCS_PROFILE)
        assert "<p>Main</p>" in result
        assert "Skip" not in result

    def test_fallback_to_raw_when_no_selector_match(self):
        html = "<html><body><p>No match</p></body></html>"
        result = filter_main_content(html, GENERIC_PROFILE)
        assert "No match" in result


class TestFilterNavRemnants:
    def test_remove_nav_elements(self):
        html = '<html><body><div class="md-nav">Nav</div><article>Content</article></body></html>'
        result = filter_nav_remnants(html, MKDOCS_PROFILE)
        assert "Nav" not in result
        assert "Content" in result

    def test_no_remove_selectors_no_change(self):
        profile = FrameworkProfile(name="test")
        html = "<html><body><nav>Menu</nav><p>Text</p></body></html>"
        result = filter_nav_remnants(html, profile)
        assert "Menu" in result


class TestFilterBoilerplate:
    def test_remove_boilerplate_phrases(self):
        html = "<p>Edit this page on GitHub</p><p>Content</p>"
        result = filter_boilerplate(html, MKDOCS_PROFILE)
        assert "Edit this page" not in result
        assert "Content" in result

    def test_no_boilerplate_no_change(self):
        profile = FrameworkProfile(name="test")
        html = "<p>Clean content</p>"
        assert filter_boilerplate(html, profile) == html


class TestFilterScripts:
    def test_remove_script_and_style(self):
        html = '<html><head><style>.cls{}</style></head><body><script>alert(1)</script><p>Text</p></body></html>'
        result = filter_scripts(html, GENERIC_PROFILE)
        assert "script" not in result
        assert "style" not in result or ".cls" not in result
        assert "Text" in result

    def test_remove_noscript(self):
        html = "<html><body><noscript>Please enable JS</noscript><p>Content</p></body></html>"
        result = filter_scripts(html, GENERIC_PROFILE)
        assert "noscript" not in result.lower() or "Please enable JS" not in result


class TestFilterCodeBlocks:
    def test_map_known_language(self):
        html = '<pre><code class="language-python">print("hello")</code></pre>'
        result = _run_code_blocks(html, MKDOCS_PROFILE)
        assert "```python" in result
        assert 'print("hello")' in result

    def test_unknown_language_empty_fence(self):
        html = '<pre><code class="language-unknown">code</code></pre>'
        result = _run_code_blocks(html, MKDOCS_PROFILE)
        assert "```\ncode\n```" in result or "```" in result

    def test_no_code_block_no_change(self):
        html = "<p>Just text</p>"
        result = _run_code_blocks(html, MKDOCS_PROFILE)
        assert "Just text" in result


class TestExtractCodeText:
    def test_simple_preformatted(self):
        from lxml import html as lxml_html
        tree = lxml_html.fromstring("<code>line1\nline2\n  line3</code>")
        assert _extract_code_text(tree) == "line1\nline2\n  line3"

    def test_docusaurus_token_lines(self):
        from lxml import html as lxml_html
        html_str = '''<code><span class="token-line"><span class="token-keyword">apiVersion</span><span class="token-punctuation">:</span><span class="token-string"> apisix.apache.org/v2</span></span>
<span class="token-line"><span class="token-keyword">kind</span><span class="token-punctuation">:</span><span class="token-string"> ApisixRoute</span></span></code>'''
        tree = lxml_html.fromstring(html_str)
        result = _extract_code_text(tree)
        lines = result.split("\n")
        assert len(lines) == 2, f"Expected 2 lines, got {len(lines)}: {result!r}"
        assert "apiVersion: apisix.apache.org/v2" in lines[0]
        assert "kind: ApisixRoute" in lines[1]

    def test_code_with_html_entities(self):
        from lxml import html as lxml_html
        tree = lxml_html.fromstring("<code>&lt;script&gt;alert(1)&lt;/script&gt;</code>")
        result = _extract_code_text(tree)
        assert "<script>alert(1)</script>" in result

    def test_code_with_br_tags(self):
        from lxml import html as lxml_html
        tree = lxml_html.fromstring("<code>line1<br>line2<br/>line3</code>")
        result = _extract_code_text(tree)
        lines = [ln for ln in result.split("\n") if ln.strip()]
        assert len(lines) == 3


class TestFilterHtmlToMd:
    def test_convert_heading(self):
        html = "<h1>Title</h1>"
        result = filter_html_to_md(html, GENERIC_PROFILE)
        assert "# Title" in result

    def test_convert_paragraph(self):
        html = "<p>Hello <strong>world</strong></p>"
        result = filter_html_to_md(html, GENERIC_PROFILE)
        assert "Hello" in result
        assert "world" in result

    def test_convert_link(self):
        html = '<p>See <a href="https://example.com">our guide</a> for details.</p>'
        result = filter_html_to_md(html, GENERIC_PROFILE)
        assert "[our guide](https://example.com)" in result
        assert "See" in result

    def test_convert_image(self):
        html = '<p><img src="https://example.com/icon.png" alt="Icon"></p>'
        result = filter_html_to_md(html, GENERIC_PROFILE)
        assert "![Icon]" in result or "Icon" in result


class TestRunPipeline:
    def test_full_pipeline_execution(self):
        html = """<html><body>
            <div class="md-content">
                <h1>Title</h1>
                <p>Content here</p>
                <pre><code class="language-python">print("hi")</code></pre>
                <script>bad</script>
            </div>
            <nav>Skip me</nav>
        </body></html>"""

        content, steps = run_pipeline(html, MKDOCS_PROFILE)
        assert len(steps) > 0
        assert "Title" in content or "# Title" in content
        assert "script" not in content.lower() or "bad" not in content

    def test_skip_filters(self):
        html = "<html><body><p>Test</p></body></html>"
        content, steps = run_pipeline(html, MKDOCS_PROFILE, skip={"scripts", "code_blocks"})
        step_names = [s for s in steps]
        assert "scripts" not in step_names
        assert "code_blocks" not in step_names


class TestFilterOverrides:
    def test_remove_copycode_image(self):
        md = 'Click [`file.yaml`](url)![](/images/copycode.svg "Copy file to clipboard")'
        result = filter_overrides(md, GENERIC_PROFILE)
        assert "copycode" not in result
        assert "Click" in result
        assert '[`file.yaml`](url)' in result

    def test_remove_feedback_yes_no(self):
        md = """## Feedback

Yes
No

. If you have a question, ask on [Stack Overflow](https://stackoverflow.com/).

January 01, 2025 at 12:00 PM PST: [some commit](https://github.com/example/commit)"""
        result = filter_overrides(md, GENERIC_PROFILE)
        assert result == ""  # 整个文档就是反馈尾注，预期清空

    def test_no_feedback_no_change(self):
        md = "# Good Content\n\nThis is real content.\n\n## Next Section\n\nMore text."
        result = filter_overrides(md, GENERIC_PROFILE)
        assert result == md

    def test_remove_only_trailing_feedback(self):
        md = """## Feedback

Yes
No

. Dismiss.

## Another Section

Real content here."""
        result = filter_overrides(md, GENERIC_PROFILE)
        assert "## Another Section" in result
        assert "Real content" in result
        assert "Feedback" not in result

    def test_remove_on_this_page(self):
        md = "## On this page\n- Getting Started\n  - [Welcome](/welcome)\n\n# Real Title\n\nContent."
        result = filter_overrides(md, GENERIC_PROFILE)
        assert "On this page" not in result
        assert "# Real Title" in result
        assert "Content" in result

    def test_on_this_page_end_of_doc(self):
        md = "## On this page\n- Item 1\n- Item 2"
        result = filter_overrides(md, GENERIC_PROFILE)
        assert result == "" or "Item" not in result

    def test_emoji_cleanup(self):
        md = "Hello ⚠️ Warning ⚡ Note ✅ Done 👋 Wave"
        result = filter_overrides(md, GENERIC_PROFILE)
        assert "Hello" in result
        assert "Warning" in result
        assert "Note" in result
        assert "Done" in result
        assert "Wave" in result
        for char in ("⚠", "✅", "👋"):
            assert char not in result, f"Expected emoji {char!r} to be stripped"


class TestFilterLineNumberedCode:
    def test_strip_fern_gutter(self):
        html = """<pre class="code-block-line-group"><table><tbody>
<tr><td class="code-block-line-gutter">1</td><td class="code-block-line-content"><span class="line">import foo</span></td></tr>
<tr><td class="code-block-line-gutter">2</td><td class="code-block-line-content"><span class="line">print("hello")</span></td></tr>
</tbody></table></pre>"""
        result = filter_line_numbered_code(html, GENERIC_PROFILE)
        assert "code-block-line-gutter" not in result
        assert "import foo" in result
        assert "code-block-line-gutter" not in result

    def test_strip_fern_gutter_shell(self):
        html = """<pre class="code-block-line-group language-bash"><table><tbody>
<tr><td class="code-block-line-gutter">$</td><td class="code-block-line-content"><span class="line">pip install foo</span></td></tr>
</tbody></table></pre>"""
        result = filter_line_numbered_code(html, GENERIC_PROFILE)
        assert "code-block-line-gutter" not in result
        assert "pip install" in result

    def test_no_gutter_no_change(self):
        html = "<pre><code>print(1)</code></pre>"
        result = filter_line_numbered_code(html, GENERIC_PROFILE)
        assert "print(1)" in result


class TestCodeBlockShellPrompt:
    BASH_PROFILE = FrameworkProfile(
        name="bash-aware",
        code_lang_class_map={"language-bash": "bash"},
    )

    def test_strip_shell_dollar_prompt(self):
        html = '<pre><code class="language-bash">$ pip install foo\n$ python run.py</code></pre>'
        result = _run_code_blocks(html, self.BASH_PROFILE)
        assert "```bash" in result
        assert "$ pip" not in result
        assert "pip install foo" in result
        assert "python run.py" in result

    def test_no_prompt_no_change(self):
        html = '<pre><code class="language-python">print("hello")</code></pre>'
        result = _run_code_blocks(html, GENERIC_PROFILE)
        assert 'print("hello")' in result

    def test_fern_table_code_with_language(self):
        html = """<pre class="code-block-line-group language-python"><table><tbody>
<tr><td class="code-block-line-gutter">1</td><td class="code-block-line-content"><span class="line">import sys</span></td></tr>
</tbody></table></pre>"""
        result = filter_line_numbered_code(html, GENERIC_PROFILE)
        result = _run_code_blocks(result, GENERIC_PROFILE)
        assert "```python" in result or "```" in result
        assert "import sys" in result


class TestPipelineWithLineNumbers:
    def test_pipeline_includes_line_numbers(self):
        html = """<html><body>
            <div class="md-content">
                <p>Test</p>
                <pre class="language-bash"><code class="language-bash">$ echo hi</code></pre>
            </div>
        </body></html>"""
        content, steps = run_pipeline(html, MKDOCS_PROFILE)
        assert "line_numbers" in steps
        # Shell prompt should be stripped
        assert "$ echo" not in content
        assert "echo hi" in content


class TestFilterOverridesCodeBlockAware:
    """B: override patterns (emoji, copycode, feedback, ...) must NOT run
    inside fenced code blocks — they would corrupt documented source."""

    def test_emoji_preserved_inside_code_block(self):
        md = 'Intro 🚀\n\n```python\nstatus = "🚀 launched"\n```\n\nOutro ✅'
        result = filter_overrides(md, GENERIC_PROFILE)
        # emoji inside code block preserved verbatim
        assert 'status = "🚀 launched"' in result
        # emoji OUTSIDE code blocks still stripped
        before = result.split("```python")[0]
        after = result.split("```")[2]
        assert "🚀" not in before
        assert "✅" not in after

    def test_emoji_still_stripped_outside_code(self):
        md = "Hello ⚠️ Warning ⚡ Note ✅ Done"
        result = filter_overrides(md, GENERIC_PROFILE)
        for char in ("⚠", "✅"):
            assert char not in result

    def test_copycode_skipped_inside_code_block(self):
        md = '```bash\nimg=![](/images/copycode.svg "copy")\necho done\n```'
        result = filter_overrides(md, GENERIC_PROFILE)
        assert "copycode" in result  # preserved inside code


class TestFilterRestoreCodeListIndent:
    """C: a placeholder indented as a list-item continuation line (any depth,
    ordered or unordered) must be moved to column 0 so the fence is at line
    start."""

    def test_indented_placeholder_moved_to_column_zero(self):
        import silkworm.spin.filters as f

        f._code_block_store = {"SILKWORMCODEBLOCK0%%": "```bash\necho hi\n```"}
        md = "1. Step one\n   2. Step two\n      SILKWORMCODEBLOCK0%%\n   Next text"
        result = f.filter_restore_code(md, GENERIC_PROFILE)
        for line in result.split("\n"):
            if "```bash" in line:
                assert line == "```bash", f"fence not at column 0: {line!r}"
        assert "echo hi" in result

    def test_unordered_list_two_space_indent(self):
        import silkworm.spin.filters as f

        f._code_block_store = {"SILKWORMCODEBLOCK0%%": "```bash\necho hi\n```"}
        md = "- item\n  SILKWORMCODEBLOCK0%%"
        result = f.filter_restore_code(md, GENERIC_PROFILE)
        for line in result.split("\n"):
            if "```bash" in line:
                assert line == "```bash", f"fence not at column 0: {line!r}"

    def test_integration_ordered_list_via_pipeline(self):
        html = (
            "<ol><li>Step\n"
            '<pre><code class="language-bash">echo hi</code></pre>\n'
            "</li></ol>"
        )
        result = _run_code_blocks(html, GENERIC_PROFILE)
        fence_lines = [ln for ln in result.split("\n") if "```bash" in ln]
        assert fence_lines, "no bash fence found"
        assert all(ln == "```bash" for ln in fence_lines), f"indented fence: {fence_lines}"
        assert "echo hi" in result

    def test_integration_unordered_list_via_pipeline(self):
        html = (
            "<ul><li>Item\n"
            '<pre><code class="language-bash">echo hi</code></pre>\n'
            "</li></ul>"
        )
        result = _run_code_blocks(html, GENERIC_PROFILE)
        fence_lines = [ln for ln in result.split("\n") if "```bash" in ln]
        assert fence_lines, "no bash fence found"
        assert all(ln == "```bash" for ln in fence_lines), f"indented fence: {fence_lines}"


class TestFencedBlockNestedFence:
    """D: when code content itself contains ```, the outer fence must switch
    to ~~~ so the inner fence doesn't break the Markdown."""

    def test_make_fenced_block_uses_tilde_for_nested_backticks(self):
        block = _make_fenced_block("md", "```python\nprint('hi')\n```")
        assert block.startswith("~~~md")
        assert block.endswith("~~~")
        # inner ``` code is preserved intact (one open + one close)
        assert block.count("```python") == 1

    def test_make_fenced_block_uses_backtick_when_safe(self):
        block = _make_fenced_block("python", 'print("hi")')
        assert block.startswith("```python")
        assert block.endswith("```")

    def test_nested_fence_via_pipeline(self):
        html = '<pre><code class="language-md">```python\nprint("hi")\n```</code></pre>'
        result = _run_code_blocks(html, GENERIC_PROFILE)
        # outer fence must be ~~~ so inner ```python survives intact
        assert "~~~md" in result
        assert result.count("```python") == 1
        # inner code preserved
        assert 'print("hi")' in result


class TestTableCellInlineCode:
    """Regression: <code> inside <td>/<th> must stay inline, never fenced.

    Markdown tables cannot contain fenced code blocks; fencing a cell's
    <code> corrupts the whole table (see tekton triggers-api/events tables).
    """

    def test_single_line_code_in_td_stays_inline(self):
        html = (
            "<table><tr>"
            "<td><code>EventListener</code></td>"
            "<td><code>Started</code></td>"
            "</tr></table>"
        )
        result = _run_code_blocks(html, GENERIC_PROFILE)
        assert "```" not in result
        assert "EventListener" in result and "Started" in result

    def test_multi_line_code_in_td_stays_inline(self):
        html = (
            "<table><tr>"
            "<td><code>\ntriggers.tekton.dev/v1alpha1\n</code></td>"
            "</tr></table>"
        )
        result = _run_code_blocks(html, GENERIC_PROFILE)
        assert "```" not in result
        assert "triggers.tekton.dev/v1alpha1" in result


class TestNestedTableFlatten:
    """Regression: a <table> nested inside a <td> must be flattened, not leak
    markdown separators into the outer table row (markdown has no nested tables).
    """

    def test_nested_table_flattened_no_leak(self):
        html = (
            "<table><tr>"
            "<td>outer</td>"
            "<td><table><tr><th>sub</th></tr><tr><td>val</td></tr></table></td>"
            "</tr></table>"
        )
        content, _ = run_pipeline(html, GENERIC_PROFILE)
        for line in content.split("\n"):
            assert not re.search(r"\|\s*\|\s*\|\s*\|\s*---", line), f"leak: {line}"
        assert "outer" in content
        assert "sub" in content and "val" in content


class TestFlattenBlockInCells:
    """Regression: a <pre> (block code) inside a <td>/<th> cannot be a fenced
    code block in markdown — markdownify would render it as ``` and break the
    table row. It must be flattened to inline `code` so the table stays valid.
    """

    def test_pre_in_td_stays_inline(self):
        html = (
            "<table><tr>"
            "<td>name</td>"
            "<td><pre>body.value == 'test'</pre></td>"
            "</tr></table>"
        )
        content, _ = run_pipeline(html, GENERIC_PROFILE)
        assert "```" not in content, f"fenced block leaked into table cell:\n{content}"
        assert "body.value == 'test'" in content
        assert content.strip().startswith("|"), f"not a table row:\n{content}"

    def test_multiline_pre_in_td_flattened_to_single_line(self):
        html = (
            "<table><tr>"
            "<td>fn</td>"
            "<td><pre>a.b.c()\n     x.y.z()</pre></td>"
            "</tr></table>"
        )
        content, _ = run_pipeline(html, GENERIC_PROFILE)
        assert "```" not in content, f"fenced block leaked into table cell:\n{content}"
        assert "a.b.c() x.y.z()" in content
