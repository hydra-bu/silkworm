from unittest.mock import patch


from silkworm.config import LLMConfig
from silkworm.spin.quality import (
    _parse_llm_quality_response,
    check_code_integrity,
    check_heading_jumps,
    check_length,
    check_link_density,
    check_noise_lines,
    evaluate,
    llm_quality_judge,
)


class TestCheckLength:
    def test_short_content_fails(self):
        result = check_length("short", min_length=500)
        assert result.passed is False
        assert result.score < 1.0

    def test_long_content_passes(self):
        long_text = "a" * 600
        result = check_length(long_text, min_length=500)
        assert result.passed is True
        assert result.score >= 1.0

    def test_score_scales_with_length(self):
        medium = check_length("a" * 250, min_length=500)
        assert 0 < medium.score < 1.0


class TestCheckCodeIntegrity:
    def test_even_backticks_pass(self):
        result = check_code_integrity("text ```code``` more ```code```")
        assert result.passed is True

    def test_odd_backticks_fail(self):
        result = check_code_integrity("text ```code``` ```unclosed")
        assert result.passed is False

    def test_no_backticks_pass(self):
        result = check_code_integrity("just text")
        assert result.passed is True


class TestCheckHeadingJumps:
    def test_no_jumps_passes(self):
        content = "# H1\n## H2\n### H3\n## H2"
        result = check_heading_jumps(content)
        assert result.passed is True

    def test_single_heading_passes(self):
        content = "# Only one"
        result = check_heading_jumps(content)
        assert result.passed is True

    def test_excessive_jumps_fail(self):
        content = "# H1\n#### H4\n###### H6\n## H2\n###### H6\n##### H5"
        result = check_heading_jumps(content)
        assert result.passed is False


class TestCheckNoiseLines:
    def test_clean_content_passes(self):
        result = check_noise_lines("Clean article content with no noise")
        assert result.passed is True
        assert result.score == 1.0

    def test_noisy_phrases_detected(self):
        noisy = "Edit this page\nContent\nWas this page helpful?\nMore content\nCopyright 2024"
        result = check_noise_lines(noisy)
        assert result.score < 1.0

    def test_high_link_density_fails(self):
        content = "\n".join(f"[Link {i}](https://example.com/{i})" for i in range(50))
        result = check_noise_lines(content)
        assert result.passed is False

    def test_low_link_density_passes(self):
        content = "Normal paragraph text with one [link](https://example.com)."
        result = check_noise_lines(content)
        assert result.passed is True

    def test_detail_contains_density_info(self):
        content = "\n".join(f"[Link {i}](https://example.com/{i})" for i in range(30))
        result = check_noise_lines(content)
        assert "链接密度" in result.detail
        assert "每千字符链接数" in result.detail


class TestCheckLinkDensity:
    def test_low_density_passes(self):
        result = check_link_density("Normal text with one [link](https://example.com).")
        assert result.passed is True
        assert result.score > 0.0

    def test_high_density_fails(self):
        links = " ".join(f"[Link{i}](https://example.com/{i})" for i in range(40))
        result = check_link_density(links)
        assert result.passed is False

    def test_custom_threshold(self):
        links = " ".join(f"[Link{i}](https://example.com/{i})" for i in range(5))
        result = check_link_density(links, max_per_1k=2.0)
        assert result.score < 1.0


