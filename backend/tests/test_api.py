"""API 层单元/集成测试（TestClient，不依赖真实 TTS）"""
import unittest

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
        r = self.c.put("/api/settings", json={"base_url": "ftp://x"})
        self.assertEqual(r.status_code, 400)


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


if __name__ == "__main__":
    unittest.main()
