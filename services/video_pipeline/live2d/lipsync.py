"""LipSync 口型同步模块

从音频文件分析振幅曲线，生成逐帧口型参数（0.0~1.0），
用于驱动 Live2D 模型的嘴部张合。

支持 WAV 和 MP3 格式。MP3 先用 FFmpeg 转 WAV 再分析。
"""

from __future__ import annotations

import math
import os
import struct
import subprocess
import shutil
import tempfile
import wave
from dataclasses import dataclass, field


@dataclass
class LipSyncFrame:
    frame_index: int
    timestamp: float
    amplitude: float
    mouth_open: float
    viseme: str = "closed"


@dataclass
class LipSyncData:
    fps: int = 25
    duration_seconds: float = 0.0
    frames: list[LipSyncFrame] = field(default_factory=list)

    def mouth_value_at(self, time_seconds: float) -> float:
        if not self.frames:
            return 0.0
        idx = int(time_seconds * self.fps)
        idx = max(0, min(idx, len(self.frames) - 1))
        return self.frames[idx].mouth_open


class LipSyncAnalyzer:
    """分析音频文件，生成逐帧口型同步数据。"""

    def __init__(self, fps: int = 25, smoothing: float = 0.3) -> None:
        self.fps = fps
        self.smoothing = max(0.0, min(smoothing, 0.95))

    def analyze(self, audio_path: str, duration_seconds: float | None = None) -> LipSyncData:
        if not os.path.isfile(audio_path):
            return self._silent_data(duration_seconds or 1.0)

        wav_path = self._ensure_wav(audio_path)
        try:
            samples, sample_rate, n_channels = self._read_wav(wav_path)
        except Exception:
            return self._silent_data(duration_seconds or 1.0)
        finally:
            if wav_path != audio_path and os.path.exists(wav_path):
                os.remove(wav_path)

        if not samples:
            return self._silent_data(duration_seconds or 1.0)

        actual_duration = len(samples) / sample_rate
        if duration_seconds and duration_seconds > 0:
            actual_duration = min(actual_duration, duration_seconds)

        total_frames = max(int(actual_duration * self.fps), 1)
        samples_per_frame = max(len(samples) // total_frames, 1)

        raw_amplitudes: list[float] = []
        for i in range(total_frames):
            start = i * samples_per_frame
            end = min(start + samples_per_frame, len(samples))
            chunk = samples[start:end]
            if chunk:
                rms = math.sqrt(sum(s * s for s in chunk) / len(chunk))
                raw_amplitudes.append(rms)
            else:
                raw_amplitudes.append(0.0)

        max_amp = max(raw_amplitudes) if raw_amplitudes else 1.0
        if max_amp < 1e-6:
            max_amp = 1.0
        normalized = [min(a / max_amp, 1.0) for a in raw_amplitudes]

        smoothed = self._smooth(normalized)

        mouth_values = [self._amplitude_to_mouth(v) for v in smoothed]

        frames: list[LipSyncFrame] = []
        for i, mv in enumerate(mouth_values):
            timestamp = i / self.fps
            frames.append(LipSyncFrame(
                frame_index=i,
                timestamp=timestamp,
                amplitude=smoothed[i],
                mouth_open=mv,
                viseme=self._amplitude_to_viseme(mv),
            ))

        return LipSyncData(
            fps=self.fps,
            duration_seconds=actual_duration,
            frames=frames,
        )

    def _ensure_wav(self, audio_path: str) -> str:
        ext = os.path.splitext(audio_path)[1].lower()
        if ext == ".wav":
            return audio_path
        ffmpeg = shutil.which(os.environ.get("FFMPEG_BINARY", "ffmpeg")) or "ffmpeg"
        tmp = tempfile.mktemp(suffix=".wav")
        try:
            subprocess.run(
                [ffmpeg, "-y", "-i", audio_path, "-ar", "16000", "-ac", "1", tmp],
                capture_output=True, check=True,
            )
            return tmp
        except Exception:
            return audio_path

    def _read_wav(self, wav_path: str) -> tuple[list[float], int, int]:
        with wave.open(wav_path, "rb") as wf:
            n_channels = wf.getnchannels()
            sample_width = wf.getsampwidth()
            sample_rate = wf.getframerate()
            n_frames = wf.getnframes()
            raw = wf.readframes(n_frames)

        if sample_width == 2:
            fmt = f"<{n_frames * n_channels}h"
            raw_samples = struct.unpack(fmt, raw)
            samples = [s / 32768.0 for s in raw_samples]
        elif sample_width == 1:
            samples = [(b - 128) / 128.0 for b in raw]
        else:
            samples = [0.0] * n_frames

        if n_channels > 1:
            mono = []
            for i in range(0, len(samples), n_channels):
                mono.append(sum(samples[i:i + n_channels]) / n_channels)
            samples = mono

        return samples, sample_rate, n_channels

    def _smooth(self, values: list[float]) -> list[float]:
        if not values or self.smoothing <= 0:
            return values
        result = [values[0]]
        alpha = self.smoothing
        for v in values[1:]:
            result.append(alpha * result[-1] + (1 - alpha) * v)
        return result

    def _amplitude_to_mouth(self, amplitude: float) -> float:
        if amplitude < 0.05:
            return 0.0
        if amplitude < 0.15:
            return amplitude * 3.0
        if amplitude < 0.5:
            return 0.3 + (amplitude - 0.15) * 1.5
        return min(0.82 + (amplitude - 0.5) * 0.36, 1.0)

    def _amplitude_to_viseme(self, mouth_open: float) -> str:
        if mouth_open < 0.05:
            return "closed"
        if mouth_open < 0.25:
            return "narrow"
        if mouth_open < 0.55:
            return "mid"
        if mouth_open < 0.8:
            return "wide"
        return "open"

    def _silent_data(self, duration: float) -> LipSyncData:
        total_frames = max(int(duration * self.fps), 1)
        frames = [
            LipSyncFrame(
                frame_index=i,
                timestamp=i / self.fps,
                amplitude=0.0,
                mouth_open=0.0,
                viseme="closed",
            )
            for i in range(total_frames)
        ]
        return LipSyncData(fps=self.fps, duration_seconds=duration, frames=frames)
