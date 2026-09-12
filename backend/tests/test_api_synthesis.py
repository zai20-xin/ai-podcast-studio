"""合成链路 API 测试：取消 / 超限 / 并发 409 / 指纹复用 / 失败续跑.

不依赖真实 TTS：用 mock 替换 TTSService.synthesize，写出极短 wav 供合并使用。
"""
from __future__ import annotations

import asyncio
import json
import time
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from tests import os  # noqa: F401
from fastapi.testclient import TestClient
from pydub.generators import Sine

from app.api.podcast import MAX_DIALOGUE_LINES
from app.config import (
    AUDIO_DIR,
    GAP_LEVEL_TO_MS,
    SILENCE_GAP_PARAGRAPH_MS,
    SILENCE_GAP_SECTION_MS,
    SILENCE_GAP_SHORT_MS,
    VOICES_DIR,
)
from app.services import task_runner


def _write_tiny_wav(dest_dir: Path, duration_ms: int = 200) -> str:
    dest_dir.mkdir(parents=True, exist_ok=True)
    path = dest_dir / f"mock_{uuid.uuid4().hex[:8]}.wav"
    Sine(440).to_audio_segment(duration=duration_ms).export(str(path), format="wav")
    return str(path)


class _TTSRecorder:
    """可配置的假 TTS：成功 / 失败 / 延迟，并记录调用次数与文本。"""

    def __init__(self, *, fail_all: bool = False, fail_texts: set[str] | None = None,
                 delay: float = 0.0, duration_ms: int = 200):
        self.fail_all = fail_all
        self.fail_texts = fail_texts or set()
        self.delay = delay
        self.duration_ms = duration_ms
        self.calls: list[str] = []

    async def synthesize(self, text: str = "", *, dest_dir: Path | None = None, **kwargs) -> str:
        self.calls.append(text)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.fail_all or text in self.fail_texts:
            raise RuntimeError(f"mock TTS failure for: {text}")
        return _write_tiny_wav(dest_dir or AUDIO_DIR, self.duration_ms)


class SynthesisApiBase(unittest.TestCase):
    """公共夹具：项目 + 单集，测试结束清理 task_runner 与资源。

    必须用 with TestClient(...)：否则每个请求各自开关 portal，
    后台合成任务会随请求结束被连带 cancel，永远等不到 done。
    """

    def setUp(self):
        from app.main import app
        from app.database import init_db

        init_db()
        # SQLite 会复用 rowid，清掉上个用例残留的取消标志，避免串味
        task_runner._cancelled.clear()
        self._client_ctx = TestClient(app)
        self.client = self._client_ctx.__enter__()
        self._to_delete: list[tuple[str, int]] = []
        self._episode_ids: list[int] = []
        self.addCleanup(self._cleanup)

    def _cleanup(self):
        for eid in self._episode_ids:
            task_runner.clear(eid)
        for kind, rid in self._to_delete:
            try:
                self.client.delete(f"/api/{kind}/{rid}")
            except Exception:
                pass
        try:
            self._client_ctx.__exit__(None, None, None)
        except Exception:
            pass

    def _create_project(self, name: str = "合成测试", mode: str = "dialogue") -> int:
        r = self.client.post("/api/projects", json={"name": name, "mode": mode})
        self.assertEqual(r.status_code, 200)
        pid = r.json()["id"]
        self._to_delete.append(("projects", pid))
        return pid

    def _create_episode(
        self,
        project_id: int,
        script: str,
        *,
        intro: str | None = None,
        outro: str | None = None,
        name: str | None = None,
    ) -> int:
        r = self.client.post(
            "/api/podcast/episodes",
            json={
                "project_id": project_id,
                "script": script,
                "name": name or f"ep-{uuid.uuid4().hex[:6]}",
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
                "intro_text": intro,
                "outro_text": outro,
            },
        )
        self.assertEqual(r.status_code, 200)
        eid = r.json()["id"]
        self._to_delete.append(("podcast/episodes", eid))
        if not hasattr(self, "_episode_ids"):
            self._episode_ids = []
        self._episode_ids.append(eid)
        return eid

    def _status(self, eid: int) -> dict:
        r = self.client.get(f"/api/podcast/episodes/{eid}/status")
        self.assertEqual(r.status_code, 200)
        return r.json()

    def _wait_status(self, eid: int, wanted: set[str], timeout: float = 8.0) -> dict:
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            last = self._status(eid)
            if last["status"] in wanted:
                return last
            time.sleep(0.05)
        self.fail(f"等待状态 {wanted} 超时，最后状态: {last}")


