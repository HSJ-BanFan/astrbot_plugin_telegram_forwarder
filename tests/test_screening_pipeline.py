"""Unit tests for ContentScreeningPipeline and typed contracts."""

import asyncio
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

_repo_root = Path(__file__).resolve().parents[1]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from core.filters.content_safety import ContentSafetyFilter
from core.filters.screening_pipeline import (
    ChannelScreeningPolicy,
    ContentScreeningPipeline,
    ScreeningStage,
    ScreeningVerdict,
    VerdictAction,
)


def _msg(text=None, *, buttons=None, declared_size=None):
    """Create a lightweight Message test double."""
    msg = SimpleNamespace(text=text, id=1001)
    if buttons:
        rows = []
        for btn_list in buttons:
            row = SimpleNamespace(
                buttons=[SimpleNamespace(text=b) for b in btn_list]
            )
            rows.append(row)
        msg.reply_markup = SimpleNamespace(rows=rows)
    else:
        msg.reply_markup = None

    if declared_size is not None:
        msg.file = SimpleNamespace(size=declared_size)
    else:
        msg.file = None
    return msg


class TestScreeningVerdictContract:
    def test_verdict_allow_properties(self):
        v = ScreeningVerdict.allow(stage=ScreeningStage.INGESTION, reason="ok")
        assert v.action == VerdictAction.ALLOW
        assert v.is_allowed is True
        assert v.is_dropped is False
        assert v.stage == ScreeningStage.INGESTION
        assert v.reason == "ok"
        assert v.matched_policy is None

    def test_verdict_drop_properties(self):
        v = ScreeningVerdict.drop(
            reason="Blocked keyword",
            stage=ScreeningStage.DISPATCH,
            matched_policy="bad_word",
        )
        assert v.action == VerdictAction.DROP
        assert v.is_allowed is False
        assert v.is_dropped is True
        assert v.stage == ScreeningStage.DISPATCH
        assert v.reason == "Blocked keyword"
        assert v.matched_policy == "bad_word"

    def test_verdict_immutable(self):
        v = ScreeningVerdict(action=VerdictAction.ALLOW)
        with pytest.raises(AttributeError):
            v.reason = "modified"  # type: ignore


