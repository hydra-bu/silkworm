from copy import deepcopy
from typing import NamedTuple

from silkworm.config import LLMConfig
from silkworm.models import FrameworkProfile, PageRecord, QualityReport
from silkworm.spin.detector import detect_framework, get_profile
from silkworm.spin.filters import run_pipeline
from silkworm.spin.quality import evaluate


class PipelineResult(NamedTuple):
    content: str
    record: PageRecord
    steps: list[str]
    retry_count: int
    final_report: QualityReport


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

    def run(self, raw_html: str, record: PageRecord) -> PipelineResult:
        if not record.framework:
            record.framework = detect_framework(raw_html)
        profile = get_profile(record.framework)

        content, steps, report, retry_count = self._run_with_retry(raw_html, profile)

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

    def _run_with_retry(
        self, raw_html: str, profile: FrameworkProfile
    ) -> tuple[str, list[str], QualityReport, int]:
        adj_profile = deepcopy(profile)
        skip: set[str] = set()
        extra_phrases: list[str] = []

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