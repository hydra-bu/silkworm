import json
import logging
import re

from openai import OpenAI

from silkworm.config import LLMConfig
from silkworm.models import CheckResult, QualityReport, RetryAction

logger = logging.getLogger(__name__)

NOISE_PHRASES: list[str] = [
    "Edit this page",
    "Was this page helpful?",
    "Was this helpful?",
    "Last update:",
    "Last updated",
    "Copyright",
    "All rights reserved",
    "Suggest an edit",
    "Suggest edits",
    "Report an issue",
    "Report issues",
    "On this page",
    "反馈",
    "编辑此页",
    "此页面有帮助吗？",
]

LINK_DENSITY_THRESHOLD = 0.15
LINK_COUNT_PER_1K_THRESHOLD = 8.0

LINK_PATTERN = re.compile(r"\[([^\]]*)\]\(([^)]*)\)")
HEADING_PATTERN = re.compile(r"^(#{1,6})\s", re.MULTILINE)
MD_SYMBOLS_PATTERN = re.compile(r"[#*`~>\[\]()!|-]")


def check_length(content: str, min_length: int = 500) -> CheckResult:
    text = _strip_markdown(content)
    length = len(text)
    passed = length >= min_length
    score = min(1.0, length / min_length)
    return CheckResult(
        passed=passed,
        score=score,
        detail=f"正文长度: {length} 字符 (阈值: {min_length})",
    )


def check_code_integrity(content: str) -> CheckResult:
    count = content.count("```")
    passed = count % 2 == 0
    return CheckResult(
        passed=passed,
        score=1.0 if passed else 0.0,
        detail=f"代码块标记计数: {count} ({'偶数' if passed else '奇数'})",
    )


_CODE_FENCE = re.compile(r'^(`{3,})\s*(\S*)\s*$', re.MULTILINE)


def check_code_block_content(content: str) -> CheckResult:
    """Check fenced code block content quality for common pipeline defects.

    Catches issues that shouldn't survive the cleaning pipeline:
    - Escaped underscores (\\_) inside code blocks — code content is rendered
      literally, so \\_ is never valid and indicates markdownify leakage
    - Python indentation loss — when lines after a colon have no leading
      whitespace, the code block structure is broken
    """
    issues: list[str] = []
    lines = content.split('\n')
    in_block = False
    block_lang = ''
    block_lines: list[str] = []

    for i, line in enumerate(lines):
        m = _CODE_FENCE.match(line)
        if m:
            if in_block:
                # End of block — analyze
                _check_python_block(block_lang, block_lines, issues, i)
                block_lines = []
            block_lang = m.group(2)
            in_block = not in_block
        elif in_block:
            if '\\_' in line:
                issues.append(f"Escaped underscore at line {i+1} in {block_lang or 'unknown'} block")
            block_lines.append(line)

    # Last block
    if block_lines:
        _check_python_block(block_lang, block_lines, issues, len(lines))

    if not issues:
        return CheckResult(passed=True, score=1.0, detail="No code block content issues")

    detail = ' | '.join(issues[:5])
    if len(issues) > 5:
        detail += f' (+{len(issues) - 5} more)'
    return CheckResult(passed=False, score=max(0.0, 1.0 - len(issues) * 0.15), detail=detail)


def _check_python_block(lang: str, lines: list[str], issues: list[str], line_offset: int) -> None:
    """Check indentation consistency inside a Python code block."""
    if lang.lower() not in ('python', 'py', ''):
        return
    if not lines:
        return

    # Quick heuristic: if a line ends with ':' (def/class/if/for/while/try/except/with/elif/else)
    # the next non-comment, non-empty line must have some leading whitespace
    for i in range(len(lines) - 1):
        stripped = lines[i].rstrip()
        if not stripped or stripped.startswith('#'):
            continue
        if stripped.endswith(':'):
            # Find the next meaningful line
            for j in range(i + 1, len(lines)):
                next_line = lines[j]
                if not next_line.strip() or next_line.strip().startswith('#'):
                    continue
                leading = len(next_line) - len(next_line.lstrip())
                if leading == 0:
                    issues.append(
                        f"Python indentation loss at content line {line_offset + j + 1}: "
                        f"line after '{stripped[:40]}' has no indentation"
                    )
                break


def check_heading_jumps(content: str) -> CheckResult:
    headings = HEADING_PATTERN.findall(content)
    if len(headings) < 2:
        return CheckResult(passed=True, score=1.0, detail="标题数量不足，跳过跳跃检查")

    jumps = 0
    for i in range(1, len(headings)):
        prev_level = len(headings[i - 1])
        curr_level = len(headings[i])
        if curr_level - prev_level > 2:
            jumps += 1

    passed = jumps <= max(1, len(headings) * 0.1)
    score = max(0.0, 1.0 - jumps / max(1, len(headings)))
    return CheckResult(
        passed=passed,
        score=score,
        detail=f"标题跳跃次数: {jumps}/{len(headings)}",
    )