class TestFastPathKeywordAndRegex:
    @pytest.mark.asyncio
    async def test_keyword_drop_in_ingestion(self):
        config = {"forward_config": {"filter_keywords": ["spam"]}}
        pipeline = ContentScreeningPipeline(config)

        verdict = await pipeline.evaluate(
            _msg("this is spam message"), stage=ScreeningStage.INGESTION
        )
        assert verdict.is_dropped
        assert verdict.action == VerdictAction.DROP
        assert verdict.stage == ScreeningStage.INGESTION
        assert verdict.matched_policy == "spam"
        assert "spam" in verdict.reason

    @pytest.mark.asyncio
    async def test_keyword_case_insensitivity(self):
        config = {"forward_config": {"filter_keywords": ["SPAM"]}}
        pipeline = ContentScreeningPipeline(config)

        verdict = await pipeline.evaluate(
            _msg("Notice: this is spam!"), stage=ScreeningStage.INGESTION
        )
        assert verdict.is_dropped

    @pytest.mark.asyncio
    async def test_keyword_boundary_vs_substring(self):
        config = {"forward_config": {"filter_keywords": ["cat", "优惠"]}}
        pipeline = ContentScreeningPipeline(config)

        # ASCII boundary check: "cat" should match "the cat sat", but NOT "catch"
        v_boundary_hit = await pipeline.evaluate(
            _msg("the cat sat on mat"), stage=ScreeningStage.INGESTION
        )
        assert v_boundary_hit.is_dropped

        v_boundary_miss = await pipeline.evaluate(
            _msg("catch me if you can"), stage=ScreeningStage.INGESTION
        )
        assert v_boundary_miss.is_allowed

        # Non-ASCII substring check: "优惠" should match inside compound text
        v_cjk_hit = await pipeline.evaluate(
            _msg("限时优惠进行中"), stage=ScreeningStage.INGESTION
        )
        assert v_cjk_hit.is_dropped

    @pytest.mark.asyncio
    async def test_regex_pattern_drop(self):
        config = {"forward_config": {"filter_regex": r"\b\d{4}\b"}}
        pipeline = ContentScreeningPipeline(config)

        hit = await pipeline.evaluate(
            _msg("Your pin is 1234 now"), stage=ScreeningStage.INGESTION
        )
        assert hit.is_dropped
        assert hit.matched_policy == r"\b\d{4}\b"

        miss = await pipeline.evaluate(
            _msg("Pin is not ready"), stage=ScreeningStage.INGESTION
        )
        assert miss.is_allowed

    @pytest.mark.asyncio
    async def test_regex_case_sensitive(self):
        config = {"forward_config": {"filter_regex": "CRITICAL"}}
        pipeline = ContentScreeningPipeline(config)

        miss = await pipeline.evaluate(
            _msg("critical warning"), stage=ScreeningStage.INGESTION
        )
        assert miss.is_allowed

        hit = await pipeline.evaluate(
            _msg("CRITICAL warning"), stage=ScreeningStage.INGESTION
        )
        assert hit.is_dropped

    @pytest.mark.asyncio
    async def test_invalid_regex_resilience(self):
        config = {"forward_config": {"filter_regex": "((["}}
        pipeline = ContentScreeningPipeline(config)

        # Should not raise, should allow message
        verdict = await pipeline.evaluate(
            _msg("normal message"), stage=ScreeningStage.INGESTION
        )
        assert verdict.is_allowed

    @pytest.mark.asyncio
    async def test_search_text_extracted_from_buttons(self):
        config = {"forward_config": {"filter_keywords": ["ad_btn"]}}
        pipeline = ContentScreeningPipeline(config)

        msg = _msg("Clean text", buttons=[["normal", "ad_btn"]])
        verdict = await pipeline.evaluate(msg, stage=ScreeningStage.INGESTION)
        assert verdict.is_dropped
        assert verdict.matched_policy == "ad_btn"


class TestDualStageEvaluation:
    @pytest.mark.asyncio
    async def test_ingestion_stage_ignores_visual_safety(self):
        mock_safety = MagicMock(spec=ContentSafetyFilter)
        mock_safety.check_qr = MagicMock(
            return_value={"filter": True, "msg": "QR risk"}
        )
        mock_safety.check_ai = AsyncMock(
            return_value={"filter": True, "msg": "AI risk"}
        )

        config = {
            "forward_config": {
                "qr_filter_enabled": True,
                "ai_filter_enabled": True,
            }
        }
        pipeline = ContentScreeningPipeline(
            config, content_safety_filter=mock_safety
        )

        # In INGESTION stage: visual checks must not be executed
        verdict = await pipeline.evaluate(
            _msg("clean text"),
            stage=ScreeningStage.INGESTION,
            image_bytes=b"fake_image",
        )
        assert verdict.is_allowed
        mock_safety.check_qr.assert_not_called()
        mock_safety.check_ai.assert_not_called()

    @pytest.mark.asyncio
    async def test_dispatch_stage_evaluates_text_first(self):
        mock_safety = MagicMock(spec=ContentSafetyFilter)
        mock_safety.check_qr = MagicMock()
        mock_safety.check_ai = AsyncMock()

        config = {
            "forward_config": {
                "filter_keywords": ["forbidden"],
                "qr_filter_enabled": True,
            }
        }
        pipeline = ContentScreeningPipeline(
            config, content_safety_filter=mock_safety
        )

        verdict = await pipeline.evaluate(
            _msg("forbidden content"),
            stage=ScreeningStage.DISPATCH,
            image_bytes=b"fake_image",
        )
        assert verdict.is_dropped
        assert verdict.stage == ScreeningStage.DISPATCH
        assert verdict.matched_policy == "forbidden"
        mock_safety.check_qr.assert_not_called()
        mock_safety.check_ai.assert_not_called()


