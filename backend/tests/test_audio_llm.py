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
        # 克隆模式参考音频丢失：过去会漏成 500，现在有专门文案
        self.assertIn("已不存在", friendly_error("音频文件不存在: /x.wav"))
        self.assertIn("7.5MB", friendly_error("参考音频 Base64 编码后为 12.0MB，超过 MiMo 的 10MB 上限"))


class TestSrtTimeline(unittest.TestCase):
    """SRT 时间轴必须与 merge_audio 的实际拼接一致。

    合并时会插入句间停顿、片头之后也有停顿；旧实现按「上一句结束即下一句开始」
    累加，偏移会随句数线性累积。
    """

    @staticmethod
    def _starts(srt: str) -> list:
        import re

        out = []
        for line in srt.splitlines():
            m = re.match(r"(\d\d):(\d\d):(\d\d),(\d\d\d) -->", line)
            if m:
                h, mi, s, ms = (int(x) for x in m.groups())
                out.append(h * 3600 + mi * 60 + s + ms / 1000.0)
        return out

    def test_timeline_accounts_for_gap_and_intro(self):
        import shutil
        import tempfile
        from pathlib import Path
        from pydub.generators import Sine
        from app.services.audio import AudioService

        svc = AudioService()
        tmp = Path(tempfile.mkdtemp())
        try:
            files = []
            for i in range(3):
                p = tmp / f"s{i}.wav"
                Sine(440).to_audio_segment(duration=1000).export(str(p), format="wav")
                files.append(str(p))
            body = [
                {"index": i, "speaker": "A", "text": f"第{i + 1}句", "audio_path": files[i]}
                for i in range(3)
            ]

            # 每句 1s + 600ms 停顿 => 起点应为 0 / 1.6 / 3.2
            starts = self._starts(svc.build_srt(body, silence_gap_ms=600))
            self.assertAlmostEqual(starts[0], 0.0, places=2)
            self.assertAlmostEqual(starts[1], 1.6, places=2)
            self.assertAlmostEqual(starts[2], 3.2, places=2)

            # 有 2s 片头时，正文整体后移「片头时长 + 段落级停顿」
            from app.config import SILENCE_GAP_PARAGRAPH_MS

            intro_offset = 2.0 + SILENCE_GAP_PARAGRAPH_MS / 1000.0
            starts2 = self._starts(
                svc.build_srt(body, silence_gap_ms=600, intro_duration_ms=2000)
            )
            self.assertAlmostEqual(starts2[0], intro_offset, places=2)
            self.assertAlmostEqual(starts2[1], intro_offset + 1.6, places=2)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class TestMergeAudio(unittest.TestCase):
    def test_merge_concatenates_in_order(self):
        """合并是否真的按顺序拼接，且总时长等于各段之和加停顿"""
        import shutil
        import tempfile
        from pathlib import Path
        from pydub import AudioSegment
        from pydub.generators import Sine
        import app.services.audio as audio_mod
        from app.services.audio import AudioService

        tmp = Path(tempfile.mkdtemp())
        original_dir = audio_mod.AUDIO_DIR
        try:
            audio_mod.AUDIO_DIR = tmp
            svc = AudioService()
            files = []
            for i in range(3):
                p = tmp / f"m{i}.wav"
                Sine(440 + i * 100).to_audio_segment(duration=1000).export(str(p), format="wav")
                files.append(str(p))

            out = svc.merge_audio(files, "merged", silence_gap=600, normalize=False)
            total = len(AudioSegment.from_file(out)) / 1000.0
            # 3 段各 1s + 2 次停顿 0.6s
            self.assertAlmostEqual(total, 4.2, places=1)
        finally:
            audio_mod.AUDIO_DIR = original_dir
            shutil.rmtree(tmp, ignore_errors=True)

    def test_outro_never_glued_to_last_line(self):
        """末句 gap_after=0 且有片尾时，片尾前仍须插入默认停顿"""
        import shutil
        import tempfile
        from pathlib import Path
        from pydub import AudioSegment
        from pydub.generators import Sine
        import app.services.audio as audio_mod
        from app.services.audio import AudioService

        tmp = Path(tempfile.mkdtemp())
        original_dir = audio_mod.AUDIO_DIR
        try:
            audio_mod.AUDIO_DIR = tmp
            svc = AudioService()
            body = tmp / "body.wav"
            outro = tmp / "outro.wav"
            Sine(440).to_audio_segment(duration=1000).export(str(body), format="wav")
            Sine(220).to_audio_segment(duration=500).export(str(outro), format="wav")

            out = svc.merge_audio(
                [str(body)],
                "with_outro",
                silence_gap=600,
                normalize=False,
                outro_text_audio=str(outro),
                gap_after_ms=[0],  # 末句不停顿，但片尾前不能贴上
            )
            total = len(AudioSegment.from_file(out)) / 1000.0
            self.assertAlmostEqual(total, 1.0 + 0.6 + 0.5, places=1)
        finally:
            audio_mod.AUDIO_DIR = original_dir
            shutil.rmtree(tmp, ignore_errors=True)

    def test_graded_gaps_applied(self):
        import shutil
        import tempfile
        from pathlib import Path
        from pydub import AudioSegment
        from pydub.generators import Sine
        import app.services.audio as audio_mod
        from app.services.audio import AudioService

        tmp = Path(tempfile.mkdtemp())
        original_dir = audio_mod.AUDIO_DIR
        try:
            audio_mod.AUDIO_DIR = tmp
            svc = AudioService()
            files = []
            for i in range(3):
                p = tmp / f"g{i}.wav"
                Sine(440).to_audio_segment(duration=500).export(str(p), format="wav")
                files.append(str(p))
            out = svc.merge_audio(
                files,
                "graded",
                silence_gap=600,
                normalize=False,
                gap_after_ms=[350, 1400, 0],
            )
            # 3*0.5s + 0.35 + 1.4 + 0（末句无后继）
            total = len(AudioSegment.from_file(out)) / 1000.0
            self.assertAlmostEqual(total, 1.5 + 0.35 + 1.4, places=1)
        finally:
            audio_mod.AUDIO_DIR = original_dir
            shutil.rmtree(tmp, ignore_errors=True)


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