class TestParseScriptOverLimit(SynthesisApiBase):
    def test_short_script_not_over_limit(self):
        r = self.client.post(
            "/api/podcast/parse-script",
            json={"script": "A: 一\nB: 二"},
        )
        body = r.json()
        self.assertEqual(body["max_lines"], MAX_DIALOGUE_LINES)
        self.assertFalse(body["over_limit"])
        self.assertEqual(body["total_lines"], 2)

    def test_over_limit_flag_true(self):
        script = "\n".join(f"A: 第{i}句" for i in range(MAX_DIALOGUE_LINES + 5))
        r = self.client.post("/api/podcast/parse-script", json={"script": script})
        body = r.json()
        self.assertEqual(body["total_lines"], MAX_DIALOGUE_LINES + 5)
        self.assertTrue(body["over_limit"])
        self.assertEqual(body["max_lines"], MAX_DIALOGUE_LINES)


class TestOverLimitRejected(SynthesisApiBase):
    def test_estimate_over_limit_returns_400(self):
        pid = self._create_project()
        script = "\n".join(f"A: 第{i}句超限测试" for i in range(MAX_DIALOGUE_LINES + 3))
        eid = self._create_episode(pid, script)
        r = self.client.post(f"/api/podcast/episodes/{eid}/estimate")
        self.assertEqual(r.status_code, 400)
        self.assertIn(str(MAX_DIALOGUE_LINES), r.json()["detail"])

    def test_synthesize_over_limit_returns_400(self):
        pid = self._create_project()
        script = "\n".join(f"A: 第{i}句超限合成" for i in range(MAX_DIALOGUE_LINES + 3))
        eid = self._create_episode(pid, script)
        r = self.client.post("/api/podcast/synthesize", json={"episode_id": eid})
        self.assertEqual(r.status_code, 400)
        self.assertIn("超过单集上限", r.json()["detail"])
        # 超限应被挡在启动前，状态不应变成 processing
        self.assertEqual(self._status(eid)["status"], "draft")


class TestCancelEndpoint(SynthesisApiBase):
    def test_cancel_unknown_episode_404(self):
        r = self.client.post("/api/podcast/episodes/999999/cancel")
        self.assertEqual(r.status_code, 404)

    def test_cancel_idle_episode_returns_false(self):
        pid = self._create_project()
        eid = self._create_episode(pid, "A: 只有一句")
        r = self.client.post(f"/api/podcast/episodes/{eid}/cancel")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertFalse(body["cancelled"])
        self.assertEqual(body["episode_id"], eid)
        self.assertIn("没有正在运行", body["message"])
        # 空闲取消不应把 draft 改写成 cancelled
        self.assertEqual(self._status(eid)["status"], "draft")

    def test_cancel_running_synthesis_stops_and_keeps_done_segments(self):
        pid = self._create_project()
        eid = self._create_episode(
            pid,
            "A: 第一句成功\nA: 第二句很慢\nA: 第三句很慢",
        )
        recorder = _TTSRecorder(delay=0.4, duration_ms=150)

        with patch("app.api.podcast.TTSService.synthesize", recorder.synthesize):
            r = self.client.post("/api/podcast/synthesize", json={"episode_id": eid})
            self.assertEqual(r.status_code, 200)
            # 等到至少有一句跑起来，再取消
            deadline = time.time() + 3
            while time.time() < deadline and len(recorder.calls) < 1:
                time.sleep(0.05)
            r = self.client.post(f"/api/podcast/episodes/{eid}/cancel")
            self.assertEqual(r.status_code, 200)
            self.assertTrue(r.json()["cancelled"])
            st = self._wait_status(eid, {"cancelled", "done", "error"}, timeout=6)
            self.assertEqual(st["status"], "cancelled")
            # 取消不是失败：不写 error_message
            self.assertIsNone(st["error_message"])

    def test_cancel_then_retry_can_resume(self):
        pid = self._create_project()
        eid = self._create_episode(pid, "A: 续跑第一句\nA: 续跑第二句")
        slow = _TTSRecorder(delay=0.5, duration_ms=120)
        fast = _TTSRecorder(duration_ms=120)

        with patch("app.api.podcast.TTSService.synthesize", slow.synthesize):
            self.client.post("/api/podcast/synthesize", json={"episode_id": eid})
            time.sleep(0.15)
            self.client.post(f"/api/podcast/episodes/{eid}/cancel")
            self._wait_status(eid, {"cancelled"}, timeout=5)

        # 续跑：用快速 mock，且不应从零开始清掉已完成分句
        with patch("app.api.podcast.TTSService.synthesize", fast.synthesize):
            r = self.client.post(f"/api/podcast/episodes/{eid}/retry")
            self.assertEqual(r.status_code, 200)
            st = self._wait_status(eid, {"done", "error", "cancelled"}, timeout=8)
            self.assertEqual(st["status"], "done")
            body = [s for s in st["segments"] if isinstance(s.get("index"), int)]
            self.assertTrue(all(s["status"] == "done" for s in body))