class TestLocalQRAndAIModeration:
    @pytest.mark.asyncio
    async def test_qr_risk_detection_drop(self):
        mock_safety = MagicMock(spec=ContentSafetyFilter)
        mock_safety.check_qr = MagicMock(
            return_value={"filter": True, "msg": "二维码命中网贷规则"}
        )

        config = {"forward_config": {"qr_filter_enabled": True}}
        pipeline = ContentScreeningPipeline(
            config, content_safety_filter=mock_safety
        )

        verdict = await pipeline.evaluate(
            _msg("check photo"),
            stage=ScreeningStage.DISPATCH,
            image_bytes=b"qr_image_bytes",
        )
        assert verdict.is_dropped
        assert verdict.stage == ScreeningStage.DISPATCH
        assert verdict.matched_policy == "qr_filter"
        assert "二维码命中网贷规则" in verdict.reason

    @pytest.mark.asyncio
    async def test_ai_moderation_decision_drop(self):
        mock_safety = MagicMock(spec=ContentSafetyFilter)
        mock_safety.check_qr = MagicMock(return_value={"filter": False})
        mock_safety.check_ai = AsyncMock(
            return_value={"filter": True, "msg": "NSFW detected"}
        )

        config = {
            "forward_config": {
                "qr_filter_enabled": True,
                "ai_filter_enabled": True,
                "ai_filter_base_url": "https://api.example.com",
                "ai_filter_api_key": "secret",
                "ai_filter_model": "gpt-4o",
            }
        }
        pipeline = ContentScreeningPipeline(
            config, content_safety_filter=mock_safety
        )

        verdict = await pipeline.evaluate(
            _msg("nsfw picture"),
            stage=ScreeningStage.DISPATCH,
            image_bytes=b"image_bytes",
        )
        assert verdict.is_dropped
        assert verdict.stage == ScreeningStage.DISPATCH
        assert verdict.matched_policy == "ai_filter"
        assert "NSFW detected" in verdict.reason


