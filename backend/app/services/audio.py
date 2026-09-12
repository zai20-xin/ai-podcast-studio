"""音频处理服务"""
import time
import wave
import logging
import shutil
import subprocess
from pathlib import Path

from pydub import AudioSegment

from app.config import (
    AUDIO_DIR,
    TEMP_DIR,
    PODCAST_LUFS,
    SILENCE_GAP_MS,
    SILENCE_GAP_PARAGRAPH_MS,
)

logger = logging.getLogger(__name__)


class AudioService:
    def __init__(self):
        self.temp_dir = TEMP_DIR
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    def merge_audio(
        self,
        audio_files: list[str],
        output_name: str,
        silence_gap: int = SILENCE_GAP_MS,
        normalize: bool = True,
        intro_text_audio: str | None = None,
        outro_text_audio: str | None = None,
        gap_after_ms: list[int | None] | None = None,
    ) -> str:
        """合并多个音频文件；可选片头/片尾；响度归一化。

        gap_after_ms 给出每一句之后应留出的停顿（毫秒），优先于统一的 silence_gap，
        使段落与章节之间能有明显更长的呼吸感 —— 单一停顿长度是「听起来平」的主要来源。
        """
        if not audio_files and not intro_text_audio and not outro_text_audio:
            raise ValueError("没有音频文件可合并")

        has_outro = bool(outro_text_audio and Path(outro_text_audio).exists())

        parts: list[AudioSegment] = []
        if intro_text_audio and Path(intro_text_audio).exists():
            parts.append(AudioSegment.from_file(intro_text_audio))
            # 片头到正文之间用段落级停顿，像节目正式开场
            parts.append(AudioSegment.silent(duration=SILENCE_GAP_PARAGRAPH_MS))

        n = len(audio_files)
        for i, audio_file in enumerate(audio_files):
            parts.append(AudioSegment.from_file(audio_file))
            # 仅在「后面还有正文」或「后面还有片尾」时插一次间隔，避免片尾前双倍静音
            if i < n - 1 or has_outro:
                gap = None
                if gap_after_ms is not None and i < len(gap_after_ms):
                    gap = gap_after_ms[i]
                # 末句的 gap_after 通常是 0（正文结束不停顿），
                # 但若后面还有片尾，必须用默认停顿隔开，否则片尾会贴着正文。
                if gap is None or (gap == 0 and has_outro and i == n - 1):
                    gap = silence_gap
                parts.append(AudioSegment.silent(duration=max(0, int(gap))))

        if has_outro:
            parts.append(AudioSegment.from_file(outro_text_audio))

        if not parts:
            raise ValueError("没有可合并的音频")

        # 统一参数后一次性拼接。
        # 旧写法 `for p in parts: combined += p` 每一轮都会复制已累积的全部数据，
        # 200 段的整篇稿件会退化成 O(n²) 的内存拷贝，越到后面越慢。
        # 注意：pydub 的 from_mono_audiosegments 是「把多个单声道合成一个多声道」，
        # 不是时序拼接，这里不能用它；改为把 raw_data 单次 join。
        target_rate = parts[0].frame_rate
        target_width = parts[0].sample_width
        normalized: list[AudioSegment] = []
        for p in parts:
            if p.channels != 1:
                p = p.set_channels(1)
            if p.frame_rate != target_rate:
                p = p.set_frame_rate(target_rate)
            if p.sample_width != target_width:
                p = p.set_sample_width(target_width)
            normalized.append(p)

        combined = AudioSegment(
            data=b"".join(s.raw_data for s in normalized),
            sample_width=target_width,
            frame_rate=target_rate,
            channels=1,
        )

        output_path = AUDIO_DIR / f"{output_name}.wav"
        combined.export(str(output_path), format="wav")
        logger.info("合并完成: %s (%.1f秒)", output_path, len(combined) / 1000)

        if normalize:
            normalized_path = self.normalize_volume(str(output_path))
            if normalized_path != str(output_path):
                Path(output_path).unlink(missing_ok=True)
                output_path = Path(normalized_path)

        return str(output_path)

    def convert_to_mp3(self, wav_path: str, output_path: str | None = None) -> str:
        audio = AudioSegment.from_file(wav_path)
        if output_path is None:
            output_path = wav_path.replace(".wav", ".mp3")
        audio.export(output_path, format="mp3", bitrate="192k")
        logger.info("转换完成: %s", output_path)
        return output_path

    def normalize_volume(self, audio_path: str, target_dbfs: float = -20.0) -> str:
        """优先 ffmpeg loudnorm 到播客响度；失败则回退 pydub 增益"""
        loud = self._ffmpeg_loudnorm(audio_path)
        if loud:
            return loud
        try:
            audio = AudioSegment.from_file(audio_path)
            if audio.dBFS == float("-inf") or len(audio) == 0:
                logger.warning("音频为空或静音，跳过响度归一: %s", audio_path)
                return audio_path
            change_in_dbfs = target_dbfs - audio.dBFS
            normalized = audio.apply_gain(change_in_dbfs)
            path = Path(audio_path)
            output_path = path.with_name(f"{path.stem}_norm{path.suffix}")
            normalized.export(str(output_path), format=path.suffix.lstrip(".") or "wav")
            return str(output_path)
        except Exception as e:
            logger.warning("音量统一失败: %s", e)
            return audio_path

    def _ffmpeg_loudnorm(self, audio_path: str, target_i: float | None = None) -> str | None:
        if not shutil.which("ffmpeg"):
            return None
        target = target_i if target_i is not None else PODCAST_LUFS
        path = Path(audio_path)
        output_path = path.with_name(f"{path.stem}_norm{path.suffix}")
        cmd = [
            "ffmpeg", "-y", "-i", str(path),
            "-af", f"loudnorm=I={target}:TP=-1.5:LRA=11",
            "-ar", "44100",
            str(output_path),
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, timeout=120, check=False
            )
            if proc.returncode == 0 and output_path.exists() and output_path.stat().st_size > 0:
                logger.info("loudnorm 完成 → %s", output_path)
                return str(output_path)
            logger.warning("ffmpeg loudnorm 失败: %s", proc.stderr[-300:])
            return None
        except Exception as e:
            logger.warning("ffmpeg loudnorm 异常: %s", e)
            return None

    @staticmethod
    def safe_unlink(*paths: str | Path | None) -> None:
        for p in paths:
            if not p:
                continue
            try:
                Path(p).unlink(missing_ok=True)
            except OSError as e:
                logger.warning("删除文件失败 %s: %s", p, e)

    def cleanup_files(self, files: list[str]) -> None:
        self.safe_unlink(*files)

    def cleanup_temp(self, keep_hours: int = 6) -> int:
        """清理临时目录中的陈旧文件。

        加了时间保护：原实现无条件删除目录下全部内容，若在合成进行中被调用
        会误删正在使用的中间产物。
        """
        cutoff = time.time() - keep_hours * 3600
        removed = 0
        for f in self.temp_dir.glob("*"):
            try:
                if f.is_file() and f.stat().st_mtime < cutoff:
                    f.unlink()
                    removed += 1
            except OSError:
                pass
        return removed

    def cleanup_old_intermediates(self, keep_hours: int = 24) -> int:
        cutoff = time.time() - keep_hours * 3600
        removed = 0
        for f in AUDIO_DIR.glob("tts_*.wav"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    removed += 1
            except OSError:
                pass
        return removed

    @staticmethod
    def format_srt_time(seconds: float) -> str:
        if seconds < 0:
            seconds = 0
        ms = int(round(seconds * 1000))
        h, rem = divmod(ms, 3600000)
        m, rem = divmod(rem, 60000)
        s, ms = divmod(rem, 1000)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"

    @staticmethod
    def wav_duration_seconds(audio_path: str | None) -> float | None:
        """只读 wav 文件头取时长。

        比 pydub 的 AudioSegment.from_file 快得多 —— 后者会完整解码音频，
        而导出字幕需要逐句取时长，长稿下差异非常明显。
        """
        if not audio_path:
            return None
        p = Path(audio_path)
        if not p.exists() or p.suffix.lower() != ".wav":
            return None
        try:
            with wave.open(str(p), "rb") as w:
                rate = w.getframerate()
                if not rate:
                    return None
                return w.getnframes() / float(rate)
        except Exception:
            return None

    def wav_duration_ms(self, audio_path: str | None) -> int:
        seconds = self.wav_duration_seconds(audio_path)
        return int(seconds * 1000) if seconds else 0

    def _segment_duration(self, seg: dict) -> float:
        dur = seg.get("duration")
        if dur is not None:
            return float(dur)
        measured = self.wav_duration_seconds(seg.get("audio_path"))
        if measured is not None:
            return measured
        return max(1.0, len(seg.get("text") or "") / 4.2)

    def build_srt(
        self,
        segments: list[dict],
        silence_gap_ms: int = SILENCE_GAP_MS,
        intro_duration_ms: int = 0,
        has_outro: bool = False,
    ) -> str:
        """生成 SRT。

        时间轴必须与 merge_audio 的实际拼接方式一致：合并时句与句之间会插入
        silence_gap 停顿，片头之后同样有一次停顿。旧实现按「上一句结束即下一句开始」
        累加，偏移会随句数线性累积（第 N 句约偏 0.6×(N-1) 秒）。

        segments: [{speaker, text, audio_path, duration?}]
        """
        default_gap = max(0.0, silence_gap_ms / 1000.0)
        cursor = 0.0
        if intro_duration_ms > 0:
            # 与 merge_audio 保持一致：片头之后是段落级停顿
            cursor += intro_duration_ms / 1000.0 + SILENCE_GAP_PARAGRAPH_MS / 1000.0

        lines = []
        idx = 1
        total = len(segments)
        for i, seg in enumerate(segments):
            dur = self._segment_duration(seg)
            start = cursor
            end = cursor + dur
            speaker = seg.get("speaker") or ""
            text = seg.get("text") or ""
            prefix = f"{speaker}：" if speaker else ""
            lines.append(str(idx))
            lines.append(f"{self.format_srt_time(start)} --> {self.format_srt_time(end)}")
            lines.append(f"{prefix}{text}")
            lines.append("")
            cursor = end
            # 与 merge_audio 对齐：仅当后面还有正文或片尾时才插停顿
            if i < total - 1 or has_outro:
                gap_s = self._gap_seconds(seg, default_gap)
                # 末句 gap_after 常为 0，但片尾前必须用默认停顿隔开
                if i == total - 1 and has_outro and gap_s == 0:
                    gap_s = default_gap
                cursor += gap_s
            idx += 1
        return "\n".join(lines)

    @staticmethod
    def _gap_seconds(seg: dict, default_seconds: float) -> float:
        """取该句之后的停顿秒数；没有逐句信息时回退到默认值"""
        ms = seg.get("gap_after_ms")
        return ms / 1000.0 if ms is not None else default_seconds

    @staticmethod
    def build_script_markdown(
        title: str,
        script: str,
        mode: str,
        hosts: dict | None = None,
    ) -> str:
        mode_label = "双人对谈" if mode == "dialogue" else "单人朗读"
        parts = [
            f"# {title or '播客脚本'}",
            "",
            f"- 模式：{mode_label}",
        ]
        if hosts:
            for label, info in hosts.items():
                if not info:
                    continue
                voice = info.get("voice_id") or info.get("voice_description") or "—"
                style = info.get("style") or "默认"
                parts.append(f"- {label}：{voice} · {style}")
        parts.extend(["", "---", "", script, ""])
        return "\n".join(parts)
