"""音频处理服务"""
import logging
import shutil
import subprocess
import tempfile
from pathlib import Path

from pydub import AudioSegment

from app.config import AUDIO_DIR, TEMP_DIR, PODCAST_LUFS

logger = logging.getLogger(__name__)


class AudioService:
    def __init__(self):
        self.temp_dir = TEMP_DIR
        self.temp_dir.mkdir(parents=True, exist_ok=True)

    def merge_audio(
        self,
        audio_files: list[str],
        output_name: str,
        silence_gap: int = 600,
        normalize: bool = True,
        intro_text_audio: str | None = None,
        outro_text_audio: str | None = None,
    ) -> str:
        """合并多个音频文件；可选片头/片尾；响度归一化"""
        if not audio_files and not intro_text_audio and not outro_text_audio:
            raise ValueError("没有音频文件可合并")

        parts: list[AudioSegment] = []
        if intro_text_audio and Path(intro_text_audio).exists():
            parts.append(AudioSegment.from_file(intro_text_audio))
            parts.append(AudioSegment.silent(duration=silence_gap))

        n = len(audio_files)
        for i, audio_file in enumerate(audio_files):
            parts.append(AudioSegment.from_file(audio_file))
            # 仅在「后面还有正文」或「后面还有片尾」时插一次间隔，避免片尾前双倍静音
            if i < n - 1 or (outro_text_audio and Path(outro_text_audio).exists()):
                parts.append(AudioSegment.silent(duration=silence_gap))

        if outro_text_audio and Path(outro_text_audio).exists():
            parts.append(AudioSegment.from_file(outro_text_audio))

        combined = AudioSegment.empty()
        for p in parts:
            combined += p

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

    def cleanup_temp(self) -> None:
        for f in self.temp_dir.glob("*"):
            try:
                f.unlink()
            except OSError:
                pass

    def cleanup_old_intermediates(self, keep_hours: int = 24) -> int:
        import time

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

    def build_srt(self, segments: list[dict]) -> str:
        """segments: [{speaker, text, audio_path, duration?}]"""
        lines = []
        cursor = 0.0
        idx = 1
        for seg in segments:
            dur = seg.get("duration")
            if dur is None:
                p = seg.get("audio_path")
                if p and Path(p).exists():
                    try:
                        dur = len(AudioSegment.from_file(p)) / 1000.0
                    except Exception:
                        dur = max(1.0, len(seg.get("text") or "") / 4.2)
                else:
                    dur = max(1.0, len(seg.get("text") or "") / 4.2)
            start = cursor
            end = cursor + float(dur)
            speaker = seg.get("speaker") or ""
            text = seg.get("text") or ""
            prefix = f"{speaker}：" if speaker else ""
            lines.append(str(idx))
            lines.append(f"{self.format_srt_time(start)} --> {self.format_srt_time(end)}")
            lines.append(f"{prefix}{text}")
            lines.append("")
            cursor = end
            idx += 1
        return "\n".join(lines)

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
