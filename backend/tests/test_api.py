"""API 层单元/集成测试（TestClient，不依赖真实 TTS）"""
import unittest
from pathlib import Path

from tests import os  # noqa: F401
from fastapi.testclient import TestClient


def make_client():
    from app.main import app
    from app.database import init_db

    init_db()
    return TestClient(app)


class TestProjectsAPI(unittest.TestCase):
    def setUp(self):
        self.c = make_client()

    def test_create_list_get_delete(self):
        r = self.c.post("/api/projects", json={"name": "ut项目", "mode": "dialogue"})
        self.assertEqual(r.status_code, 200)
        pid = r.json()["id"]
        self.assertEqual(r.json()["episode_count"], 0)

        r = self.c.get("/api/projects")
        self.assertEqual(r.status_code, 200)
        self.assertGreaterEqual(r.json()["total"], 1)

        r = self.c.get(f"/api/projects/{pid}")
        self.assertEqual(r.json()["name"], "ut项目")

        r = self.c.delete(f"/api/projects/{pid}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(self.c.get(f"/api/projects/{pid}").status_code, 404)


class TestSettingsAPI(unittest.TestCase):
    def setUp(self):
        self.c = make_client()

    def test_get_and_bad_url(self):
        r = self.c.get("/api/settings")
        self.assertEqual(r.status_code, 200)
        self.assertIn("api_key_set", r.json())
        self.assertIn("tts_provider", r.json())
        self.assertIn("providers", r.json())
        self.assertTrue(any(p["id"] == "mimo" for p in r.json()["providers"]["tts"]))
        r = self.c.put("/api/settings", json={"base_url": "ftp://x"})
        self.assertEqual(r.status_code, 400)

    def test_unknown_provider_rejected(self):
        r = self.c.put("/api/settings", json={"tts_provider": "not-a-vendor"})
        self.assertEqual(r.status_code, 400)
        r = self.c.put("/api/settings", json={"llm_provider": "not-a-vendor"})
        self.assertEqual(r.status_code, 400)

    def test_update_provider_persists(self):
        from app.config import runtime_config

        old = runtime_config.llm_provider
        try:
            r = self.c.put("/api/settings", json={"llm_provider": "openai"})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(runtime_config.llm_provider, "openai")
            r = self.c.get("/api/settings")
            self.assertEqual(r.json()["llm_provider"], "openai")
        finally:
            runtime_config.update(llm_provider=old or "freellmapi")

    def test_update_key_persists_to_env(self):
        """设置页写的 Key 必须落盘，否则重启后丢失。"""
        from app.config import ENV_PATH, runtime_config, persist_env
        from app.api import settings as settings_mod

        marker = "ut-persist-key-do-not-use"
        old_key = runtime_config.api_key
        backup = ENV_PATH.read_text(encoding="utf-8") if ENV_PATH.exists() else None
        try:
            r = self.c.put("/api/settings", json={"api_key": marker})
            self.assertEqual(r.status_code, 200)
            self.assertEqual(runtime_config.api_key, marker)
            self.assertTrue(ENV_PATH.exists())
            content = ENV_PATH.read_text(encoding="utf-8")
            self.assertIn(f"MIMO_API_KEY={marker}", content)

            # 模拟重启：清空内存后从 env 读回
            runtime_config._api_key = ""
            os.environ.pop("MIMO_API_KEY", None)
            # 属性会回落到 environ；再手动 load 一次
            from dotenv import load_dotenv

            load_dotenv(ENV_PATH, override=True)
            self.assertEqual(runtime_config.api_key, marker)
        finally:
            runtime_config._api_key = old_key
            if old_key:
                os.environ["MIMO_API_KEY"] = old_key
            else:
                os.environ.pop("MIMO_API_KEY", None)
            if backup is None:
                if ENV_PATH.exists():
                    # 恢复为「无该测试键」的状态：重写去掉 marker
                    text = ENV_PATH.read_text(encoding="utf-8")
                    ENV_PATH.write_text(
                        "\n".join(
                            ln
                            for ln in text.splitlines()
                            if not ln.startswith("MIMO_API_KEY=" + marker)
                        )
                        + "\n",
                        encoding="utf-8",
                    )
            else:
                ENV_PATH.write_text(backup, encoding="utf-8")


class TestParseAndEstimate(unittest.TestCase):
    def setUp(self):
        self.c = make_client()

    def test_parse_script(self):
        r = self.c.post(
            "/api/podcast/parse-script",
            json={"script": "A: 一\nB: 二"},
        )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["speakers"], ["A", "B"])

    def test_estimate(self):
        r = self.c.post(
            "/api/projects", json={"name": "est", "mode": "single"}
        )
        pid = r.json()["id"]
        r = self.c.post(
            "/api/podcast/episodes",
            json={
                "project_id": pid,
                "script": "A: 你好世界测试\nB: 第二句内容",
                "host_a_config": {"model_type": "builtin", "voice_id": "冰糖"},
            },
        )
        eid = r.json()["id"]
        r = self.c.post(f"/api/podcast/episodes/{eid}/estimate")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["total_lines"], 2)
        self.assertGreater(body["total_chars"], 0)
        self.c.delete(f"/api/podcast/episodes/{eid}")
        self.c.delete(f"/api/projects/{pid}")


