"""Model 单元测试。"""

from datetime import datetime, timezone

from silkworm.models import (
    CheckResult,
    FrameworkProfile,
    PageRecord,
    QualityReport,
    SiteConfig,
)


class TestSiteConfig:
    def test_default_values(self):
        cfg = SiteConfig(base_url="https://example.com")
        assert cfg.base_url == "https://example.com"
        assert cfg.crawl_delay == 1.0
        assert cfg.max_concurrency == 4
        assert cfg.respect_robots is True
        assert cfg.force_render is False

    def test_custom_values(self):
        cfg = SiteConfig(
            base_url="https://docs.docker.com",
            allow_patterns=[r"/engine/"],
            deny_patterns=[r"/reference/api/"],
            crawl_delay=2.0,
            max_concurrency=2,
            force_render=True,
        )
        assert cfg.allow_patterns == [r"/engine/"]
        assert cfg.deny_patterns == [r"/reference/api/"]
        assert cfg.crawl_delay == 2.0
        assert cfg.max_concurrency == 2
        assert cfg.force_render is True


class TestFrameworkProfile:
    def test_default_profile(self):
        profile = FrameworkProfile(name="mkdocs-material")
        assert profile.name == "mkdocs-material"
        assert profile.main_selector == ""
        assert profile.remove_selectors == []
        assert profile.boilerplate_phrases == []
        assert profile.code_lang_class_map == {}

    def test_full_profile(self):
        profile = FrameworkProfile(
            name="docusaurus",
            main_selector=".theme-doc-markdown",
            remove_selectors=[".sidebar", "nav"],
            boilerplate_phrases=["Edit this page"],
            code_lang_class_map={"language-js": "javascript"},
        )
        assert profile.main_selector == ".theme-doc-markdown"
        assert "Edit this page" in profile.boilerplate_phrases
        assert profile.code_lang_class_map["language-js"] == "javascript"


class TestPageRecord:
    def test_default_status(self):
        record = PageRecord(url="https://example.com/page")
        assert record.status == "pending"
        assert record.http_status == 0
        assert record.content_hash == ""

    def test_fetched_record(self):
        record = PageRecord(
            url="https://example.com/page",
            canonical_url="https://example.com/page",
            fetched_at=datetime.now(timezone.utc),
            content_hash="abc123",
            http_status=200,
            raw_html_path="cocoon/abc.html",
            framework="mkdocs-material",
            status="fetched",
        )
        assert record.http_status == 200
        assert record.framework == "mkdocs-material"
        assert record.status == "fetched"


class TestQualityReport:
    def test_default_report(self):
        report = QualityReport()
        assert report.passed is False
        assert report.total_score == 0.0
        assert report.checks == {}
        assert report.message == ""

    def test_passing_report(self):
        check = CheckResult(passed=True, score=1.0, detail="OK")
        report = QualityReport(
            passed=True, total_score=0.95, checks={"length": check}
        )
        assert report.passed is True
        assert report.total_score == 0.95
        assert report.checks["length"].passed is True
