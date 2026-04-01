"""FFmpeg 合成器

将背景图 + 透明通道角色视频 + 音频 + 字幕 合成为最终视频片段。
支持多片段拼接和 BGM 叠加。
"""

from __future__ import annotations

import os
import shutil
import subprocess


class Live2DCompositor:
    """合成背景、角色视频、音频、字幕为最终成品。"""

    def __init__(self, ffmpeg_binary: str | None = None) -> None:
        self.ffmpeg = ffmpeg_binary or shutil.which(os.environ.get("FFMPEG_BINARY", "ffmpeg")) or "ffmpeg"

    def composite_clip(
        self,
        background_path: str,
        character_frames_dir: str,
        audio_path: str,
        output_path: str,
        duration_seconds: float,
        fps: int = 25,
        width: int = 1080,
        height: int = 1920,
    ) -> str:
        """将背景图 + 角色帧序列 + 音频合成为单个视频片段。"""
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        frame_pattern = os.path.join(character_frames_dir, "frame_%05d.png")
        has_frames = any(
            f.startswith("frame_") and f.endswith(".png")
            for f in os.listdir(character_frames_dir)
        ) if os.path.isdir(character_frames_dir) else False

        if has_frames and os.path.isfile(background_path):
            command = [
                self.ffmpeg, "-y",
                "-loop", "1", "-i", background_path,
                "-framerate", str(fps), "-i", frame_pattern,
                "-i", audio_path,
                "-t", f"{duration_seconds:.3f}",
                "-filter_complex",
                f"[0:v]scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},setsar=1[bg];"
                f"[1:v]scale={width}:{height}:flags=lanczos,format=rgba[char];"
                f"[bg][char]overlay=0:0:format=auto[out]",
                "-map", "[out]",
                "-map", "2:a",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k",
                "-r", str(fps),
                "-shortest",
                "-pix_fmt", "yuv420p",
                output_path,
            ]
        elif has_frames:
            command = [
                self.ffmpeg, "-y",
                "-framerate", str(fps), "-i", frame_pattern,
                "-i", audio_path,
                "-t", f"{duration_seconds:.3f}",
                "-filter_complex",
                f"[0:v]scale={width}:{height}:flags=lanczos,"
                f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=black,format=yuv420p[v]",
                "-map", "[v]",
                "-map", "1:a",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k",
                "-r", str(fps),
                "-shortest",
                output_path,
            ]
        else:
            command = [
                self.ffmpeg, "-y",
                "-loop", "1", "-i", background_path,
                "-i", audio_path,
                "-t", f"{duration_seconds:.3f}",
                "-vf",
                f"scale={width}:{height}:force_original_aspect_ratio=increase,"
                f"crop={width}:{height},setsar=1,format=yuv420p",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
                "-c:a", "aac", "-b:a", "128k",
                "-r", str(fps),
                "-shortest",
                output_path,
            ]

        result = subprocess.run(
            command, capture_output=True, text=True,
            encoding="utf-8", errors="ignore",
        )
        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg 片段合成失败：{result.stderr[-500:]}")
        return output_path

    def concat_clips(
        self,
        clip_paths: list[str],
        output_path: str,
        subtitle_path: str | None = None,
        bgm_path: str | None = None,
        bgm_volume: float = 0.15,
        total_duration: float | None = None,
    ) -> str:
        """拼接多个视频片段为最终成品，叠加字幕和 BGM。"""
        os.makedirs(os.path.dirname(output_path), exist_ok=True)

        concat_file = output_path + ".concat.txt"
        with open(concat_file, "w", encoding="utf-8") as f:
            for clip in clip_paths:
                normalized = clip.replace("\\", "/")
                f.write(f"file '{normalized}'\n")

        filter_parts: list[str] = []
        inputs = [self.ffmpeg, "-y", "-f", "concat", "-safe", "0", "-i", concat_file]

        if bgm_path and os.path.isfile(bgm_path):
            inputs.extend(["-i", bgm_path])

        if subtitle_path and os.path.isfile(subtitle_path):
            sub_name = os.path.basename(subtitle_path)
            sub_style = (
                "FontName=Microsoft YaHei,FontSize=13,PrimaryColour=&H00FFFFFF,"
                "OutlineColour=&H80000000,BackColour=&H80000000,"
                "Bold=1,Outline=2,Shadow=1,MarginV=28,Alignment=2"
            )
            filter_parts.append(f"subtitles={sub_name}:force_style='{sub_style}'")

        command = list(inputs)

        if total_duration:
            command.extend(["-t", f"{total_duration:.3f}"])

        if bgm_path and os.path.isfile(bgm_path):
            filter_complex = (
                f"[0:a]volume=1.0[voice];"
                f"[1:a]volume={bgm_volume:.2f},aloop=loop=-1:size=2e+09[bgm];"
                f"[voice][bgm]amix=inputs=2:duration=first[aout]"
            )
            if filter_parts:
                vf = ",".join(filter_parts)
                filter_complex = f"[0:v]{vf}[vout];" + filter_complex
                command.extend(["-filter_complex", filter_complex, "-map", "[vout]", "-map", "[aout]"])
            else:
                command.extend(["-filter_complex", filter_complex, "-map", "0:v", "-map", "[aout]"])
        else:
            if filter_parts:
                command.extend(["-vf", ",".join(filter_parts)])
            command.extend(["-c:a", "aac", "-b:a", "128k"])

        command.extend([
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "22",
            "-pix_fmt", "yuv420p",
            output_path,
        ])

        cwd = os.path.dirname(output_path)
        result = subprocess.run(
            command, capture_output=True, text=True,
            encoding="utf-8", errors="ignore", cwd=cwd,
        )

        if os.path.exists(concat_file):
            os.remove(concat_file)

        if result.returncode != 0:
            raise RuntimeError(f"FFmpeg 最终拼接失败：{result.stderr[-500:]}")
        return output_path

    def generate_background(
        self,
        output_path: str,
        color: tuple[int, int, int] = (20, 20, 30),
        width: int = 1080,
        height: int = 1920,
        gradient: bool = True,
    ) -> str:
        """生成一张纯色/渐变背景图作为默认背景。"""
        try:
            from PIL import Image, ImageDraw
        except ImportError:
            raise RuntimeError("Pillow 未安装")

        img = Image.new("RGB", (width, height), color)
        if gradient:
            draw = ImageDraw.Draw(img)
            r, g, b = color
            for y in range(height):
                ratio = y / height
                factor = 0.6 + 0.4 * ratio
                row_color = (
                    int(r * factor),
                    int(g * factor),
                    int(b * factor),
                )
                draw.line([(0, y), (width, y)], fill=row_color)

        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        img.save(output_path, "PNG")
        return output_path
