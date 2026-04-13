from __future__ import annotations

import json
import os
import re
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
        self.ffmpeg_video_provider = FFmpegVideoProvider(os.getenv("FFMPEG_BINARY", "ffmpeg")) if has_ffmpeg else None

    def build_project(
        self,
        title: str,
        theme: str,
        script_text: str,
        ratio: str = "4:3",
        resolution: str = "1440x1080",
        target_duration_seconds: float = 18.0,
        max_duration_seconds: float = 20.0,
    ) -> dict:
        os.environ["VIDEO_OUTPUT_RATIO"] = ratio
        os.environ["VIDEO_OUTPUT_RESOLUTION"] = resolution
        project = self.parser.parse(title=title, theme=theme, script_text=script_text, ratio=ratio, resolution=resolution)
        if hasattr(self.tts_provider, 'set_character_profiles') and project.characters:
            self.tts_provider.set_character_profiles(project.characters)
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
                    video_asset, image_asset = self._recover_video_asset(
                        shot=shot,
                        image_asset=image_asset,
                        audio_asset=audio_asset,
                        image_dir=image_dir,
                        video_dir=video_dir,
                        clip_duration=clip_duration,
                        error_message=str(exc),
                    )
                assets.extend([image_asset, audio_asset, video_asset])
                clip_duration = min(video_asset.duration_seconds, audio_asset.duration_seconds, remaining_duration)
                end_time = current_time + clip_duration
                subtitle_text = (audio_asset.metadata.get("transcript") if isinstance(audio_asset.metadata, dict) else "") or shot.tts_text or ""
                if not subtitle_text:
                    subtitle_text = ""
                subtitle_events = self._offset_subtitle_events(
                    (audio_asset.metadata.get("subtitle_events") if isinstance(audio_asset.metadata, dict) else []) or [],
                    current_time,
                    end_time,
                )
                timeline_clip = TimelineClip(
                    clip_id=f"clip-{shot.shot_id}",
                    scene_id=scene.scene_id,
                    shot_id=shot.shot_id,
                    start_seconds=current_time,
                    end_seconds=end_time,
                    visual_asset_id=video_asset.asset_id,
                    audio_asset_id=audio_asset.asset_id,
                    subtitle_text=subtitle_text,
                    subtitle_events=subtitle_events,
                )
                timeline.append(timeline_clip)
                for event in subtitle_events:
                    if str(event.get("text", "")).strip():
                        srt_blocks.append(
                            self._build_srt_block(
                                len(srt_blocks) + 1,
                                float(event["start_seconds"]),
                                float(event["end_seconds"]),
                                str(event["text"]),
                            )
                        )
                current_time = end_time
                if current_time >= target_duration_seconds:
                    stop_generation = True
                if current_time >= max_duration_seconds:
                    stop_generation = True
                    break

        subtitle_path = os.path.join(output_dir, "subtitles.srt")
        with open(subtitle_path, "w", encoding="utf-8") as file:
            file.write("\n\n".join(srt_blocks))
        subtitle_ass_path = os.path.join(output_dir, "subtitles.ass")
        self._write_ass_subtitles(subtitle_ass_path, project, timeline)

        concat_path = os.path.join(output_dir, "concat.txt")
        render_plan = RenderPlan(
            project_id=project.project_id,
            output_video_path=os.path.join(output_dir, "final_video.mp4"),
            subtitle_path=subtitle_path,
            timeline=timeline,
            assets=assets,
            commands=self._build_commands(concat_path, subtitle_ass_path, os.path.join(output_dir, "final_video.mp4")),
            notes=[
                f"image_provider={'skipped' if self.skip_image_generation else self.image_provider.provider_name}",
                f"tts_provider={self.tts_provider.provider_name}",
                f"video_provider={self.video_provider.provider_name}",
                f"total_duration={current_time:.3f}",
            ],
        )
        self._write_concat_file(concat_path, render_plan)
        self._render_final_video(render_plan, concat_path, subtitle_ass_path)
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
        return [
            f"{ffmpeg_binary} -y -f concat -safe 0 -i {concat_path} "
            f"-vf \"ass={subtitle_path}\" "
            f"-c:v libx264 -preset veryfast -c:a aac -b:a 128k {output_video_path}",
        ]

    def _render_final_video(self, render_plan: RenderPlan, concat_path: str, subtitle_ass_path: str) -> None:
        if self.video_provider.provider_name not in {"ffmpeg-video", "jimeng-video"}:
            return
        if not render_plan.timeline:
            return
        ffmpeg_binary = os.getenv("FFMPEG_BINARY", "ffmpeg")
        output_dir = os.path.dirname(render_plan.output_video_path)
        subtitle_name = os.path.basename(subtitle_ass_path)
        output_name = os.path.basename(render_plan.output_video_path)
        concat_name = os.path.basename(concat_path)
        total_duration = render_plan.timeline[-1].end_seconds
        subtitle_filter = f"ass={subtitle_name}"
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

    def _write_ass_subtitles(self, file_path: str, project, timeline: list[TimelineClip]) -> None:
        width, height = self._resolution_size(project.resolution)
        scene_map = {scene.scene_id: scene for scene in project.scenes}
        first_seen_characters: set[str] = set()
        lines = [
            "[Script Info]",
            "ScriptType: v4.00+",
            f"PlayResX: {width}",
            f"PlayResY: {height}",
            "WrapStyle: 2",
            "ScaledBorderAndShadow: yes",
            "",
            "[V4+ Styles]",
            "Format: Name,Fontname,Fontsize,PrimaryColour,SecondaryColour,OutlineColour,BackColour,Bold,Italic,Underline,StrikeOut,ScaleX,ScaleY,Spacing,Angle,BorderStyle,Outline,Shadow,Alignment,MarginL,MarginR,MarginV,Encoding",
            "Style: DialogueBottom,Microsoft YaHei,30,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,2,1,2,72,72,46,1",
            "Style: DialogueTop,Microsoft YaHei,28,&H00FFFFFF,&H000000FF,&H00000000,&H64000000,1,0,0,0,100,100,0,0,1,2,1,8,72,72,82,1",
            "Style: LocationLeft,Microsoft YaHei,26,&H00FFF2D8,&H000000FF,&H00000000,&H50000000,1,0,0,0,100,100,0,0,1,2,0,7,56,56,48,1",
            "Style: LocationRight,Microsoft YaHei,26,&H00FFF2D8,&H000000FF,&H00000000,&H50000000,1,0,0,0,100,100,0,0,1,2,0,9,56,56,48,1",
            "Style: NameLeft,Microsoft YaHei,22,&H00C7F2FF,&H000000FF,&H00000000,&H50000000,1,0,0,0,100,100,0,0,1,2,0,4,42,42,104,1",
            "Style: NameRight,Microsoft YaHei,22,&H00C7F2FF,&H000000FF,&H00000000,&H50000000,1,0,0,0,100,100,0,0,1,2,0,6,42,42,104,1",
            "",
            "[Events]",
            "Format: Layer,Start,End,Style,Name,MarginL,MarginR,MarginV,Effect,Text",
        ]
        for index, clip in enumerate(timeline):
            scene = scene_map.get(clip.scene_id)
            if scene is None:
                continue
            for event in clip.subtitle_events:
                wrapped_subtitle = self._wrap_subtitle_text(str(event.get("text", "")), max_line_length=12)
                if wrapped_subtitle:
                    subtitle_style = self._select_dialogue_style(scene, clip.shot_id)
                    lines.append(
                        f"Dialogue: 0,{self._format_ass_time(float(event['start_seconds']))},{self._format_ass_time(float(event['end_seconds']))},{subtitle_style},,0,0,0,,{self._escape_ass_text(wrapped_subtitle)}"
                    )
            if index == 0 or timeline[index - 1].scene_id != clip.scene_id:
                location_style = "LocationLeft" if (scene.index % 2 == 1) else "LocationRight"
                location_text = self._escape_ass_text(f"{scene.location}·{scene.time_of_day}".strip("·"))
                end_time = min(clip.end_seconds, clip.start_seconds + 2.6)
                lines.append(
                    f"Dialogue: 1,{self._format_ass_time(clip.start_seconds)},{self._format_ass_time(end_time)},{location_style},,0,0,0,,{location_text}"
                )
            focus_name = self._clean_caption_name(scene, clip.shot_id)
            if focus_name and focus_name not in first_seen_characters:
                first_seen_characters.add(focus_name)
                name_style = "NameRight" if (len(first_seen_characters) % 2 == 1) else "NameLeft"
                end_time = min(clip.end_seconds, clip.start_seconds + 2.8)
                identity_text = self._character_identity_text(project, focus_name)
                caption_text = focus_name if not identity_text else f"{focus_name}｜{identity_text}"
                lines.append(
                    f"Dialogue: 1,{self._format_ass_time(clip.start_seconds)},{self._format_ass_time(end_time)},{name_style},,0,0,0,,{self._escape_ass_text(caption_text)}"
                )
        with open(file_path, "w", encoding="utf-8-sig") as file:
            file.write("\n".join(lines))

    def _clean_caption_name(self, scene, shot_id: str) -> str:
        for shot in scene.shots:
            if shot.shot_id == shot_id:
                return (shot.character_focus or "").strip()
        return ""

    def _wrap_subtitle_text(self, text: str, max_line_length: int = 12) -> str:
        normalized = re.sub(r"\s+", " ", (text or "").replace("\\", " ").strip())
        if not normalized:
            return ""
        segments: list[str] = []
        buffer = ""
        for piece in re.split(r"([，。！？；,.!?])", normalized):
            if not piece:
                continue
            if len(buffer) + len(piece) > max_line_length and buffer:
                segments.append(buffer.strip())
                buffer = piece
            else:
                buffer += piece
        if buffer.strip():
            segments.append(buffer.strip())
        if len(segments) > 2:
            segments = [segments[0], "".join(segments[1:])]
        return r"\N".join(segment for segment in segments[:2] if segment)

    def _offset_subtitle_events(self, events: list[dict], clip_start: float, clip_end: float) -> list[dict]:
        adjusted: list[dict] = []
        for event in events:
            try:
                start = clip_start + float(event.get("start_seconds", 0.0))
                end = clip_start + float(event.get("end_seconds", 0.0))
            except (TypeError, ValueError):
                continue
            start = max(start, clip_start)
            end = min(max(end, start + 0.12), clip_end)
            adjusted.append({**event, "start_seconds": start, "end_seconds": end})
        if not adjusted and clip_end > clip_start:
            return []
        return adjusted

    def _select_dialogue_style(self, scene, shot_id: str) -> str:
        for shot in scene.shots:
            if shot.shot_id != shot_id:
                continue
            if any(token in shot.framing for token in ["特写", "近景"]):
                return "DialogueTop"
            if any(token in shot.viewpoint for token in ["压迫", "俯视"]):
                return "DialogueTop"
            return "DialogueBottom"
        return "DialogueBottom"

    def _character_identity_text(self, project, name: str) -> str:
        for character in getattr(project, "characters", []):
            if getattr(character, "name", "") == name:
                return (getattr(character, "identity", "") or "").strip()
        return ""

    def _format_ass_time(self, total_seconds: float) -> str:
        centiseconds = int(round(total_seconds * 100))
        hours = centiseconds // 360000
        minutes = (centiseconds % 360000) // 6000
        seconds = (centiseconds % 6000) // 100
        cs = centiseconds % 100
        return f"{hours}:{minutes:02d}:{seconds:02d}.{cs:02d}"

    def _escape_ass_text(self, text: str) -> str:
        line_break_placeholder = "__ASS_LINE_BREAK__"
        hard_space_placeholder = "__ASS_HARD_SPACE__"
        escaped = (
            (text or "")
            .replace(r"\N", line_break_placeholder)
            .replace(r"\h", hard_space_placeholder)
            .replace("\\", r"\\")
            .replace("{", r"\{")
            .replace("}", r"\}")
        )
        return escaped.replace(line_break_placeholder, r"\N").replace(hard_space_placeholder, r"\h")

    def _resolution_size(self, resolution: str) -> tuple[int, int]:
        try:
            width_text, height_text = resolution.lower().split("x", 1)
            return max(int(width_text), 1), max(int(height_text), 1)
        except Exception:
            return 1440, 1080

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

    def _recover_video_asset(
        self,
        shot: Shot,
        image_asset: AssetRecord,
        audio_asset: AssetRecord,
        image_dir: str,
        video_dir: str,
        clip_duration: float,
        error_message: str,
    ) -> tuple[AssetRecord, AssetRecord]:
        import logging

        lowered_message = error_message.lower()
        can_try_image_ffmpeg = (
            self.skip_image_generation
            and self.ffmpeg_video_provider is not None
            and "access denied" in lowered_message
        )
        if can_try_image_ffmpeg:
            try:
                recovered_image_asset = self.image_provider.generate(shot, image_dir)
                recovered_video_asset = self.ffmpeg_video_provider.render(
                    shot,
                    recovered_image_asset,
                    audio_asset,
                    video_dir,
                    target_duration=clip_duration,
                )
                recovered_video_asset.metadata["fallback_reason"] = error_message[:500]
                recovered_video_asset.metadata["recovered_via"] = "image+ffmpeg"
                logging.getLogger(__name__).warning(
                    f"镜头 {shot.shot_id} 即梦权限受限，已改用文生图+FFmpeg 兜底：{error_message[:200]}"
                )
                return recovered_video_asset, recovered_image_asset
            except Exception as recovery_exc:
                logging.getLogger(__name__).warning(
                    f"镜头 {shot.shot_id} 文生图+FFmpeg 兜底失败，继续使用黑屏方案：{str(recovery_exc)[:200]}"
                )
        return self._fallback_video(shot, audio_asset, video_dir, clip_duration, error_message), image_asset

    def _build_srt_block(self, index: int, start_seconds: float, end_seconds: float, text: str) -> str:
        return f"{index}\n{self._format_srt_time(start_seconds)} --> {self._format_srt_time(end_seconds)}\n{text}"

    def _format_srt_time(self, total_seconds: float) -> str:
        milliseconds = int(round(total_seconds * 1000))
        hours = milliseconds // 3_600_000
        minutes = (milliseconds % 3_600_000) // 60_000
        seconds = (milliseconds % 60_000) // 1000
        millis = milliseconds % 1000
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{millis:03d}"
