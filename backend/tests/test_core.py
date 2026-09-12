"""后端单元测试：脚本解析、主播映射、指令构建"""
import unittest

from tests import os  # noqa: F401  确保环境变量已设置


class TestParseDialogue(unittest.TestCase):
    def test_mixed_colon_and_chapter(self):
        from app.api.podcast import parse_dialogue_lines, extract_speakers

        script = "A: 你好\nB：世界\n【章节】\n---\n旁白一句\nC:好"
        lines = parse_dialogue_lines(script)
        self.assertEqual(lines[0], {"speaker": "A", "text": "你好"})
        self.assertEqual(lines[1], {"speaker": "B", "text": "世界"})
        self.assertEqual(lines[2], {"speaker": "", "text": "旁白一句"})
        self.assertEqual(lines[3], {"speaker": "C", "text": "好"})
        self.assertEqual(extract_speakers(lines), ["A", "B", "C"])

    def test_max_lines_cap(self):
        from app.api.podcast import parse_dialogue_lines, MAX_DIALOGUE_LINES

        script = "\n".join(f"A: 第{i}句" for i in range(MAX_DIALOGUE_LINES + 20))
        self.assertEqual(len(parse_dialogue_lines(script)), MAX_DIALOGUE_LINES)

    def test_empty(self):
        from app.api.podcast import parse_dialogue_lines

        self.assertEqual(parse_dialogue_lines("   \n\n"), [])


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
        self.assertIn("情绪：平静", text)
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


if __name__ == "__main__":
    unittest.main()
