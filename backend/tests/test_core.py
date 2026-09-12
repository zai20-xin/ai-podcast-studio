"""核心解析与指令构建的单元测试（不依赖真实 API）"""
import sys
import os
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# 延迟 import，避免未配置 Key 时模块级失败
os.environ.setdefault("MIMO_API_KEY", "test-key-for-unit-tests")


class TestParseDialogue(unittest.TestCase):
    def test_mixed_colon(self):
        from app.api.podcast import parse_dialogue_lines, extract_speakers

        script = "A: 你好\nB：世界\n【章节】\n---\n旁白一句"
        lines = parse_dialogue_lines(script)
        self.assertEqual(lines[0], {"speaker": "A", "text": "你好"})
        self.assertEqual(lines[1], {"speaker": "B", "text": "世界"})
        self.assertEqual(lines[2], {"speaker": "", "text": "旁白一句"})
        self.assertEqual(extract_speakers(lines), ["A", "B"])

    def test_pick_host(self):
        from app.api.podcast import _pick_host_config
        from app.schemas.episode import HostConfig, ModelType

        a = HostConfig(model_type=ModelType.builtin, speaker_names=["A"])
        b = HostConfig(model_type=ModelType.builtin, speaker_names=["B", "C"])
        self.assertIs(_pick_host_config("A", a, b), a)
        self.assertIs(_pick_host_config("B", a, b), b)
        self.assertIs(_pick_host_config("C", a, b), b)


class TestTTSInstruction(unittest.TestCase):
    def test_build_instruction_and_tags(self):
        from app.services.tts_service import TTSService, convert_tags, STYLE_PRESETS

        svc = TTSService()
        text = svc._build_instruction(style="温柔", speed="偏慢", emotion="平静", global_instruction="口语化")
        self.assertIn("口语化", text)
        self.assertIn(STYLE_PRESETS["温柔"], text)
        self.assertIn("语速偏慢", text)
        self.assertIn("情绪：平静", text)

        tagged = svc._build_audio_tag_text("[laugh]你好", "温柔")
        self.assertTrue(tagged.startswith("(温柔)"))
        self.assertIn("(轻笑)你好", tagged)

        self.assertEqual(convert_tags("[sigh]唉"), "(叹气)唉")


class TestPathSanitize(unittest.TestCase):
    def test_upload_name(self):
        from app.api.voices import _sanitize_upload_name

        self.assertEqual(_sanitize_upload_name("../etc/passwd"), "_etc_passwd")
        self.assertNotIn("/", _sanitize_upload_name("a/b/c"))


if __name__ == "__main__":
    unittest.main()