class TestConcurrent409(SynthesisApiBase):
    def test_synthesize_while_running_is_409(self):
        pid = self._create_project()
        eid = self._create_episode(pid, "A: 并发第一句\nA: 并发第二句")
        slow = _TTSRecorder(delay=0.8, duration_ms=100)

        with patch("app.api.podcast.TTSService.synthesize", slow.synthesize):
            r1 = self.client.post("/api/podcast/synthesize", json={"episode_id": eid})
            self.assertEqual(r1.status_code, 200)
            r2 = self.client.post("/api/podcast/synthesize", json={"episode_id": eid})
            self.assertEqual(r2.status_code, 409)
            # 状态已是 processing 时会先被 _assert_not_busy 拦下
            self.assertTrue(
                "已有合成任务" in r2.json()["detail"]
                or "正在合成中" in r2.json()["detail"],
                r2.json()["detail"],
            )
            self.client.post(f"/api/podcast/episodes/{eid}/cancel")
            self._wait_status(eid, {"cancelled", "done", "error"}, timeout=6)

    def test_retry_while_running_is_409(self):
        pid = self._create_project()
        eid = self._create_episode(pid, "A: 重试并发句一\nA: 重试并发句二")
        slow = _TTSRecorder(delay=0.8, duration_ms=100)

        with patch("app.api.podcast.TTSService.synthesize", slow.synthesize):
            self.client.post("/api/podcast/synthesize", json={"episode_id": eid})
            r = self.client.post(f"/api/podcast/episodes/{eid}/retry")
            self.assertEqual(r.status_code, 409)
            self.client.post(f"/api/podcast/episodes/{eid}/cancel")
            self._wait_status(eid, {"cancelled", "done", "error"}, timeout=6)

    def test_retry_without_segments_400(self):
        pid = self._create_project()
        eid = self._create_episode(pid, "A: 从未合成过")
        r = self.client.post(f"/api/podcast/episodes/{eid}/retry")
        self.assertEqual(r.status_code, 400)
        self.assertIn("没有可续跑", r.json()["detail"])