def _check_link_density(content: str) -> tuple[float, float]:
    total_chars = max(1, len(content))
    # 短文本（<200字符）下 per-1k 指标失真，回退到密度判断
    if total_chars < 200:
        links = LINK_PATTERN.findall(content)
        link_chars = sum(len(text) for text, _ in links)
        density = link_chars / total_chars
        return density, 0.0
    links = LINK_PATTERN.findall(content)
    link_chars = sum(len(text) for text, _ in links)
    density = link_chars / total_chars
    per_1k = len(links) / (total_chars / 1000)
    return density, per_1k


def check_noise_lines(content: str) -> CheckResult:
    noise_count = sum(content.count(phrase) for phrase in NOISE_PHRASES)
    phrase_passed = noise_count <= 5

    link_density, link_per_1k = _check_link_density(content)
    density_passed = (
        link_density < LINK_DENSITY_THRESHOLD
        and link_per_1k < LINK_COUNT_PER_1K_THRESHOLD
    )

    passed = phrase_passed and density_passed
    score = round(
        min(
            1.0,
            max(0.0, 1.0 - noise_count / 10.0)
            * max(0.0, 1.0 - link_density * 3)
            * max(0.0, 1.0 - (link_per_1k - 2) / 20),
        ),
        3,
    )

    detail = (
        f"噪声短语命中: {noise_count} 次{' (超标)' if not phrase_passed else ''}"
        f" | 链接密度: {link_density:.1%}"
        f"{' (过高)' if not density_passed and link_density >= LINK_DENSITY_THRESHOLD else ''}"
        f" | 每千字符链接数: {link_per_1k:.1f}"
        f"{' (过高)' if not density_passed and link_per_1k >= LINK_COUNT_PER_1K_THRESHOLD else ''}"
    )

    return CheckResult(passed=passed, score=score, detail=detail)


def check_link_density(
    content: str,
    max_density: float = LINK_DENSITY_THRESHOLD,
    max_per_1k: float = LINK_COUNT_PER_1K_THRESHOLD,
) -> CheckResult:
    link_density, link_per_1k = _check_link_density(content)
    passed = link_density < max_density and link_per_1k < max_per_1k
    score = round(
        min(
            1.0,
            max(0.0, 1.0 - link_density * 3)
            * max(0.0, 1.0 - (link_per_1k - 2) / 20),
        ),
        3,
    )
    return CheckResult(
        passed=passed,
        score=score,
        detail=(
            f"链接密度: {link_density:.1%}"
            f" | 每千字符链接数: {link_per_1k:.1f}"
            f" (阈值: {max_density:.0%}/{max_per_1k:.0f})"
        ),
    )


def _strip_markdown(md: str) -> str:
    text = MD_SYMBOLS_PATTERN.sub("", md)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _diagnose_failure(checks: dict[str, CheckResult]) -> RetryAction | None:
    hj = checks.get("heading_jumps")
    le = checks.get("length")
    nl = checks.get("noise_lines")
    ci = checks.get("code_integrity")

    if hj and not hj.passed and le and not le.passed:
        return RetryAction(
            filter_name="main_content",
            action="try_trafilatura",
            description=(
                "Filter 1 主区域选择器可能选错（标题跳跃+正文过短），"
                "建议切换到 trafilatura 兜底提取"
            ),
        )

    if hj and not hj.passed:
        return RetryAction(
            filter_name="main_content",
            action="widen_selector",
            description=(
                "Filter 1 主区域选择器可能选小（标题跳跃），"
                "建议扩大 selector 范围或切 trafilatura"
            ),
        )

    if le and not le.passed:
        return RetryAction(
            filter_name="main_content",
            action="try_trafilatura",
            description=(
                "Filter 1 主区域选择器可能选小（正文过短），"
                "建议切换到 trafilatura 兜底提取"
            ),
        )

    if nl and not nl.passed:
        return RetryAction(
            filter_name="boilerplate",
            action="add_boilerplate",
            description=(
                "Filter 3 样板文字清除不彻底（噪声短语命中或链接密度过高），"
                "建议补充 boilerplate_phrases"
            ),
        )

    if ci and not ci.passed:
        return RetryAction(
            filter_name="code_blocks",
            action="skip_filter",
            description=(
                "Filter 5 代码块处理导致 fence 不配对，"
                "建议跳过 code_blocks filter"
            ),
        )

    cbc = checks.get("code_block_content")
    if cbc and not cbc.passed:
        return RetryAction(
            filter_name="line_numbers",
            action="skip_filter",
            description=(
                "代码块内容异常（转移下划线或缩进丢失），"
                "建议跳过 line_numbers filter"
            ),
        )

    return None


