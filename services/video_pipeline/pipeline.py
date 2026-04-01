from __future__ import annotations

import json
import os
import shutil
import subprocess
from dotenv import load_dotenv

from .models import AssetRecord, RenderPlan, TimelineClip, to_dict
from .parser import ScriptParser
from .providers import (
    FFmpegVideoProvider,
    JimengVideoProvider,
    PlaceholderImageProvider,
    PlaceholderTTSProvider,
    PlaceholderVideoProvider,
    VolcImageProvider,
    VolcTTSProvider,
)

load_dotenv(dotenv_path=".env", override=True, encoding="utf-8")


class VideoPipelineService:
    def __init__(self, base_output_dir: str) -> None:
        self.base_output_dir = base_output_dir
        self.parser = ScriptParser()
        provider_mode = os.getenv("VIDEO_PIPELINE_MODE", "jimeng").strip().lower()
        self.use_jimeng = provider_mode == "jimeng"

        use_real_image = provider_mode in {"real", "jimeng"} or (
            provider_mode == "auto"
            and bool(os.getenv("VOLC_ACCESS_KEY_ID", "").strip())
            and bool(os.getenv("VOLC_SECRET_ACCESS_KEY", "").strip())
            and bool(os.getenv("VOLC_IMAGE_REQ_KEY", "").strip())
        )
        use_real_tts = provider_mode in {"real", "jimeng"} or (
            provider_mode == "auto" and bool(os.getenv("VOLC_TTS_APP_ID", "").strip()) and bool(os.getenv("VOLC_TTS_ACCESS_KEY", "").strip())
        )
        has_ffmpeg = shutil.which(os.getenv("FFMPEG_BINARY", "ffmpeg"))
        use_real_video = provider_mode in {"real", "jimeng"} or (
            provider_mode == "auto"
            and bool(os.getenv("VOLC_ACCESS_KEY_ID", "").strip())
            and bool(os.getenv("VOLC_SECRET_ACCESS_KEY", "").strip())
        )
        self.skip_image_generation = self.use_jimeng

        self.image_provider = VolcImageProvider() if use_real_image else PlaceholderImageProvider()
        self.tts_provider = VolcTTSProvider() if use_real_tts else PlaceholderTTSProvider()

        if self.use_jimeng and has_ffmpeg:
            self.video_provider = JimengVideoProvider(os.getenv("FFMPEG_BINARY", "ffmpeg"))
        elif use_real_video and has_ffmpeg:
            self.video_provider = JimengVideoProvider(os.getenv("FFMPEG_BINARY", "ffmpeg"))
        elif use_real_tts and has_ffmpeg:
            self.video_provider = FFmpegVideoProvider(os.getenv("FFMPEG_BINARY", "ffmpeg"))
        else:
            self.video_provider = PlaceholderVideoProvider()

    def build_project(
        self,
        title: str,
        theme: str,
        script_text: str,
        ratio: str = "9:16",
        resolution: str = "1080x1920",
        target_duration_seconds: float = 18.0,
        max_duration_seconds: float = 20.0,
    ) -> dict:
        project = self.parser.parse(title=title, theme=theme, script_text=script_text, ratio=ratio, resolution=resolution)
        project_dir = os.path.join(self.base_output_dir, project.project_id)
        storyboard_dir = os.path.join(project_dir, "storyboard")
        image_dir = os.path.join(project_dir, "assets", "images")
        audio_dir = os.path.join(project_dir, "assets", "audio")
        video_dir = os.path.join(project_dir, "assets", "video")
        output_dir = os.path.join(project_dir, "output")
        for path in [project_dir, storyboard_dir, image_dir, audio_dir, video_dir, output_dir]:
            os.makedirs(path, exist_ok=True)
        self._clear_directory(storyboard_dir)
        self._clear_directory(image_dir)
        self._clear_directory(audio_dir)
        self._clear_directory(video_dir)
        self._clear_directory(output_dir)

        self._write_json(os.path.join(project_dir, "project.json"), to_dict(project))

        assets = []
        timeline = []
        srt_blocks = []
        current_time = 0.0
        stop_generation = False

        for scene in project.scenes:
            if stop_generation:
                break
            self._write_json(os.path.join(storyboard_dir, f"{scene.scene_id}.json"), to_dict(scene))
            for shot in scene.shots:
                remaining_duration = max_duration_seconds - current_time
                if remaining_duration <= 0:
                    stop_generation = True
                    break
                if self.skip_image_generation:
                    image_asset = AssetRecord(
                        asset_id=f"image-{shot.shot_id}",
                        asset_type="image_skipped",
                        scene_id=shot.scene_id,
                        shot_id=shot.shot_id,
                        provider="skipped-for-jimeng",
                        file_path="",
                        prompt=shot.image_prompt,
                        duration_seconds=shot.duration_seconds,
                        metadata={"skipped": True, "reason": "jimeng video mode"},
                    )
                else:
                    try:
                        image_asset = self.image_provider.generate(shot, image_dir)
                    except Exception as exc:
                        raise RuntimeError(f"镜头 {shot.shot_id} 文生图失败：{exc}") from exc
                try:
                    audio_asset = self.tts_provider.synthesize(scene, shot, audio_dir)
                except Exception as exc:
                    raise RuntimeError(f"镜头 {shot.shot_id} TTS 失败：{exc}") from exc
                clip_duration = min(
                    max(audio_asset.duration_seconds, 0.8),
                    remaining_duration,
                )
                try:
                    video_asset = self.video_provider.render(shot, image_asset, audio_asset, video_dir, target_duration=clip_duration)
                except Exception as exc:
                    video_asset = self._fallback_video(shot, audio_asset, video_dir, clip_duration, str(exc))
                assets.extend([image_asset, audio_asset, video_asset])
                clip_duration = min(video_asset.duration_seconds, audio_asset.duration_seconds, remaining_duration)
                end_time = current_time + clip_duration
                subtitle_text = (audio_asset.metadata.get("transcript") if isinstance(audio_asset.metadata, dict) else "") or shot.tts_text or ""
                if not subtitle_text:
                    subtitle_text = ""
                timeline_clip = TimelineClip(
                    clip_id=f"clip-{shot.shot_id}",
                    scene_id=scene.scene_id,
                    shot_id=shot.shot_id,
                    start_seconds=current_time,
                    end_seconds=end_time,
                    visual_asset_id=video_asset.asset_id,
                    audio_asset_id=audio_asset.asset_id,
                    subtitle_text=subtitle_text,
                )
                timeline.append(timeline_clip)
                if subtitle_text.strip():
                    srt_blocks.append(self._build_srt_block(len(srt_blocks) + 1, current_time, end_time, subtitle_text))
                current_time = end_time
                if current_time >= target_duration_seconds:
                    stop_generation = True
                if current_time >= max_duration_seconds:
                    stop_generation = True
                    break

        subtitle_path = os.path.join(output_dir, "subtitles.srt")
        with open(subtitle_path, "w", encoding="utf-8") as file:
            file.write("\n\n".join(srt_blocks))

        concat_path = os.path.join(output_dir, "concat.txt")
        render_plan = RenderPlan(
            project_id=project.project_id,
            output_video_path=os.path.join(output_dir, "final_video.mp4"),
            subtitle_path=subtitle_path,
            timeline=timeline,
            assets=assets,
            commands=self._build_commands(concat_path, subtitle_path, os.path.join(output_dir, "final_video.mp4")),
            notes=[
                f"image_provider={'skipped' if self.skip_image_generation else self.image_provider.provider_name}",
                f"tts_provider={self.tts_provider.provider_name}",
                f"video_provider={self.video_provider.provider_name}",
                f"total_duration={current_time:.3f}",
            ],
        )
        self._write_concat_file(concat_path, render_plan)
        self._render_final_video(render_plan, concat_path)
        self._write_json(os.path.join(output_dir, "render_plan.json"), to_dict(render_plan))
        return {
            "project": to_dict(project),
            "render_plan": to_dict(render_plan),
            "project_dir": project_dir,
        }

    def _write_concat_file(self, file_path: str, render_plan: RenderPlan) -> None:
        lines = []
        for clip in render_plan.timeline:
            matching_assets = [asset for asset in render_plan.assets if asset.asset_id == clip.visual_asset_id]
            if matching_assets:
                normalized_path = matching_assets[0].file_path.replace("\\", "/")
                lines.append(f"file '{normalized_path}'")
                lines.append(f"duration {max(clip.end_seconds - clip.start_seconds, 0.1):.3f}")
        if render_plan.timeline:
            last_asset = [asset for asset in render_plan.assets if asset.asset_id == render_plan.timeline[-1].visual_asset_id]
            if last_asset:
                normalized_path = last_asset[0].file_path.replace("\\", "/")
                lines.append(f"file '{normalized_path}'")
        with open(file_path, "w", encoding="utf-8") as file:
            file.write("\n".join(lines))

    def _clear_directory(self, directory_path: str) -> None:
        if not os.path.isdir(directory_path):
            return
        for entry in os.listdir(directory_path):
            target_path = os.path.join(directory_path, entry)
            if os.path.isdir(target_path):
                shutil.rmtree(target_path, ignore_errors=True)
            else:
                try:
                    os.remove(target_path)
                except FileNotFoundError:
                    continue

    def _write_json(self, file_path: str, payload: dict) -> None:
        with open(file_path, "w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)

    def _build_commands(self, concat_path: str, subtitle_path: str, output_video_path: str) -> list[str]:
        ffmpeg_binary = os.getenv("FFMPEG_BINARY", "ffmpeg")
        subtitle_style = (
            "FontName=Microsoft YaHei,FontSize=13,PrimaryColour=&H00FFFFFF,"
            "OutlineColour=&H80000000,Bold=1,Outline=2,Shadow=1,MarginV=28"
        )
        return [
            f"{ffmpeg_binary} -y -f concat -safe 0 -i {concat_path} "
            f"-vf \"subtitles={subtitle_path}:force_style='{subtitle_style}'\" "
            f"-c:v libx264 -preset veryfast -c:a aac -b:a 128k {output_video_path}",
        ]

    def _render_final_video(self, render_plan: RenderPlan, concat_path: str) -> None:
        if self.video_provider.provider_name not in {"ffmpeg-video", "jimeng-video"}:
            return
        if not render_plan.timeline:
            return
        ffmpeg_binary = os.getenv("FFMPEG_BINARY", "ffmpeg")
        output_dir = os.path.dirname(render_plan.output_video_path)
        subtitle_name = os.path.basename(render_plan.subtitle_path)
        output_name = os.path.basename(render_plan.output_video_path)
        concat_name = os.path.basename(concat_path)
        total_duration = render_plan.timeline[-1].end_seconds
        subtitle_style = (
            "FontName=Microsoft YaHei,FontSize=13,PrimaryColour=&H00FFFFFF,"
            "OutlineColour=&H80000000,BackColour=&H80000000,"
            "Bold=1,Outline=2,Shadow=1,MarginV=28,Alignment=2"
        )
        subtitle_filter = f"subtitles={subtitle_name}:force_style='{subtitle_style}'"
        command = [
            ffmpeg_binary,
            "-y",
            "-f", "concat", "-safe", "0", "-i", concat_name,
            "-t", f"{total_duration:.3f}",
            "-vf", subtitle_filter,
            "-c:v", "libx264", "-preset", "veryfast",
            "-c:a", "aac", "-b:a", "128k",
            output_name,
        ]
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="ignore", cwd=output_dir)
        if completed.returncode != 0:
            raise RuntimeError(f"FFmpeg 最终拼接失败：{completed.stderr}")

    def _fallback_video(self, shot: Shot, audio_asset: AssetRecord, video_dir: str, clip_duration: float, error_message: str) -> AssetRecord:
        """当视频渲染失败时，用 FFmpeg 生成黑屏+音频的兜底视频，避免整条管线中断。"""
        import logging
        logging.getLogger(__name__).warning(f"镜头 {shot.shot_id} 视频渲染失败，使用兜底方案：{error_message[:200]}")
        ffmpeg_binary = os.getenv("FFMPEG_BINARY", "ffmpeg")
        output_path = os.path.join(video_dir, f"{shot.shot_id}.mp4")
        duration = max(clip_duration, 0.5)
        command = [
            ffmpeg_binary, "-y",
            "-f", "lavfi", "-i", f"color=c=black:s=1080x1920:d={duration:.3f}:r=25",
            "-i", audio_asset.file_path,
            "-t", f"{duration:.3f}",
            "-map", "0:v:0", "-map", "1:a:0",
            "-c:v", "libx264", "-preset", "veryfast",
            "-c:a", "aac", "-shortest",
            output_path,
        ]
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="ignore")
        if completed.returncode != 0:
            raise RuntimeError(f"镜头 {shot.shot_id} 兜底视频也失败：{completed.stderr}")
        return AssetRecord(
            asset_id=f"video-{shot.shot_id}",
            asset_type="video_fallback",
            scene_id=shot.scene_id,
            shot_id=shot.shot_id,
            provider="ffmpeg-fallback",
            file_path=output_path,
            prompt=shot.video_prompt,
            duration_seconds=duration,
            metadata={"fallback_reason": error_message[:500]},
        )

    def _build_srt_block(self, index: int, start_seconds: float, end_seconds: float, text: str) -> str:
        return f"{index}\n{self._format_srt_time(start_seconds)} --> {self._format_srt_time(end_seconds)}\n{text}"

    def _format_srt_time(self, total_seconds: float) -> str:
        milliseconds = int(round(total_seconds * 1000))
        hours = milliseconds // 3_600_000
        minutes = (milliseconds % 3_600_000) // 60_000
        seconds = (milliseconds % 60_000) // 1000
        millis = milliseconds % 1000
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"