class TestSynthesisLifecycle(SynthesisApiBase):
    def test_happy_path_with_intro_outro_and_gaps(self):
        pid = self._create_project()
        # 结构：句间 → 段落 → 章节，用于校验 gap_after_ms 分级
        script = "A: 第一句。\nA: 第二句。\n\n【第一章】\nA: 章内句。\n\nA: 收尾。"
        eid = self._create_episode(pid, script, intro="欢迎收听", outro="下期再见")
        recorder = _TTSRecorder(duration_ms=180)

        with patch("app.api.podcast.TTSService.synthesize", recorder.synthesize):
            r = self.client.post("/api/podcast/synthesize", json={"episode_id": eid})
            self.assertEqual(r.status_code, 200)
            # 正文 4 句（【第一章】跳过）+ 片头 + 片尾
            self.assertEqual(r.json()["total_lines"], 6)
            st = self._wait_status(eid, {"done", "error"}, timeout=10)
            self.assertEqual(st["status"], "done", st.get("error_message"))
            self.assertGreaterEqual(st["done_count"], 4)
            self.assertEqual(st["failed_count"], 0)
            self.assertTrue(st["audio_path"])

        body = sorted(
            (s for s in st["segments"] if isinstance(s.get("index"), int)),
            key=lambda s: s["index"],
        )
        self.assertEqual(len(body), 4)
        gaps = [s["gap_after_ms"] for s in body]
        self.assertEqual(gaps[-1], GAP_LEVEL_TO_MS[0])  # 最后一句不停顿
        self.assertIn(SILENCE_GAP_SHORT_MS, gaps)
        self.assertIn(SILENCE_GAP_PARAGRAPH_MS, gaps)
        self.assertIn(SILENCE_GAP_SECTION_MS, gaps)
        for s in body:
            self.assertTrue(s.get("fingerprint"))
            self.assertTrue(Path(s["audio_path"]).exists())

        # 片头/片尾也进入 segments 且按指纹复用字段落盘
        kinds = {s.get("kind") for s in st["segments"] if "kind" in s}
        self.assertIn("intro", kinds)
        self.assertIn("outro", kinds)

    def test_fingerprint_reuse_skips_unchanged_lines(self):
        pid = self._create_project()
        eid = self._create_episode(pid, "A: 不变的句子\nB: 也不变的句子")
        first = _TTSRecorder(duration_ms=150)

        with patch("app.api.podcast.TTSService.synthesize", first.synthesize):
            self.client.post("/api/podcast/synthesize", json={"episode_id": eid})
            st1 = self._wait_status(eid, {"done", "error"}, timeout=8)
            self.assertEqual(st1["status"], "done")
            self.assertEqual(len(first.calls), 2)

        # 配置与脚本均未变：第二次合成不应再打 TTS
        second = _TTSRecorder(duration_ms=150)
        with patch("app.api.podcast.TTSService.synthesize", second.synthesize):
            r = self.client.post("/api/podcast/synthesize", json={"episode_id": eid})
            self.assertEqual(r.status_code, 200)
            # synthesize 刻意不清 segments_json，以保留指纹
            st2 = self._wait_status(eid, {"done", "error"}, timeout=8)
            self.assertEqual(st2["status"], "done")
            self.assertEqual(
                second.calls, [],
                f"指纹未变却重做了 {len(second.calls)} 句: {second.calls}",
            )

    def test_text_change_only_redoes_changed_line(self):
        pid = self._create_project()
        eid = self._create_episode(pid, "A: 第一句保持\nB: 第二句保持")
        first = _TTSRecorder(duration_ms=150)

        with patch("app.api.podcast.TTSService.synthesize", first.synthesize):
            self.client.post("/api/podcast/synthesize", json={"episode_id": eid})
            self._wait_status(eid, {"done", "error"}, timeout=8)
            self.assertEqual(len(first.calls), 2)

        # 只改第二句
        r = self.client.put(
            f"/api/podcast/episodes/{eid}",
            json={"script": "A: 第一句保持\nB: 第二句改过了"},
        )
        self.assertEqual(r.status_code, 200)

        second = _TTSRecorder(duration_ms=150)
        with patch("app.api.podcast.TTSService.synthesize", second.synthesize):
            self.client.post("/api/podcast/synthesize", json={"episode_id": eid})
            st = self._wait_status(eid, {"done", "error"}, timeout=8)
            self.assertEqual(st["status"], "done")
            self.assertEqual(len(second.calls), 1, second.calls)
            self.assertIn("改过了", second.calls[0])

    def test_fail_fast_after_consecutive_errors(self):
        pid = self._create_project()
        lines = [f"A: 失败句{i}" for i in range(8)]
        eid = self._create_episode(pid, "\n".join(lines))
        # 全部失败：应触发 fail-fast，而不是硬撑完 8 句
        failer = _TTSRecorder(fail_all=True)

        with patch("app.api.podcast.TTSService.synthesize", failer.synthesize):
            self.client.post("/api/podcast/synthesize", json={"episode_id": eid})
            st = self._wait_status(eid, {"error", "done"}, timeout=10)
            self.assertEqual(st["status"], "error")
            self.assertGreaterEqual(st["failed_count"], 3)
            # fail-fast 后不应把 8 句全部打完（最多比阈值多一点并发在飞）
            self.assertLessEqual(len(failer.calls), 8)
            self.assertIn("失败", st["error_message"] or "")

    def test_partial_failure_then_retry_only_pending(self):
        pid = self._create_project()
        eid = self._create_episode(pid, "A: 会成功的\nB: 会失败的\nA: 也会成功")
        # 只让中间那句失败
        partial = _TTSRecorder(fail_texts={"会失败的"}, duration_ms=150)

        with patch("app.api.podcast.TTSService.synthesize", partial.synthesize):
            self.client.post("/api/podcast/synthesize", json={"episode_id": eid})
            st = self._wait_status(eid, {"error", "done"}, timeout=10)
            self.assertEqual(st["status"], "error")
            self.assertEqual(st["failed_count"], 1)
            self.assertEqual(st["done_count"], 2)

        # 续跑：故障恢复后全部成功；成功句按指纹复用
        ok = _TTSRecorder(duration_ms=150)
        with patch("app.api.podcast.TTSService.synthesize", ok.synthesize):
            r = self.client.post(f"/api/podcast/episodes/{eid}/retry")
            self.assertEqual(r.status_code, 200)
            st2 = self._wait_status(eid, {"done", "error"}, timeout=10)
            self.assertEqual(st2["status"], "done", st2.get("error_message"))
            self.assertEqual(len(ok.calls), 1)
            self.assertIn("会失败的", ok.calls[0])


