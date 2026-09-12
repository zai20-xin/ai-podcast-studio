"""后端单元测试：脚本解析、主播映射、指令构建"""
import unittest

from tests import os  # noqa: F401  确保环境变量已设置


class TestParseDialogue(unittest.TestCase):
    def test_mixed_colon_and_chapter(self):
        from app.api.podcast import parse_dialogue_lines, extract_speakers

        script = "A: 你好\nB：世界\n【章节】\n---\n旁白一句\nC:好"
        lines = parse_dialogue_lines(script)
        # 逐项比对说话人与文本（dialogue 现在还会带 gap_after 等元信息）
        self.assertEqual(
            [(l["speaker"], l["text"]) for l in lines],
            [("A", "你好"), ("B", "世界"), ("", "旁白一句"), ("C", "好")],
        )
        self.assertEqual(extract_speakers(lines), ["A", "B", "C"])

    def test_max_lines_cap(self):
        """超限不再静默截断。

        旧行为是 `dialogue[:MAX_DIALOGUE_LINES]`，超出部分被悄悄丢弃，
        用户会以为整篇都合成了。现在改为全部解析 + 显式报错。
        """
        from app.api.podcast import (
            parse_dialogue_lines,
            assert_within_line_limit,
            MAX_DIALOGUE_LINES,
        )

        script = "\n".join(f"A: 第{i}句" for i in range(MAX_DIALOGUE_LINES + 20))
        lines = parse_dialogue_lines(script)
        # 一句都不能丢
        self.assertEqual(len(lines), MAX_DIALOGUE_LINES + 20)
        # 超限由专门函数报错，且错误信息里带上实际句数与上限
        with self.assertRaises(ValueError) as ctx:
            assert_within_line_limit(lines)
        self.assertIn(str(MAX_DIALOGUE_LINES), str(ctx.exception))

    def test_within_limit_passes(self):
        from app.api.podcast import (
            parse_dialogue_lines,
            assert_within_line_limit,
            MAX_DIALOGUE_LINES,
        )

        script = "\n".join(f"A: 第{i}句" for i in range(MAX_DIALOGUE_LINES))
        lines = parse_dialogue_lines(script)
        self.assertEqual(len(lines), MAX_DIALOGUE_LINES)
        assert_within_line_limit(lines)  # 恰好等于上限不应报错

    def test_empty(self):
        from app.api.podcast import parse_dialogue_lines

        self.assertEqual(parse_dialogue_lines("   \n\n"), [])


class TestGradedPause(unittest.TestCase):
    """停顿分级：段落与章节处的停顿应明显长于句间"""

    def test_parse_marks_gap_levels(self):
        from app.api.podcast import parse_dialogue_lines
        from app.config import GAP_NONE, GAP_SHORT, GAP_PARAGRAPH, GAP_SECTION

        script = "A: 句一。\nA: 句二。\n\n【第一章】\nA: 章内句。\n\nA: 新段。\nA: 收尾。"
        gaps = [l["gap_after"] for l in parse_dialogue_lines(script)]
        self.assertEqual(gaps[0], GAP_SHORT)      # 后面是普通换行
        self.assertEqual(gaps[1], GAP_SECTION)    # 后面紧接【第一章】
        self.assertEqual(gaps[2], GAP_PARAGRAPH)  # 后面是空行
        self.assertEqual(gaps[3], GAP_SHORT)
        self.assertEqual(gaps[-1], GAP_NONE)      # 最后一句之后不再停顿

    def test_gap_lengths_are_ordered(self):
        from app.config import (
            SILENCE_GAP_SHORT_MS,
            SILENCE_GAP_PARAGRAPH_MS,
            SILENCE_GAP_SECTION_MS,
        )

        self.assertLess(SILENCE_GAP_SHORT_MS, SILENCE_GAP_PARAGRAPH_MS)
        self.assertLess(SILENCE_GAP_PARAGRAPH_MS, SILENCE_GAP_SECTION_MS)


class TestPickHost(unittest.TestCase):
    def test_mapping(self):
        from app.api.podcast import _pick_host_config
        from app.schemas.episode import HostConfig, ModelType

        a = HostConfig(model_type=ModelType.builtin, speaker_names=["A"])
        b = HostConfig(model_type=ModelType.clone, speaker_names=["B", "C"])
        self.assertIs(_pick_host_config("A", a, b), a)
        self.assertIs(_pick_host_config("B", a, b), b)
        self.assertIs(_pick_host_config("C", a, b), b)
        # 未映射角色默认给 B
        self.assertIs(_pick_host_config("Z", a, b), b)

    def test_no_mapping_single(self):
        from app.api.podcast import _pick_host_config
        from app.schemas.episode import HostConfig

        a = HostConfig()
        self.assertIs(_pick_host_config("", a, None), a)
        self.assertIs(_pick_host_config("旁白", a, None), a)