class TestAIBudgetAndExhaustion:
    @pytest.mark.asyncio
    async def test_ai_budget_decrement_and_fallback_to_qr_only(self):
        mock_safety = MagicMock(spec=ContentSafetyFilter)
        mock_safety.check_qr = MagicMock(return_value={"filter": False})
        mock_safety.check_ai = AsyncMock(
            return_value={"filter": False, "msg": "Clean"}
        )

        config = {
            "forward_config": {
                "ai_filter_enabled": True,
                "ai_filter_max_calls_per_cycle": 2,
            }
        }
        pipeline = ContentScreeningPipeline(
            config, content_safety_filter=mock_safety
        )

        assert pipeline.get_remaining_ai_budget() == 2

        # 1st call
        v1 = await pipeline.evaluate(
            _msg("msg 1"),
            stage=ScreeningStage.DISPATCH,
            image_bytes=b"img1",
        )
        assert v1.is_allowed
        assert pipeline.get_remaining_ai_budget() == 1
        assert mock_safety.check_ai.call_count == 1

        # 2nd call
        v2 = await pipeline.evaluate(
            _msg("msg 2"),
            stage=ScreeningStage.DISPATCH,
            image_bytes=b"img2",
        )
        assert v2.is_allowed
        assert pipeline.get_remaining_ai_budget() == 0
        assert mock_safety.check_ai.call_count == 2

        # 3rd call: quota exhausted -> skip AI, allow through fallback
        v3 = await pipeline.evaluate(
            _msg("msg 3"),
            stage=ScreeningStage.DISPATCH,
            image_bytes=b"img3",
        )
        assert v3.is_allowed
        assert pipeline.get_remaining_ai_budget() == 0
        assert mock_safety.check_ai.call_count == 2  # Not called again!

        # Now test QR still runs when AI quota is exhausted:
        mock_safety.check_qr = MagicMock(
            return_value={"filter": True, "msg": "QR spam"}
        )
        pipeline_qr = ContentScreeningPipeline(
            {
                "forward_config": {
                    "qr_filter_enabled": True,
                    "ai_filter_enabled": True,
                    "ai_filter_max_calls_per_cycle": 0,
                }
            },
            content_safety_filter=mock_safety,
        )
        v_qr = await pipeline_qr.evaluate(
            _msg("msg with qr"),
            stage=ScreeningStage.DISPATCH,
            image_bytes=b"qr_img",
        )
        assert v_qr.is_dropped
        assert v_qr.matched_policy == "qr_filter"

    @pytest.mark.asyncio
    async def test_reset_cycle_budget(self):
        config = {
            "forward_config": {
                "ai_filter_enabled": True,
                "ai_filter_max_calls_per_cycle": 3,
            }
        }
        mock_safety = MagicMock(spec=ContentSafetyFilter)
        mock_safety.check_ai = AsyncMock(return_value={"filter": False})
        pipeline = ContentScreeningPipeline(
            config, content_safety_filter=mock_safety
        )

        assert pipeline.get_remaining_ai_budget() == 3
        await pipeline.evaluate(_msg("m"), stage=ScreeningStage.DISPATCH)
        assert pipeline.get_remaining_ai_budget() == 2

        pipeline.reset_cycle_budget()
        assert pipeline.get_remaining_ai_budget() == 3


class TestImageDegradation:
    @pytest.mark.asyncio
    async def test_oversized_image_degrades_to_text_only(self):
        mock_safety = MagicMock(spec=ContentSafetyFilter)
        mock_safety.check_qr = MagicMock()
        mock_safety.check_ai = AsyncMock(return_value={"filter": False})

        # Max 1 MB limit
        config = {
            "forward_config": {
                "content_filter_max_image_mb": 1,
                "qr_filter_enabled": True,
                "ai_filter_enabled": True,
            }
        }
        pipeline = ContentScreeningPipeline(
            config, content_safety_filter=mock_safety
        )

        # 1.5 MB image
        large_bytes = b"x" * int(1.5 * 1024 * 1024)
        verdict = await pipeline.evaluate(
            _msg("text message"),
            stage=ScreeningStage.DISPATCH,
            image_bytes=large_bytes,
        )

        assert verdict.is_allowed
        # QR was skipped because image was stripped due to size limit
        mock_safety.check_qr.assert_not_called()
        # AI was called with image_bytes=None
        mock_safety.check_ai.assert_called_once_with(
            "text message", None, mock_safety.check_ai.call_args[0][2]
        )

    @pytest.mark.asyncio
    async def test_declared_size_exceeded_degrades(self):
        mock_safety = MagicMock(spec=ContentSafetyFilter)
        mock_safety.check_qr = MagicMock()
        mock_safety.check_ai = AsyncMock(return_value={"filter": False})

        config = {
            "forward_config": {
                "content_filter_max_image_mb": 2,
                "qr_filter_enabled": True,
                "ai_filter_enabled": True,
            }
        }
        pipeline = ContentScreeningPipeline(
            config, content_safety_filter=mock_safety
        )

        # Declared size is 5 MB
        msg = _msg("text", declared_size=5 * 1024 * 1024)
        verdict = await pipeline.evaluate(
            msg, stage=ScreeningStage.DISPATCH, image_bytes=b"some_bytes"
        )

        assert verdict.is_allowed
        mock_safety.check_qr.assert_not_called()
        mock_safety.check_ai.assert_called_once_with(
            "text", None, mock_safety.check_ai.call_args[0][2]
        )