class TestPreviewMissingReference(SynthesisApiBase):
    def test_missing_clone_reference_is_400_not_500(self):
        """克隆模式下参考音频在目录内但已删除：应 400 人话，而不是 500。"""
        ghost = str(VOICES_DIR / f"ghost_{uuid.uuid4().hex}.wav")
        r = self.client.post(
            "/api/podcast/preview-sentence",
            json={
                "text": "试听一句克隆音色",
                "model_type": "clone",
                "reference_audio": ghost,
            },
        )
        self.assertEqual(r.status_code, 400)
        detail = r.json()["detail"]
        self.assertTrue(
            "不存在" in detail or "已不存在" in detail or "参考音频" in detail,
            detail,
        )


class TestExportSrtWithIntro(SynthesisApiBase):
    def test_srt_export_accounts_for_intro_duration(self):
        """导出 SRT 时片头时长应把正文整体后移，而不是从 0 开始。"""
        pid = self._create_project()
        eid = self._create_episode(pid, "A: 字幕第一句\nA: 字幕第二句", intro="片头旁白")
        recorder = _TTSRecorder(duration_ms=1000)

        with patch("app.api.podcast.TTSService.synthesize", recorder.synthesize):
            self.client.post("/api/podcast/synthesize", json={"episode_id": eid})
            st = self._wait_status(eid, {"done", "error"}, timeout=10)
            self.assertEqual(st["status"], "done")

        r = self.client.get(f"/api/podcast/{eid}/export/srt")
        self.assertEqual(r.status_code, 200)
        srt = r.text
        self.assertIn("字幕第一句", srt)
        # 正文起点应 > 片头时长（约 1s）+ 段落停顿，绝不是 00:00:00,000 起拍
        first_start = srt.split("-->")[0].strip().split("\n")[-1]
        self.assertNotEqual(first_start, "00:00:00,000")
        # 解析起点秒数
        hh, mm, rest = first_start.split(":")
        ss, ms = rest.split(",")
        start_s = int(hh) * 3600 + int(mm) * 60 + int(ss) + int(ms) / 1000
        self.assertGreaterEqual(start_s, 1.0)

    def test_srt_without_segments_400(self):
        pid = self._create_project()
        eid = self._create_episode(pid, "A: 还没合成")
        r = self.client.get(f"/api/podcast/{eid}/export/srt")
        self.assertEqual(r.status_code, 400)


class TestCancelContract(SynthesisApiBase):
    def test_cancel_response_matches_contract(self):
        contract = json.loads(
            (Path(__file__).resolve().parents[2] / "contracts" / "api-contract.json")
            .read_text(encoding="utf-8")
        )
        keys = contract["endpoints"]["POST /api/podcast/episodes/{id}/cancel"][
            "required_keys"
        ]
        pid = self._create_project()
        eid = self._create_episode(pid, "A: 契约取消")
        r = self.client.post(f"/api/podcast/episodes/{eid}/cancel")
        for k in keys:
            self.assertIn(k, r.json(), f"cancel 响应缺少 {k}")
        self.assertIn("cancelled", contract["episode_status"])


if __name__ == "__main__":
    unittest.main()
