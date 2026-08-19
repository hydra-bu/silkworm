from copy import deepcopy
import logging
import re
from typing import NamedTuple

from silkworm.config import LLMConfig
from silkworm.models import FrameworkProfile, PageRecord, QualityReport, CheckResult
from silkworm.spin.detector import detect_framework, get_profile
from silkworm.spin.filters import normalize_markdown, run_pipeline
from silkworm.spin.quality import evaluate

logger = logging.getLogger(__name__)


class PipelineResult(NamedTuple):
    content: str
    record: PageRecord
    steps: list[str]
    retry_count: int
    final_report: QualityReport


# 围栏代码块分隔行（``` 或 ~~~，起始可带缩进与语言标注，闭合为裸行）
_FENCE_LINE = re.compile(r"^\s*(`{3,}|~{3,})[^`~]*$", re.MULTILINE)


_GENERIC_BOILERPLATE_FALLBACK = [
    "Feedback",
    "Was this",
    "Edit this",
    "Last modified",
    "Thanks for",
]

_WIDE_SELECTOR = "main, article, .content, #content, .documentation, body"


class PipelineOrchestrator:
    def __init__(
        self,
        max_retries: int = 2,
        quality_min_length: int = 500,
        quality_threshold: float = 0.6,
        llm_config: LLMConfig | None = None,
    ):
        self.max_retries = max_retries
        self.quality_min_length = quality_min_length
        self.quality_threshold = quality_threshold
        self.llm_config = llm_config

    def run(self, raw_content: str, record: PageRecord) -> PipelineResult:
        # 内容质量预检查：过滤低价值文件
        if not self._is_content_valuable(raw_content, record):
            record.status = "skipped_low_quality"
            return PipelineResult(
                content="",
                record=record,
                steps=["skipped_low_quality"],
                retry_count=0,
                final_report=QualityReport(
                    passed=False,
                    total_score=0.0,
                    checks={"quality": CheckResult(passed=False, score=0.0, detail="Content too short or low value")},
                ),
            )

        if not record.framework:
            if record.source == "markdown":
                # markdown 源没有 HTML 可跑指纹检测，但 URL 可能命中框架的
                # 官方 markdown 端点约定（Profile.markdown_suffixes 声明），
                # 据此推断框架；未命中则回退 generic。
                record.framework = self._infer_framework_from_url(record.url)
            else:
                record.framework = detect_framework(raw_content)
        profile = get_profile(record.framework)

        if record.source == "markdown":
            content, steps, report = self._run_markdown(raw_content, profile)
            return self._finalize(content, record, steps, report, 0)

        content, steps, report, retry_count = self._run_with_retry(raw_content, profile)
        return self._finalize(content, record, steps, report, retry_count)

    @staticmethod
    def _infer_framework_from_url(url: str) -> str:
        """markdown 源：按 URL 路径后缀匹配 Profile.markdown_suffixes 声明的框架。"""
        if not url or not url.startswith(("http://", "https://")):
            return "generic"
        path = url.split("?", 1)[0].split("#", 1)[0]
        for name, profile in get_profile.__globals__["BUILTIN_PROFILES"].items():
            suffixes = getattr(profile, "markdown_suffixes", None)
            if suffixes and path.endswith(tuple(suffixes)):
                return name
        return "generic"

    def _finalize(
        self,
        content: str,
        record: PageRecord,
        steps: list[str],
        report: QualityReport,
        retry_count: int,
    ) -> PipelineResult:
        if report.passed:
            record.status = "passed"
        else:
            record.status = "manual_review"
        record.quality_report = report.model_dump()

        return PipelineResult(
            content=content,
            record=record,
            steps=steps,
            retry_count=retry_count,
            final_report=report,
        )

    def _run_markdown(
        self, raw_md: str, profile: FrameworkProfile
    ) -> tuple[str, list[str], QualityReport]:
        """官方 markdown 端点内容：轻量 normalize 后直接质检。

        框架官方输出的 markdown 已是结构化正文，跳过 HTML 清洗管线，
        仅做格式规整（空白/样板行），再走与 HTML 管线相同的质量门禁。
        """
        content = normalize_markdown(raw_md, profile)
        steps = ["markdown_normalize", "quality_gate"]
        raw_code_count = len(_FENCE_LINE.findall(raw_md)) // 2
        report = evaluate(
            content,
            min_length=self.quality_min_length,
            score_threshold=self.quality_threshold,
            llm_config=self.llm_config,
            raw_code_count=raw_code_count,
        )
        return content, steps, report

    def _run_with_retry(
        self, raw_html: str, profile: FrameworkProfile
    ) -> tuple[str, list[str], QualityReport, int]:
        adj_profile = deepcopy(profile)
        skip: set[str] = set()
        extra_phrases: list[str] = []
        # HTML 源：以 <pre> 数量作为原始代码块基线（供质检硬失败项使用）
        raw_code_count = raw_html.lower().count("<pre")

        for attempt in range(self.max_retries + 1):
            if extra_phrases:
                adj_profile.boilerplate_phrases = list(
                    set(adj_profile.boilerplate_phrases + extra_phrases)
                )

            content, steps = run_pipeline(raw_html, adj_profile, skip=skip)
            report = evaluate(
                content,
                min_length=self.quality_min_length,
                score_threshold=self.quality_threshold,
                llm_config=self.llm_config,
                raw_code_count=raw_code_count,
            )

            if report.passed:
                return content, steps, report, attempt

            if attempt < self.max_retries:
                self._apply_retry(report.retry_action, adj_profile, skip, extra_phrases)

        return content, steps, report, self.max_retries

    def _apply_retry(
        self,
        action: object,
        profile: FrameworkProfile,
        skip: set[str],
        extra_phrases: list[str],
    ) -> None:
        if action is None or not hasattr(action, "action"):
            # 无明确策略 → 回退 widen selector + trafilatura
            profile.main_selector = _WIDE_SELECTOR
            return

        act = action.action if hasattr(action, "action") else ""
        filter_name = action.filter_name if hasattr(action, "filter_name") else ""

        if act == "try_trafilatura":
            profile.main_selector = ""
        elif act == "widen_selector":
            profile.main_selector = _WIDE_SELECTOR
        elif act == "add_boilerplate":
            extra_phrases.extend(_GENERIC_BOILERPLATE_FALLBACK)
        elif act == "skip_filter":
            skip.add(filter_name)
        else:
            profile.main_selector = _WIDE_SELECTOR

    @staticmethod
    def _is_content_valuable(raw_content: str, record: PageRecord) -> bool:
        """预检查内容质量：过滤明显无意义的页面。

        直接跳过的特征（内容极少，无需重试）：
        - 正文 < 50 字符（去掉 frontmatter）
        - 正文 < 5 行
        - 几乎全是链接（链接密度 > 20，无实质内容）

        其他情况走正常重试流程。
        """
        # 去掉 frontmatter
        if raw_content.startswith("---"):
            parts = raw_content.split("---", 2)
            if len(parts) >= 3:
                body = parts[2]
            else:
                body = raw_content
        else:
            body = raw_content

        lines = body.split("\n")
        non_empty_lines = [l for l in lines if l.strip()]
        char_count = len(body.strip())
        line_count = len(non_empty_lines)

        # 计算链接密度
        link_count = len(re.findall(r"\[.*?\]\(.*?\)", body))
        link_density = link_count / max(1, char_count / 100)  # 每100字符的链接数

        # 只有内容极少时才直接跳过（无需重试）
        is_low_value = (
            char_count < 50
            or line_count < 5
            or (link_density > 20 and char_count < 200)  # 几乎全是链接
        )

        if is_low_value:
            logger.info(
                "跳过低价值页面: %s (%d chars, %d lines, links=%d)",
                record.url,
                char_count,
                line_count,
                link_count,
            )

        return not is_low_value