class TestScriptAPIValidation(unittest.TestCase):
    def setUp(self):
        self.c = make_client()

    def test_rewrite_bad_action(self):
        r = self.c.post(
            "/api/script/rewrite",
            json={"script": "A: 这是一段足够长的测试脚本内容", "action": "nope"},
        )
        self.assertEqual(r.status_code, 400)

    def test_generate_without_key(self):
        # LLM_API_KEY 为空时应 400/502，且 detail 为人话
        from app.config import runtime_config

        old = runtime_config.llm_api_key
        try:
            runtime_config._llm_api_key = ""
            os.environ.pop("LLM_API_KEY", None)
            r = self.c.post(
                "/api/script/generate",
                json={"source": "足够长的大纲内容用于测试写稿接口", "mode": "single"},
            )
            self.assertIn(r.status_code, (400, 502))
            detail = r.json().get("detail", "")
            self.assertTrue("Key" in detail or "LLM" in detail or "配置" in detail)
        finally:
            runtime_config._llm_api_key = old


class TestPreviewLFI(unittest.TestCase):
    def test_reject_outside_voices(self):
        c = make_client()
        r = c.post(
            "/api/podcast/preview-sentence",
            json={
                "text": "你好这是一句试听文本",
                "model_type": "clone",
                "reference_audio": "/etc/passwd",
            },
        )
        self.assertEqual(r.status_code, 400)


class TestEpisodeStatusFields(unittest.TestCase):
    def test_status_shape(self):
        c = make_client()
        r = c.post("/api/projects", json={"name": "st", "mode": "single"})
        pid = r.json()["id"]
        r = c.post(
            "/api/podcast/episodes",
            json={
                "project_id": pid,
                "script": "只有一句旁白内容测试",
                "host_a_config": {"model_type": "builtin", "voice_id": "茉莉"},
                "intro_text": "欢迎收听",
            },
        )
        eid = r.json()["id"]
        r = c.get(f"/api/podcast/episodes/{eid}/status")
        self.assertEqual(r.status_code, 200)
        for k in (
            "status",
            "progress_current",
            "progress_total",
            "segments",
            "failed_count",
            "done_count",
        ):
            self.assertIn(k, r.json())
        c.delete(f"/api/podcast/episodes/{eid}")
        c.delete(f"/api/projects/{pid}")


class TestClonedVoiceManage(unittest.TestCase):
    """克隆素材：上传命名 / 重命名 / 删除"""

    def setUp(self):
        self.c = make_client()
        self._created: list[int] = []

    def tearDown(self):
        for vid in self._created:
            try:
                self.c.delete(f"/api/voices/cloned/{vid}")
            except Exception:
                pass

    def _upload(self, name: str, filename: str = "ref.wav") -> dict:
        from pydub.generators import Sine
        import io

        buf = io.BytesIO()
        Sine(440).to_audio_segment(duration=200).export(buf, format="wav")
        buf.seek(0)
        r = self.c.post(
            f"/api/voices/clone?name={name}",
            files={"audio": (filename, buf.getvalue(), "audio/wav")},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self._created.append(r.json()["id"])
        return r.json()

    def test_upload_keeps_chinese_display_name(self):
        body = self._upload("访谈女声·小雅", filename="interview host.wav")
        self.assertEqual(body["name"], "访谈女声·小雅")
        # 落盘路径不包含中文，且在 voices 目录下
        self.assertIn("voices", body["reference_path"])
        self.assertNotIn("访谈", Path(body["reference_path"]).name)

    def test_rename_and_list(self):
        body = self._upload("旧名字")
        r = self.c.patch(f"/api/voices/cloned/{body['id']}", json={"name": "新名字 · 主播A"})
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["name"], "新名字 · 主播A")
        # reference_path 不应因重命名而变化
        self.assertEqual(r.json()["reference_path"], body["reference_path"])

        r = self.c.get("/api/voices/cloned")
        names = [v["name"] for v in r.json()]
        self.assertIn("新名字 · 主播A", names)

    def test_rename_empty_rejected(self):
        body = self._upload("临时")
        r = self.c.patch(f"/api/voices/cloned/{body['id']}", json={"name": "   "})
        self.assertEqual(r.status_code, 400)
        self.assertIn("不能为空", r.json()["detail"])
        r = self.c.patch(f"/api/voices/cloned/{body['id']}", json={"name": ""})
        self.assertEqual(r.status_code, 422)

    def test_rename_unknown_404(self):
        r = self.c.patch("/api/voices/cloned/999999", json={"name": "x"})
        self.assertEqual(r.status_code, 404)

    def test_delete_removes_record(self):
        body = self._upload("将删除")
        path = Path(body["reference_path"])
        self.assertTrue(path.exists())
        r = self.c.delete(f"/api/voices/cloned/{body['id']}")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(path.exists())
        r = self.c.get(f"/api/voices/cloned/{body['id']}/audio")
        self.assertEqual(r.status_code, 404)
        self._created.remove(body["id"])


if __name__ == "__main__":
    unittest.main()