class TestEvaluate:
    def test_good_content_passes(self):
        content = "# Title\n\n" + "a" * 600 + "\n\n```python\nprint('hi')\n```"
        report = evaluate(content)
        assert report.passed is True
        assert report.total_score > 0.6

    def test_poor_content_fails(self):
        content = "# H1\n##### H5\n```\nunclosed fence\nEdit this page\nWas this helpful?"
        report = evaluate(content)
        assert report.passed is False
        assert report.total_score < 0.5

    def test_report_contains_all_checks(self):
        report = evaluate("sample content for testing")
        assert "length" in report.checks
        assert "code_integrity" in report.checks
        assert "heading_jumps" in report.checks
        assert "noise_lines" in report.checks

    def test_report_message_on_failure(self):
        content = "# H1\n##### H5\n```\nopen fence\nEdit this page"
        report = evaluate(content)
        assert report.passed is False
        assert isinstance(report.message, str)
        assert len(report.message) > 0

    def test_short_content_has_retry_action(self):
        content = "# H1\nToo short.\nEdit this page"
        report = evaluate(content, min_length=500)
        assert report.failed_filter is not None
        assert report.retry_action is not None
        assert "main_content" in report.retry_action.filter_name

    def test_code_failure_has_retry_action(self):
        content = "# Title\n\n" + "a" * 600 + "\n\n```orphan fence\n"
        report = evaluate(content)
        assert report.retry_action is not None
        assert report.retry_action.action == "skip_filter"

    def test_noise_failure_has_retry_action(self):
        noisy = "# Title\n\n" + "a" * 600 + "\n\n" + "\n".join(
            f"[Link{i}](https://example.com/{i})" for i in range(30)
        )
        report = evaluate(noisy)
        # noise check fails on link density, but total_score >= threshold so passes overall
        assert not report.checks["noise_lines"].passed
        assert report.retry_action is not None
        assert report.retry_action.action == "add_boilerplate"

    def test_good_content_no_retry_action(self):
        content = "# Title\n\n" + "a" * 600 + "\n\n```python\nprint('ok')\n```"
        report = evaluate(content)
        assert report.retry_action is None
        assert report.failed_filter is None

    def test_gray_zone_triggers_llm_with_config(self):
        """灰色区间 + 有 LLM 配置时调用 LLM 复核。"""
        content = "# Title\n\n" + "a" * 200 + "\n\n```\nunclosed"
        with patch("silkworm.spin.quality.llm_quality_judge", return_value={
            "passed": True, "score": 0.85, "reason": "Looks OK"
        }) as mock_llm:
            llm_cfg = LLMConfig(model="gpt-4o-mini")
            report = evaluate(content, min_length=500, score_threshold=0.7,
                              llm_config=llm_cfg)
            mock_llm.assert_called_once()
            assert report.needs_llm is True
            assert report.llm_passed is True
            assert report.passed is True

    def test_gray_zone_no_llm_config(self):
        """灰色区间但未配置 LLM → needs_llm=True, llm_passed=None。"""
        content = "# Title\n\n" + "a" * 200 + "\n\n```\nunclosed"
        report = evaluate(content, min_length=500, score_threshold=0.7,
                          llm_config=None)
        assert report.needs_llm is True
        assert report.llm_passed is None

    def test_llm_passes_overrides_failed(self):
        """灰色区间 + LLM 说通过 → passed 覆盖为 True。"""
        content = "# Title\n\n" + "a" * 200 + "\n\n```\nunclosed"
        with patch("silkworm.spin.quality.llm_quality_judge", return_value={
            "passed": True, "score": 0.9, "reason": "Acceptable"
        }):
            llm_cfg = LLMConfig(model="gpt-4o-mini")
            report = evaluate(content, min_length=500, score_threshold=0.7,
                              llm_config=llm_cfg)
            assert report.passed is True
            assert report.llm_passed is True

    def test_llm_fails_no_override(self):
        """灰色区间 + LLM 说不通过 → passed 保持 False。"""
        content = "# Title\n\n" + "a" * 200 + "\n\n```\nunclosed"
        with patch("silkworm.spin.quality.llm_quality_judge", return_value={
            "passed": False, "score": 0.2, "reason": "Truncated content"
        }):
            llm_cfg = LLMConfig(model="gpt-4o-mini")
            report = evaluate(content, min_length=500, score_threshold=0.7,
                              llm_config=llm_cfg)
            assert report.passed is False
            assert report.llm_passed is False

    def test_llm_not_called_when_score_above_gray_zone(self):
        """总分高于灰色区间上限 → 不调 LLM。"""
        content = "# Title\n\n" + "a" * 600 + "\n\n```python\nprint('ok')\n```"
        with patch("silkworm.spin.quality.llm_quality_judge") as mock_llm:
            report = evaluate(content, llm_config=LLMConfig(model="gpt-4o-mini"))
            mock_llm.assert_not_called()
            assert report.needs_llm is False
            assert report.passed is True

    def test_llm_not_called_when_score_below_gray_zone(self):
        """总分低于灰色区间下限 → 不调 LLM。"""
        content = "tiny"
        with patch("silkworm.spin.quality.llm_quality_judge") as mock_llm:
            report = evaluate(content, min_length=500, llm_config=LLMConfig(model="gpt-4o-mini"))
            mock_llm.assert_not_called()
            assert report.needs_llm is False


