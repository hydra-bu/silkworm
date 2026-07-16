"""配置管理 — 基于 pydantic + YAML 的站点/全局配置。"""

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from silkworm.models import SiteConfig


class LLMConfig(BaseModel):
    """LLM 服务配置，用于 Navigator 导航分析等场景。"""

    base_url: str = Field(default="http://192.168.0.13:8000/v1")
    api_key: str = Field(default="")
    model: str = Field(default="Gemma4")
    temperature: float = 0.1
    max_tokens: int = 4096
    timeout: float = 120.0

    def get_api_key(self) -> str:
        """优先环境变量，其次配置字面值。"""
        return os.environ.get("SILKWORM_LLM_API_KEY") or self.api_key or ""


class GlobalConfig(BaseModel):
    """全局配置。"""

    cocoon_dir: Path = Field(default=Path("cocoon"), description="原始 HTML 存储区")
    silk_dir: Path = Field(default=Path("silk"), description="最终 Markdown 输出目录")
    max_retries: int = 2
    quality_min_length: int = 500
    quality_score_threshold: float = 0.6
    llm_threshold_low: float = 0.3
    llm_threshold_high: float = 0.8
    llm: LLMConfig = Field(default_factory=LLMConfig)

    model_config = {"arbitrary_types_allowed": True}


class AppConfig(BaseModel):
    """应用配置，包含全局配置和多个站点配置。"""

    global_config: GlobalConfig = Field(default_factory=GlobalConfig)
    sites: dict[str, SiteConfig] = Field(default_factory=dict)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "AppConfig":
        """从 YAML 文件加载配置。"""
        path = Path(path)
        if not path.exists():
            return cls()

        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        if raw is None:
            return cls()

        global_config = GlobalConfig(**(raw.get("global", {})))
        sites = {}
        for name, cfg in raw.get("sites", {}).items():
            sites[name] = SiteConfig(**cfg)

        return cls(global_config=global_config, sites=sites)

    def to_yaml(self, path: str | Path) -> None:
        """将当前配置写入 YAML 文件。"""
        raw = {
            "global": self.global_config.model_dump(mode="json"),
            "sites": {k: v.model_dump(mode="json") for k, v in self.sites.items()},
        }
        Path(path).write_text(yaml.dump(raw, default_flow_style=False, allow_unicode=True), encoding="utf-8")