class TestTTSInstruction(unittest.TestCase):
    def test_build_and_tags(self):
        from app.services.tts_service import TTSService, convert_tags, STYLE_PRESETS

        svc = TTSService()
        text = svc._build_instruction(
            style="温柔", speed="偏慢", emotion="平静", global_instruction="口语化"
        )
        self.assertIn("口语化", text)
        self.assertIn(STYLE_PRESETS["温柔"], text)
        self.assertIn("语速偏慢", text)
        self.assertIn("整体情绪偏平静", text)
        tagged = svc._build_audio_tag_text("[laugh]你好", "温柔")
        self.assertTrue(tagged.startswith("(温柔)"))
        self.assertIn("(轻笑)你好", tagged)
        self.assertEqual(convert_tags("[sigh]唉"), "(叹气)唉")

    def test_encode_rejects_outside_voices_dir(self):
        from app.services.tts_service import TTSService

        svc = TTSService()
        with self.assertRaises(ValueError):
            svc._encode_audio("/etc/hosts")


class TestEstimate(unittest.TestCase):
    def test_duration(self):
        from app.api.podcast import estimate_duration_seconds

        lines = [{"text": "一二三四五六七八九十", "speaker": "A"}] * 2
        sec = estimate_duration_seconds(lines)
        self.assertGreater(sec, 0)


class TestReclaim(unittest.TestCase):
    def test_not_processing(self):
        from types import SimpleNamespace
        from datetime import datetime
        from app.api.podcast import _reclaim_if_stale

        ep = SimpleNamespace(id=1, status="done", processing_heartbeat=datetime.utcnow())
        self.assertFalse(_reclaim_if_stale(ep))

    def test_stale_processing_no_lock(self):
        from types import SimpleNamespace
        from datetime import datetime, timedelta
        from app.api.podcast import _reclaim_if_stale

        ep = SimpleNamespace(
            id=99991,
            status="processing",
            processing_heartbeat=datetime.utcnow() - timedelta(hours=2),
        )
        self.assertTrue(_reclaim_if_stale(ep))


class TestInstructionNoConflict(unittest.TestCase):
    """指令必须做到维度职责分离。

    旧实现里场景预设、风格字典、语速字典都在描述语速，
    拼接后同一条指令会出现多种互斥的语速要求，模型行为不可预测。
    """

    def test_scene_presets_mention_speed_exactly_once(self):
        from app.services.tts_service import TTSService
        from app.services.presets import SCENE_PRESETS

        svc = TTSService()
        for name, p in SCENE_PRESETS.items():
            text = svc._build_instruction(
                p["style"], p["speed"], p["emotion"], p["global_instruction"]
            )
            self.assertEqual(
                text.count("语速"), 1, f"「{name}」的语速描述出现了 {text.count('语速')} 次"
            )

    def test_style_presets_do_not_claim_speed(self):
        from app.services.tts_service import STYLE_PRESETS

        for name, text in STYLE_PRESETS.items():
            self.assertNotIn("语速", text, f"风格「{name}」不应声称语速（语速由语速字典单独负责）")

    def test_preset_directions_are_dimension_pure(self):
        from app.services.presets import SCENE_PRESETS

        for name, p in SCENE_PRESETS.items():
            self.assertNotIn(
                "语速", p["global_instruction"], f"预设「{name}」的情境描述不应内嵌语速"
            )
            self.assertEqual(p["style"], "", f"预设「{name}」不应预设风格，否则与情境描述重复")
            self.assertEqual(p["emotion"], "", f"预设「{name}」不应预设情绪，否则与情境描述重复")

    def test_manual_override_still_works(self):
        """用户在高级设置里手动覆盖风格与情绪时，仍应正确组装"""
        from app.services.tts_service import TTSService
        from app.services.presets import SCENE_PRESETS

        svc = TTSService()
        text = svc._build_instruction(
            "紧张", "偏快", "焦虑", SCENE_PRESETS["🔍 悬疑讲述"]["global_instruction"]
        )
        self.assertIn("紧张", text)
        self.assertIn("整体情绪偏焦虑", text)
        self.assertIn("语速偏快", text)
        self.assertEqual(text.count("语速"), 1)