LLM_QUALITY_SYSTEM_PROMPT = """You are a documentation extraction quality judge. Given Markdown content extracted from a documentation website, determine whether the extraction was SUCCESSFUL or FAILED.

Common extraction failures to detect:
1. **Navigation/boilerplate only**: Content is mostly sidebar menus, breadcrumbs, or "Edit this page" boilerplate — not the actual documentation body.
2. **Truncated content**: Content cuts off abruptly mid-sentence or mid-section.
3. **Wrong region**: Content appears to be from a header, footer, or sidebar — not the main content area.
4. **Broken structure**: Headings without body text, or body text without context.
5. **Excessive links**: Content is mostly a list of links rather than prose documentation.
6. **Garbled text**: Unreadable character sequences, encoding issues.

Respond with a JSON object ONLY (no markdown fences, no explanation):
{"passed": true, "score": 0.95, "reason": "Clean documentation body with proper structure"}

Score guidelines:
- 0.9-1.0: Clean, complete documentation content
- 0.7-0.9: Acceptable with minor issues
- 0.5-0.7: Marginal — some useful content but also problems
- 0.0-0.5: Failed extraction — not usable as documentation"""


def llm_quality_judge(content: str, llm_config: LLMConfig) -> dict | None:
    api_key = llm_config.get_api_key()
    if not api_key:
        return None

    max_chars = 8000
    truncated = content[:max_chars]
    if len(content) > max_chars:
        truncated += "\n\n[... content truncated ...]"

    try:
        client = OpenAI(
            base_url=llm_config.base_url,
            api_key=api_key,
        )

        resp = client.chat.completions.create(
            model=llm_config.model,
            messages=[
                {"role": "system", "content": LLM_QUALITY_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"Evaluate this extracted Markdown documentation content:\n\n{truncated}",
                },
            ],
            temperature=llm_config.temperature,
            max_tokens=512,
            timeout=llm_config.timeout,
        )

        raw = (resp.choices[0].message.content or "").strip()
        return _parse_llm_quality_response(raw)

    except Exception as e:
        logger.warning("LLM 质检调用失败: %s", e)
        return None


def _parse_llm_quality_response(raw: str) -> dict | None:
    if not raw:
        return None

    if raw.startswith("```"):
        start = raw.find("\n")
        end = raw.rfind("```")
        if start != -1 and end != -1 and end > start:
            raw = raw[start:end].strip()
        elif start != -1:
            raw = raw[start:].strip()

    start_idx = raw.find("{")
    end_idx = raw.rfind("}")
    if start_idx == -1 or end_idx == -1 or end_idx <= start_idx:
        return None

    raw = raw[start_idx : end_idx + 1]

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None

    if "passed" not in data:
        return None

    return {
        "passed": bool(data["passed"]),
        "score": float(data.get("score", 0.5)),
        "reason": str(data.get("reason", "")),
    }


def evaluate(
    content: str,
    min_length: int = 500,
    score_threshold: float = 0.6,
    llm_low: float = 0.3,
    llm_high: float = 0.8,
    llm_config: LLMConfig | None = None,
) -> QualityReport:
    checks = {
        "length": check_length(content, min_length),
        "code_integrity": check_code_integrity(content),
        "code_block_content": check_code_block_content(content),
        "heading_jumps": check_heading_jumps(content),
        "noise_lines": check_noise_lines(content),
    }

    total_score = sum(c.score for c in checks.values()) / len(checks)
    all_passed = all(c.passed for c in checks.values())
    passed = all_passed or total_score >= score_threshold

    retry_action = None
    if not all_passed:
        retry_action = _diagnose_failure(checks)

    needs_llm = llm_low <= total_score <= llm_high and not passed

    llm_passed: bool | None = None
    llm_reason = ""
    if needs_llm and llm_config:
        llm_result = llm_quality_judge(content, llm_config)
        if llm_result is not None:
            llm_passed = llm_result["passed"]
            llm_reason = llm_result.get("reason", "")
            if llm_passed:
                passed = True

    message_parts = []
    if passed:
        message_parts.append("质检通过")
    else:
        message_parts.append("质检未通过")
    if needs_llm:
        if llm_config:
            message_parts.append(f"LLM 复核: {'✅ 通过' if llm_passed else '❌ 不通过'}" + (f" ({llm_reason})" if llm_reason else ""))
        else:
            message_parts.append("落在灰色区间（未配置 LLM，按规则判定）")
    if retry_action:
        message_parts.append(retry_action.description)

    return QualityReport(
        passed=passed,
        total_score=round(total_score, 3),
        checks=checks,
        failed_filter=retry_action.filter_name if retry_action else None,
        retry_action=retry_action,
        needs_llm=needs_llm,
        llm_passed=llm_passed,
        llm_reason=llm_reason,
        message=" | ".join(message_parts),
    )