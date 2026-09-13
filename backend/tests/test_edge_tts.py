"""Edge TTS 引擎与供应商旁路单元测试（不访问微软网络）"""
import asyncio
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from tests import os  # noqa: F401


class TestEdgeTTSEngine(unittest.TestCase):
    def test_strip_tags_and_rate(self):
        from app.services.edge_tts_engine import (
            strip_tts_tags,
            rate_for_speed,
            resolve_voice,
            DEFAULT_EDGE_VOICE,
        )

        self.assertEqual(strip_tts_tags("(轻笑)你好（停顿）世界"), "你好世界")
        # 控制字符/零宽字符应被清掉
        self.assertEqual(strip_tts_tags("你好\x00\x0b​世界"), "你好世界")
        self.assertEqual(rate_for_speed("偏快"), "+15%")
        self.assertEqual(rate_for_speed(None), "+0%")
        self.assertEqual(rate_for_speed("未知档"), "+0%")
        self.assertEqual(resolve_voice("zh-CN-YunxiNeural"), "zh-CN-YunxiNeural")
        self.assertEqual(resolve_voice("冰糖"), DEFAULT_EDGE_VOICE)
        self.assertEqual(resolve_voice(None), DEFAULT_EDGE_VOICE)

    def test_voice_catalog_shape(self):
        from app.services.edge_tts_engine import EDGE_VOICES
        from app.api.voices import BUILTIN_VOICES

        self.assertGreaterEqual(len(EDGE_VOICES), 8)
        for v in EDGE_VOICES:
            self.assertIn("id", v)
            self.assertIn("name", v)
            self.assertIn("lang", v)
            self.assertIn("gender", v)
            self.assertTrue(v["id"].endswith("Neural"))
        # 与 MiMo 内置音色 ID 不混用
        mimo_ids = {v["id"] for v in BUILTIN_VOICES}
        edge_ids = {v["id"] for v in EDGE_VOICES}
        self.assertFalse(mimo_ids & edge_ids)


class TestTTSServiceEdgeBranch(unittest.TestCase):
    def test_init_without_api_key(self):
        from app.config import runtime_config
        from app.services.tts_service import TTSService

        old = runtime_config.tts_provider
        old_key = runtime_config.api_key
        try:
            runtime_config.update(tts_provider="edge-tts")
            os.environ["MIMO_API_KEY"] = ""
            runtime_config._api_key = ""
            svc = TTSService()
            self.assertTrue(svc.is_edge)
            self.assertIsNone(svc.client)
            self.assertEqual(svc.supported_model_types, ("builtin",))
        finally:
            runtime_config.update(tts_provider=old or "mimo")
            if old_key:
                os.environ["MIMO_API_KEY"] = old_key
                runtime_config._api_key = old_key

    def test_design_clone_rejected(self):
        from app.config import runtime_config
        from app.services.tts_service import TTSService

        old = runtime_config.tts_provider
        try:
            runtime_config.update(tts_provider="edge-tts")
            os.environ.setdefault("MIMO_API_KEY", "x")
            runtime_config._api_key = os.environ.get("MIMO_API_KEY", "x")
            svc = TTSService()
            with self.assertRaises(ValueError) as ctx:
                asyncio.run(svc.synthesize("你好", model_type="clone", reference_audio="x.wav"))
            self.assertIn("clone", str(ctx.exception))
            with self.assertRaises(ValueError):
                asyncio.run(svc.synthesize("你好", model_type="design", voice_description="女声"))
        finally:
            runtime_config.update(tts_provider=old or "mimo")

    def test_synthesize_calls_edge_engine(self):
        from app.config import runtime_config
        from app.services.tts_service import TTSService

        old = runtime_config.tts_provider
        try:
            runtime_config.update(tts_provider="edge-tts")
            os.environ.setdefault("MIMO_API_KEY", "x")
            runtime_config._api_key = os.environ.get("MIMO_API_KEY", "x")
            svc = TTSService()

            async def fake_edge(text, *, voice_id=None, speed=None, dest_dir=None, timeout=60.0):
                return f"/tmp/fake_{voice_id}_{speed}.wav"

            # 局部替换导入路径上的符号：synthesize 内是延迟 import
            import app.services.edge_tts_engine as engine

            orig = engine.synthesize_to_wav
            engine.synthesize_to_wav = fake_edge
            try:
                path = asyncio.run(
                    svc.synthesize(
                        "测试句",
                        model_type="builtin",
                        voice_id="zh-CN-XiaoxiaoNeural",
                        speed="偏快",
                    )
                )
            finally:
                engine.synthesize_to_wav = orig
            self.assertIn("zh-CN-XiaoxiaoNeural", path)
            self.assertIn("偏快", path)
        finally:
            runtime_config.update(tts_provider=old or "mimo")


class TestVoicesAPIEdge(unittest.TestCase):
    def setUp(self):
        from fastapi.testclient import TestClient
        from app.main import app
        from app.database import init_db
        from app.config import runtime_config

        init_db()
        self.c = TestClient(app)
        self._old_provider = runtime_config.tts_provider
        runtime_config.update(tts_provider="edge-tts")

    def tearDown(self):
        from app.config import runtime_config

        runtime_config.update(tts_provider=self._old_provider or "mimo")

    def test_builtin_and_meta_switch(self):
        r = self.c.get("/api/voices/builtin")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["provider"], "edge-tts")
        self.assertTrue(all(v["id"].endswith("Neural") for v in body["voices"]))
        self.assertEqual(body["defaults"]["A"], "zh-CN-XiaoxiaoNeural")

        m = self.c.get("/api/voices/meta")
        self.assertEqual(m.status_code, 200)
        meta = m.json()
        self.assertEqual(meta["tts_provider"], "edge-tts")
        self.assertFalse(meta["capabilities"]["design"])
        self.assertFalse(meta["capabilities"]["clone"])
        self.assertEqual([t["id"] for t in meta["model_types"]], ["builtin"])


class TestDefaultProvider(unittest.TestCase):
    def test_auto_edge_when_no_key(self):
        from importlib import reload
        import app.config as config_mod

        old_env = {
            "TTS_PROVIDER": os.environ.get("TTS_PROVIDER"),
            "MIMO_API_KEY": os.environ.get("MIMO_API_KEY"),
        }
        try:
            os.environ.pop("TTS_PROVIDER", None)
            os.environ.pop("MIMO_API_KEY", None)
            self.assertEqual(config_mod._default_tts_provider(), "edge-tts")
            os.environ["MIMO_API_KEY"] = "k"
            self.assertEqual(config_mod._default_tts_provider(), "mimo")
            os.environ["TTS_PROVIDER"] = "edge-tts"
            self.assertEqual(config_mod._default_tts_provider(), "edge-tts")
        finally:
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


if __name__ == "__main__":
    unittest.main()