class TestParseLLMQualityResponse:
    def test_parse_valid_json(self):
        raw = '{"passed": true, "score": 0.95, "reason": "Clean content"}'
        result = _parse_llm_quality_response(raw)
        assert result is not None
        assert result["passed"] is True
        assert result["score"] == 0.95
        assert "Clean content" in result["reason"]

    def test_parse_with_markdown_fence(self):
        raw = '```json\n{"passed": false, "score": 0.3, "reason": "Truncated"}\n```'
        result = _parse_llm_quality_response(raw)
        assert result is not None
        assert result["passed"] is False
        assert result["score"] == 0.3

    def test_parse_missing_passed(self):
        result = _parse_llm_quality_response('{"score": 0.5}')
        assert result is None

    def test_parse_empty(self):
        assert _parse_llm_quality_response("") is None
        assert _parse_llm_quality_response(None) is None

    def test_parse_malformed_json(self):
        result = _parse_llm_quality_response('{"passed": true, broken')
        assert result is None

    def test_parse_no_json_object(self):
        result = _parse_llm_quality_response("Just plain text")
        assert result is None


class TestLLMQualityJudge:
    def test_no_api_key_returns_none(self):
        cfg = LLMConfig(model="gpt-4o-mini", api_key="")
        result = llm_quality_judge("content", cfg)
        assert result is None

    def test_truncates_long_content(self):
        cfg = LLMConfig(model="gpt-4o-mini", api_key="sk-test")
        with patch("silkworm.spin.quality.OpenAI") as mock_openai:
            mock_instance = mock_openai.return_value
            mock_instance.chat.completions.create.return_value.choices[0].message.content = (
                '{"passed": true, "score": 0.9, "reason": "OK"}'
            )
            long_content = "x" * 10_000
            result = llm_quality_judge(long_content, cfg)
            mock_openai.assert_called_once()
            call_kwargs = mock_instance.chat.completions.create.call_args.kwargs
            user_msg = call_kwargs["messages"][1]["content"]
            assert len(user_msg) < 9000  # truncated to 8000 + suffix
            assert "[... content truncated ...]" in user_msg
            assert result is not None

    def test_openai_error_returns_none(self):
        cfg = LLMConfig(model="gpt-4o-mini", api_key="sk-test")
        with patch("silkworm.spin.quality.OpenAI") as mock_openai:
            mock_instance = mock_openai.return_value
            mock_instance.chat.completions.create.side_effect = RuntimeError("API down")
            result = llm_quality_judge("content", cfg)
            assert result is None

    def test_passes_correct_params(self):
        cfg = LLMConfig(
            model="gpt-4o-mini",
            api_key="sk-test",
            base_url="https://custom.endpoint/v1",
            temperature=0.3,
        )
        with patch("silkworm.spin.quality.OpenAI") as mock_openai:
            mock_instance = mock_openai.return_value
            mock_instance.chat.completions.create.return_value.choices[0].message.content = (
                '{"passed": true, "score": 1.0, "reason": "Perfect"}'
            )
            result = llm_quality_judge("content", cfg)
            mock_openai.assert_called_once_with(
                base_url="https://custom.endpoint/v1",
                api_key="sk-test",
            )
            call_kwargs = mock_instance.chat.completions.create.call_args.kwargs
            assert call_kwargs["model"] == "gpt-4o-mini"
            assert call_kwargs["temperature"] == 0.3
            assert call_kwargs["max_tokens"] == 512
            assert result["passed"] is True


class TestEvaluateCodePreservedHardFail:
    """硬失败项：原始有代码但输出 0 围栏 → 不通过。"""

    _LONG = "# Title\n\n" + ("word " * 200) + "\n"

    def test_hard_fail_when_code_lost(self):
        content = "# Title\n\n" + ("word " * 200) + "\n"
        report = evaluate(content, raw_code_count=3)
        assert report.passed is False
        assert "code_preserved" in report.checks
        assert report.checks["code_preserved"].passed is False

    def test_pass_when_code_preserved(self):
        content = "# Title\n\n```python\nprint(1)\n```\n\n" + ("word " * 200) + "\n"
        report = evaluate(content, raw_code_count=3)
        assert report.passed is True
        assert "code_preserved" not in report.checks

    def test_no_baseline_unchanged(self):
        content = "# Title\n\n" + ("word " * 200) + "\n"
        report = evaluate(content)
        assert "code_preserved" not in report.checks
        assert report.passed is True
