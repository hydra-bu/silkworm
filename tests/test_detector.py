"""Detector 单元测试。"""

from silkworm.spin.detector import BUILTIN_PROFILES, detect_framework, get_profile


class TestDetectFramework:
    def test_detect_mkdocs_material(self):
        html = """<html><head><meta name="generator" content="mkdocs"></head>
                  <body><div class="md-content">Content</div></body></html>"""
        assert detect_framework(html) == "mkdocs-material"

    def test_detect_docusaurus(self):
        html = """<html><head><meta name="generator" content="docusaurus"></head>
                  <body><div id="__docusaurus">Content</div></body></html>"""
        assert detect_framework(html) == "docusaurus"

    def test_detect_sphinx(self):
        html = """<html><body><div class="rst-content">Content</div>
                  <div role="navigation" class="sphinxsidebar">Nav</div></body></html>"""
        assert detect_framework(html) == "sphinx"

    def test_fallback_to_generic(self):
        html = "<html><body><div>No framework markers</div></body></html>"
        assert detect_framework(html) == "generic"

    def test_empty_html_returns_generic(self):
        assert detect_framework("") == "generic"

    def test_invalid_html_returns_generic(self):
        assert detect_framework("not html at all <<>>") == "generic"

    def test_detect_kubernetes(self):
        html = """<html><body>
                  <main data-pagefind-body><div class="td-content">K8s docs content</div></main>
                  </body></html>"""
        assert detect_framework(html) == "kubernetes"

    def test_highest_score_wins(self):
        html = """<html><head><meta name="generator" content="mkdocs"></head>
                  <body><div class="md-content">Content</div>
                  <div class="rst-content">Also Sphinx-like</div></body></html>"""
        # mkdocs-material score: 3 (meta) + 2 (.md-content) = 5
        # sphinx score: 2 (.rst-content) = 2
        assert detect_framework(html) == "mkdocs-material"


class TestGetProfile:
    def test_known_framework(self):
        profile = get_profile("mkdocs-material")
        assert profile.name == "mkdocs-material"
        assert profile.main_selector == ".md-content"

    def test_unknown_framework_returns_generic(self):
        profile = get_profile("nonexistent-framework")
        assert profile.name == "generic"

    def test_generic_has_main_selector(self):
        profile = get_profile("generic")
        assert "main" in profile.main_selector
        assert "article" in profile.main_selector

    def test_builtin_profiles_loaded(self):
        assert "kubernetes" in BUILTIN_PROFILES
        assert "mkdocs-material" in BUILTIN_PROFILES
        assert "docusaurus" in BUILTIN_PROFILES
        assert "sphinx" in BUILTIN_PROFILES
        assert "generic" in BUILTIN_PROFILES