class TestConfigFingerprint(unittest.TestCase):
    """配置指纹：决定某一句能否复用已有音频，避免「改一个字整篇重跑」"""

    @staticmethod
    def _fp(**overrides):
        from app.api.podcast import _config_fingerprint
        from app.schemas.episode import HostConfig

        base = {"model_type": "builtin", "voice_id": "冰糖", "style": "温柔", "speed": "正常"}
        base.update(overrides)
        return _config_fingerprint(HostConfig(**base), "口语化", "你好世界", "A")

    def test_same_input_is_stable(self):
        self.assertEqual(self._fp(), self._fp())

    def test_text_change_invalidates(self):
        from app.api.podcast import _config_fingerprint
        from app.schemas.episode import HostConfig

        cfg = HostConfig(model_type="builtin", voice_id="冰糖")
        self.assertNotEqual(
            _config_fingerprint(cfg, None, "第一句", "A"),
            _config_fingerprint(cfg, None, "第一句改了", "A"),
        )

    def test_voice_change_invalidates(self):
        self.assertNotEqual(self._fp(voice_id="冰糖"), self._fp(voice_id="茉莉"))

    def test_style_change_invalidates(self):
        self.assertNotEqual(self._fp(style="温柔"), self._fp(style="兴奋"))

    def test_global_instruction_change_invalidates(self):
        from app.api.podcast import _config_fingerprint
        from app.schemas.episode import HostConfig

        cfg = HostConfig(model_type="builtin", voice_id="冰糖")
        self.assertNotEqual(
            _config_fingerprint(cfg, "口语化", "句子", "A"),
            _config_fingerprint(cfg, "播报腔", "句子", "A"),
        )


class TestTaskRunner(unittest.TestCase):
    """合成任务生命周期：可启动、可取消、可查询"""

    def test_start_is_idempotent_while_running(self):
        import asyncio
        from app.services import task_runner

        async def main():
            async def work():
                await asyncio.sleep(5)

            self.assertTrue(task_runner.start(4242, work()))
            self.assertTrue(task_runner.is_running(4242))
            # 同一 episode 重复启动应被拒绝，且不泄漏 coroutine
            self.assertFalse(task_runner.start(4242, work()))
            task_runner.cancel(4242)
            await asyncio.sleep(0.05)
            self.assertFalse(task_runner.is_running(4242))
            task_runner.clear(4242)

        asyncio.run(main())

    def test_cancel_marks_flag_and_can_be_cleared(self):
        import asyncio
        from app.services import task_runner

        async def main():
            async def work():
                await asyncio.sleep(5)

            task_runner.start(4243, work())
            self.assertTrue(task_runner.cancel(4243))
            self.assertTrue(task_runner.is_cancelled(4243))
            await asyncio.sleep(0.05)
            task_runner.clear(4243)
            self.assertFalse(task_runner.is_cancelled(4243))

        asyncio.run(main())

    def test_cancel_unknown_episode_is_false(self):
        from app.services import task_runner

        self.assertFalse(task_runner.cancel(987654))
        self.assertFalse(task_runner.is_cancelled(987654))

    def test_clear_refuses_to_drop_live_task(self):
        import asyncio
        from app.services import task_runner

        async def main():
            async def work():
                await asyncio.sleep(5)

            self.assertTrue(task_runner.start(4250, work()))
            task_runner.clear(4250)
            # 活任务不能被 pop 掉，否则会双开且旧任务无法取消
            self.assertTrue(task_runner.is_running(4250) or task_runner.is_cancelled(4250))
            await asyncio.sleep(0.05)
            self.assertFalse(task_runner.is_running(4250))
            task_runner.clear(4250)

        asyncio.run(main())

    def test_estimate_uses_graded_gaps(self):
        from app.api.podcast import estimate_duration_seconds
        from app.config import (
            SILENCE_GAP_SHORT_MS,
            SILENCE_GAP_SECTION_MS,
            GAP_SECTION,
        )

        short = [{"text": "十二个汉字的句子内容", "gap_after_ms": SILENCE_GAP_SHORT_MS}]
        long_gap = [
            {"text": "十二个汉字的句子内容", "gap_after_ms": SILENCE_GAP_SECTION_MS},
            {"text": "收尾", "gap_after_ms": 0},
        ]
        # 同字数下章节停顿应显著拉长预估
        self.assertGreater(
            estimate_duration_seconds(long_gap),
            estimate_duration_seconds(short),
        )
        # 无 gap 字段时回退句间停顿，不应为 0
        bare = [{"text": "十二个汉字的句子内容"}, {"text": "又一句"}]
        self.assertGreater(estimate_duration_seconds(bare), 0)
        _ = GAP_SECTION


if __name__ == "__main__":
    unittest.main()
