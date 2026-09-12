"""前后端契约测试（后端侧）：响应字段必须与 contracts/api-contract.json 一致"""
import json
import unittest

from tests import CONTRACT_PATH, os  # noqa: F401
from fastapi.testclient import TestClient


def load_contract() -> dict:
    return json.loads(CONTRACT_PATH.read_text(encoding="utf-8"))


def assert_keys(testcase: unittest.TestCase, data: dict, keys: list[str], ctx: str):
    for k in keys:
        testcase.assertIn(k, data, f"{ctx} 缺少字段 {k}")


class TestApiContract(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from app.main import app
        from app.database import init_db

        init_db()
        cls.c = TestClient(app)
        cls.contract = load_contract()

    def _ep(self, name: str) -> dict:
        return self.contract["endpoints"][name]

    def test_root(self):
        r = self.c.get("/")
        assert_keys(self, r.json(), self._ep("GET /")["required_keys"], "GET /")

    def test_settings(self):
        r = self.c.get("/api/settings")
        assert_keys(
            self, r.json(), self._ep("GET /api/settings")["required_keys"], "settings"
        )

    def test_voices_meta_and_builtin(self):
        r = self.c.get("/api/voices/meta")
        assert_keys(
            self, r.json(), self._ep("GET /api/voices/meta")["required_keys"], "meta"
        )
        self.assertEqual(
            [m["id"] for m in r.json()["model_types"]], self.contract["model_types"]
        )
        r = self.c.get("/api/voices/builtin")
        assert_keys(
            self, r.json(), self._ep("GET /api/voices/builtin")["required_keys"], "builtin"
        )
        item_keys = self._ep("GET /api/voices/builtin")["item_keys"]
        self.assertGreater(len(r.json()["voices"]), 0)
        for v in r.json()["voices"]:
            assert_keys(self, v, item_keys, "voice item")

    def test_projects_and_episode_lifecycle(self):
        r = self.c.post("/api/projects", json={"name": "契约项目", "mode": "dialogue"})
        assert_keys(
            self, r.json(), self._ep("POST /api/projects")["required_keys"], "create project"
        )
        pid = r.json()["id"]

        r = self.c.get("/api/projects")
        assert_keys(
            self, r.json(), self._ep("GET /api/projects")["required_keys"], "list projects"
        )
        for item in r.json()["projects"]:
            assert_keys(
                self, item, self._ep("GET /api/projects")["item_keys"], "project item"
            )

        r = self.c.post(
            "/api/podcast/parse-script",
            json={"script": "A: 契约第一句\nB: 契约第二句"},
        )
        assert_keys(
            self,
            r.json(),
            self._ep("POST /api/podcast/parse-script")["required_keys"],
            "parse",
        )
        for line in r.json()["dialogue"]:
            assert_keys(
                self,
                line,
                self._ep("POST /api/podcast/parse-script")["dialogue_item_keys"],
                "dialogue",
            )

        host = {
            "model_type": "builtin",
            "voice_id": "冰糖",
            "speaker_names": ["A"],
        }
        for f in self.contract["host_config_fields"]:
            # 允许省略可选字段，但提交后响应序列化应包含
            host.setdefault(f, None if f != "speaker_names" else [])

        r = self.c.post(
            "/api/podcast/episodes",
            json={
                "project_id": pid,
                "script": "A: 第一句\nB: 第二句",
                "host_a_config": {
                    "model_type": "builtin",
                    "voice_id": "冰糖",
                    "speaker_names": ["A"],
                },
                "host_b_config": {
                    "model_type": "builtin",
                    "voice_id": "白桦",
                    "speaker_names": ["B"],
                },
                "intro_text": "欢迎",
            },
        )
        assert_keys(
            self,
            r.json(),
            self._ep("POST /api/podcast/episodes")["required_keys"],
            "create episode",
        )
        eid = r.json()["id"]
        self.assertIn(r.json()["status"], self.contract["episode_status"])

        # host config JSON 内字段契约
        ha = json.loads(r.json()["host_a_config"])
        for f in self.contract["host_config_fields"]:
            self.assertIn(f, ha, f"host_a_config 缺少 {f}")

        r = self.c.get(f"/api/podcast/episodes/{eid}/status")
        assert_keys(
            self,
            r.json(),
            self._ep("GET /api/podcast/episodes/{id}/status")["required_keys"],
            "status",
        )

        r = self.c.post(f"/api/podcast/episodes/{eid}/estimate")
        assert_keys(
            self,
            r.json(),
            self._ep("POST /api/podcast/episodes/{id}/estimate")["required_keys"],
            "estimate",
        )

        self.c.delete(f"/api/podcast/episodes/{eid}")
        self.c.delete(f"/api/projects/{pid}")

    def test_script_rewrite_validation_contract(self):
        # 非法 action 应 400，合法结构见契约（成功路径需 LLM，此处只测校验）
        r = self.c.post(
            "/api/script/rewrite",
            json={"script": "A: 足够长的脚本用于契约校验测试", "action": "bad"},
        )
        self.assertEqual(r.status_code, 400)


if __name__ == "__main__":
    unittest.main()