class TestChannelSpecificRuleOverrides:
    @pytest.mark.asyncio
    async def test_channel_keyword_supplements_global(self):
        config = {
            "forward_config": {"filter_keywords": ["global_spam"]},
            "source_channels": [
                {
                    "channel_username": "@target_ch",
                    "filter_keywords": ["local_ad"],
                }
            ],
        }
        pipeline = ContentScreeningPipeline(config)

        # Global channel check: drops global_spam, allows local_ad
        assert (
            await pipeline.evaluate(
                _msg("global_spam"), "other_ch", ScreeningStage.INGESTION
            )
        ).is_dropped
        assert (
            await pipeline.evaluate(
                _msg("local_ad"), "other_ch", ScreeningStage.INGESTION
            )
        ).is_allowed

        # target_ch check: drops BOTH global_spam and local_ad
        assert (
            await pipeline.evaluate(
                _msg("global_spam"), "target_ch", ScreeningStage.INGESTION
            )
        ).is_dropped
        assert (
            await pipeline.evaluate(
                _msg("local_ad"), "target_ch", ScreeningStage.INGESTION
            )
        ).is_dropped

    @pytest.mark.asyncio
    async def test_channel_ignore_global_filters(self):
        config = {
            "forward_config": {
                "filter_keywords": ["global_keyword"],
                "filter_regex": r"^\d{3}$",
            },
            "source_channels": [
                {
                    "channel_username": "rebel_ch",
                    "ignore_global_filters": True,
                    "filter_keywords": ["rebel_only"],
                }
            ],
        }
        pipeline = ContentScreeningPipeline(config)

        # rebel_ch should ignore global keyword & regex
        assert (
            await pipeline.evaluate(
                _msg("global_keyword"), "rebel_ch", ScreeningStage.INGESTION
            )
        ).is_allowed
        assert (
            await pipeline.evaluate(
                _msg("123"), "rebel_ch", ScreeningStage.INGESTION
            )
        ).is_allowed
        # rebel_ch only drops its own keyword
        assert (
            await pipeline.evaluate(
                _msg("rebel_only"), "rebel_ch", ScreeningStage.INGESTION
            )
        ).is_dropped

    @pytest.mark.asyncio
    async def test_channel_normalization_matching(self):
        config = {
            "source_channels": [
                {
                    "channel_username": "https://t.me/CryptoAlerts",
                    "filter_keywords": ["airdrop"],
                }
            ]
        }
        pipeline = ContentScreeningPipeline(config)

        # Matches username with @ or case variations
        assert (
            await pipeline.evaluate(
                _msg("airdrop here"), "@cryptoalerts", ScreeningStage.INGESTION
            )
        ).is_dropped
        assert (
            await pipeline.evaluate(
                _msg("airdrop here"), "cryptoalerts", ScreeningStage.INGESTION
            )
        ).is_dropped

    @pytest.mark.asyncio
    async def test_reload_config_clears_policy_cache(self):
        config_v1 = {
            "source_channels": [
                {"channel_username": "ch1", "filter_keywords": ["first_rule"]}
            ]
        }
        pipeline = ContentScreeningPipeline(config_v1)

        v1 = await pipeline.evaluate(
            _msg("first_rule"), "ch1", ScreeningStage.INGESTION
        )
        assert v1.is_dropped

        # Reload with new config
        config_v2 = {
            "source_channels": [
                {"channel_username": "ch1", "filter_keywords": ["second_rule"]}
            ]
        }
        pipeline.reload_config(config_v2)

        # Old rule no longer matches
        v2_old = await pipeline.evaluate(
            _msg("first_rule"), "ch1", ScreeningStage.INGESTION
        )
        assert v2_old.is_allowed

        # New rule matches
        v2_new = await pipeline.evaluate(
            _msg("second_rule"), "ch1", ScreeningStage.INGESTION
        )
        assert v2_new.is_dropped
