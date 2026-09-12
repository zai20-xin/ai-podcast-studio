"""音频与 LLM 辅助函数单元测试"""
import tempfile
import unittest
from pathlib import Path

from tests import os  # noqa: F401


class TestAudioService(unittest.TestCase):
    def test_srt_time(self):
        from app.services.audio import AudioService

        self.assertEqual(AudioService.format_srt_time(0), "00:00:00,000")
        self.assertEqual(AudioService.format_srt_time(61.5), "00:01:01,500")
        self.assertEqual(AudioService.format_srt_time(3661.25), "01:01:01,250")

    def test_build_srt_without_files(self):
        from app.services.audio import AudioService

        svc = AudioService()
        srt = svc.build_srt(
            [
                {"speaker": "A", "text": "第一句", "audio_path": None},
                {"speaker": "B", "text": "第二句", "audio_path": None},
            ]
        )
        self.assertIn("1\n", srt)
        self.assertIn("A：第一句", srt)
        self.assertIn("-->", srt)
        self.assertIn("B：第二句", srt)

    def test_script_markdown(self):
        from app.services.audio import AudioService

        md = AudioService.build_script_markdown(
            title="测试集",
            script="A: 你好",
            mode="dialogue",
            hosts={"主播 A": {"voice_id": "冰糖", "style": "温柔"}},
        )
        self.assertIn("# 测试集", md)
        self.assertIn("双人对谈", md)
        self.assertIn("冰糖", md)

    def test_normalize_silent_audio_returns_original(self):
        from app.services.audio import AudioService
        from pydub import AudioSegment

        svc = AudioService()
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "silent.wav"
            AudioSegment.silent(duration=100).export(str(p), format="wav")
            out = svc.normalize_volume(str(p))
            # 静音或 loudnorm 失败时不应炸，返回路径字符串
            self.assertTrue(isinstance(out, str) and out)


class TestFriendlyError(unittest.TestCase):
    def test_messages(self):
        from app.services.llm import friendly_error

        self.assertIn("API Key", friendly_error("Invalid API key"))
        self.assertIn("超时", friendly_error("Request timed out"))
        self.assertIn("参考音频", friendly_error("参考音频路径无效"))
        self.assertIn("频繁", friendly_error("Rate limit exceeded"))


class TestHostConfigSerialize(unittest.TestCase):
    def test_model_dump_json(self):
        import json
        from app.schemas.episode import HostConfig, ModelType

        h = HostConfig(model_type=ModelType.design, voice_description="年轻女声")
        data = json.loads(json.dumps(h.model_dump(mode="json")))
        self.assertEqual(data["model_type"], "design")
        self.assertEqual(data["voice_description"], "年轻女声")
        for key in (
            "model_type",
            "voice_id",
            "reference_audio",
            "voice_description",
            "style",
            "speed",
            "emotion",
            "audio_tag_style",
            "speaker_names",
        ):
            self.assertIn(key, data)


if __name__ == "__main__":
    unittest.main()
