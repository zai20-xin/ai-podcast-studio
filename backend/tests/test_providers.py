"""供应商兼容层单元测试"""
import unittest

from tests import os  # noqa: F401


class TestProviderCatalog(unittest.TestCase):
    def test_mimo_tts_uses_api_key_auth(self):
        from app.providers import TTS_BY_ID, create_async_client

        self.assertEqual(TTS_BY_ID["mimo"].auth, "api-key")
        self.assertEqual(TTS_BY_ID["mimo"].status, "supported")
        client = create_async_client(
            api_key="k",
            base_url="https://example.invalid/v1",
            provider_id="mimo",
            kind="tts",
        )
        try:
            self.assertEqual(client.auth_headers, {"api-key": "k"})
        finally:
            # 避免未关闭连接告警
            import asyncio

            try:
                asyncio.get_event_loop_policy()
            except Exception:
                pass

    def test_llm_custom_uses_bearer(self):
        from app.providers import LLM_BY_ID, create_sync_client

        self.assertEqual(LLM_BY_ID["freellmapi"].auth, "bearer")
        client = create_sync_client(
            api_key="secret",
            base_url="http://127.0.0.1:9/v1",
            provider_id="freellmapi",
            kind="llm",
        )
        self.assertIn("Bearer", client.auth_headers.get("Authorization", ""))

    def test_catalog_marks_experimental(self):
        from app.providers import catalog_payload

        data = catalog_payload()
        tts = {p["id"]: p for p in data["tts"]}
        llm = {p["id"]: p for p in data["llm"]}
        self.assertEqual(tts["openai_compatible"]["status"], "experimental")
        self.assertEqual(llm["ollama"]["status"], "experimental")
        self.assertEqual(tts["mimo"]["status"], "supported")
        # experimental 必须带接入说明，避免用户误以为开箱即用
        self.assertTrue(tts["openai_compatible"]["note"])
        self.assertTrue(llm["custom"]["note"])


if __name__ == "__main__":
    unittest.main()
