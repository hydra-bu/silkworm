"""核心数据模型。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class SiteConfig(BaseModel):
    """站点采集配置。"""

    base_url: str
    allow_patterns: list[str] = []  # URL 允许规则（正则）
    deny_patterns: list[str] = []
    crawl_delay: float = 1.0  # 秒
    max_concurrency: int = 4
    user_agent: str = "Silkworm/0.1.0 (+https://github.com/silkworm)"
    respect_robots: bool = True
    force_render: bool = False  # 强制走 playwright


class FrameworkProfile(BaseModel):
    """框架 Profile，定义如何检测和处理特定文档框架。"""

    name: str  # mkdocs / docusaurus / sphinx / generic
    fingerprint_rules: list[str] = Field(
        default_factory=list, description="用于检测框架的 CSS 选择器或 meta 规则"
    )
    main_selector: str = ""
    remove_selectors: list[str] = Field(default_factory=list)
    boilerplate_phrases: list[str] = Field(default_factory=list)
    code_lang_class_map: dict[str, str] = Field(default_factory=dict)
    markdown_suffixes: list[str] = Field(
        default_factory=list,
        description="该框架官方的 markdown 端点后缀（如 ['.md']），抓取时优先尝试",
    )
    markdown_strip_headings: list[str] = Field(
        default_factory=list,
        description="官方 markdown 中应整体移除的章节（按标题匹配，含其下全部子内容），如框架注入的 Agent 指令段",
    )


PageStatus = Literal[
    "pending", "fetched", "cleaned", "passed", "failed", "manual_review"
]


class PageRecord(BaseModel):
    """单页面的采集记录，存储于 SQLite / _meta/pages.db。"""

    url: str
    canonical_url: str = ""
    fetched_at: datetime | None = None
    content_hash: str = ""
    http_status: int = 0
    raw_html_path: str = ""
    raw_md_path: str = ""  # 官方 markdown 端点内容路径（source=markdown 时）
    framework: str = ""
    status: PageStatus = "pending"
    quality_report: dict | None = None
    source: Literal["html", "markdown"] = "html"  # 原始内容类型


class FingerprintRule(BaseModel):
    """框架指纹规则。"""

    selector: str  # CSS 选择器或 meta[name] 格式
    value: str | None = None  # 期望的属性值（例如 meta content），None 表示只检查存在性
    score: int = 1  # 该规则的匹配分数


class RetryAction(BaseModel):
    """质检失败后推荐的重试调整策略。"""

    filter_name: str = ""  # 建议调整的 Filter 名称，如 "main_content"
    action: str = ""  # 调整方式: "try_trafilatura" / "add_boilerplate" / "skip_filter" / "widen_selector" / "narrow_selector"
    description: str = ""  # 人类可读的描述，如 "Filter 1 主区域选择器可能选小，建议切 trafilatura 兜底"


class QualityReport(BaseModel):
    """质检报告。"""

    passed: bool = False
    total_score: float = 0.0
    checks: dict[str, "CheckResult"] = Field(default_factory=dict)
    failed_filter: str | None = None  # 哪个 Filter 可能有问题
    retry_action: RetryAction | None = None  # 重试调整策略
    needs_llm: bool = False  # 分数落在灰色区间，建议 LLM 复核
    llm_passed: bool | None = None  # LLM 复核结果（None=未调用）
    llm_reason: str = ""  # LLM 复核理由
    message: str = ""


class CheckResult(BaseModel):
    """单个质检项的结果。"""

    passed: bool
    score: float
    detail: str = ""
