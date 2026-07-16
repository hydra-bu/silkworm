"""Config 单元测试。"""

from pathlib import Path

import yaml

from silkworm.config import AppConfig, GlobalConfig
from silkworm.models import SiteConfig


class TestGlobalConfig:
    def test_defaults(self):
        cfg = GlobalConfig()
        assert cfg.cocoon_dir == Path("cocoon")
        assert cfg.silk_dir == Path("silk")
        assert cfg.max_retries == 2
        assert cfg.quality_min_length == 500


class TestAppConfig:
    def test_empty_config(self):
        cfg = AppConfig()
        assert cfg.global_config is not None
        assert cfg.sites == {}

    def test_add_site(self):
        cfg = AppConfig()
        cfg.sites["k8s"] = SiteConfig(base_url="https://kubernetes.io")
        assert "k8s" in cfg.sites
        assert cfg.sites["k8s"].base_url == "https://kubernetes.io"

    def test_yaml_roundtrip(self, tmp_path: Path):
        cfg = AppConfig()
        cfg.sites["test"] = SiteConfig(
            base_url="https://example.com",
            allow_patterns=[r"/docs/"],
            crawl_delay=2.0,
        )

        yaml_path = tmp_path / "silkworm.yaml"
        cfg.to_yaml(str(yaml_path))
        assert yaml_path.exists()

        loaded = AppConfig.from_yaml(str(yaml_path))
        assert "test" in loaded.sites
        assert loaded.sites["test"].base_url == "https://example.com"
        assert loaded.sites["test"].crawl_delay == 2.0
        assert loaded.sites["test"].allow_patterns == [r"/docs/"]

    def test_from_yaml_nonexistent(self):
        cfg = AppConfig.from_yaml("/nonexistent/path.yaml")
        assert cfg.global_config is not None
        assert cfg.sites == {}

    def test_from_yaml_with_global(self, tmp_path: Path):
        data = {
            "global": {"cocoon_dir": "custom_cocoon", "max_retries": 5},
            "sites": {
                "docs": {"base_url": "https://docs.example.com"}
            },
        }
        path = tmp_path / "test.yaml"
        path.write_text(yaml.dump(data), encoding="utf-8")

        cfg = AppConfig.from_yaml(str(path))
        assert cfg.global_config.cocoon_dir == Path("custom_cocoon")
        assert cfg.global_config.max_retries == 5
        assert "docs" in cfg.sites
