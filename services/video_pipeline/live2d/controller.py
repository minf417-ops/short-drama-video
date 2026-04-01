"""Live2D 控制器 — 总调度模块

协调 LipSync 分析 → 角色渲染 → FFmpeg 合成，为每个镜头生成带角色动画的视频片段。

流程：
1. 接收 Shot + Scene + 音频路径
2. 分析音频生成口型同步数据
3. 根据剧本情绪/动作映射 Live2D 表情和动作
4. 渲染角色帧序列（Pillow fallback 或真实 Live2D）
5. 合成背景 + 角色 + 音频 → 最终片段视频
"""

from __future__ import annotations

import json
import logging
import os
import shutil
from dataclasses import dataclass, field

from ..models import AssetRecord, Scene, Shot

from .compositor import Live2DCompositor
from .lipsync import LipSyncAnalyzer, LipSyncData
from .mood_motion_map import ExpressionMotionConfig, MoodMotionMapper
from .renderer import (
    CharacterStyle,
    Live2DWebRenderer,
    PillowCharacterRenderer,
    ROLE_STYLES,
)

logger = logging.getLogger(__name__)


@dataclass
class Live2DModelConfig:
    model_path: str = ""
    character_name: str = ""
    role: str = "female_lead"
    expressions: list[str] = field(default_factory=list)
    motions: dict[str, list[int]] = field(default_factory=dict)


class Live2DController:
    """总调度：口型同步 → 角色渲染 → 背景合成 → 输出视频片段。"""

    def __init__(
        self,
        fps: int = 25,
        width: int = 1080,
        height: int = 1920,
        models_dir: str = "",
        use_live2d_web: bool = False,
    ) -> None:
        self.fps = fps
        self.width = width
        self.height = height
        self.models_dir = models_dir
        self.use_live2d_web = use_live2d_web

        self.lipsync_analyzer = LipSyncAnalyzer(fps=fps, smoothing=0.3)
        self.mood_mapper = MoodMotionMapper()
        self.renderer = PillowCharacterRenderer(width=width, height=height, fps=fps)
        renderer_html_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "..", "static", "live2d", "index.html")
        )
        self.web_renderer = Live2DWebRenderer(
            width=width,
            height=height,
            fps=fps,
            renderer_html_path=renderer_html_path,
        )
        self.compositor = Live2DCompositor()

        self._model_configs: dict[str, Live2DModelConfig] = {}
        self._sample_model_roots: list[str] = []
        self._load_model_configs()

    def render_shot(
        self,
        scene: Scene,
        shot: Shot,
        audio_path: str,
        audio_duration: float,
        background_path: str | None,
        output_dir: str,
        genre: str = "",
    ) -> AssetRecord:
        """为单个镜头生成完整视频片段（角色动画 + 背景 + 音频）。

        返回 AssetRecord 指向最终合成的 .mp4 文件。
        """
        os.makedirs(output_dir, exist_ok=True)
        clip_dir = os.path.join(output_dir, shot.shot_id)
        frames_dir = os.path.join(clip_dir, "frames")
        os.makedirs(frames_dir, exist_ok=True)

        logger.info(f"[Live2D] 开始渲染镜头 {shot.shot_id}")

        # 1. 口型同步分析
        logger.info(f"[Live2D] 分析口型同步: {audio_path}")
        lipsync_data = self.lipsync_analyzer.analyze(audio_path, duration_seconds=audio_duration)

        # 2. 情绪/动作映射
        config = self.mood_mapper.resolve(
            mood=shot.emotion,
            action=shot.body_action,
            genre=genre,
            camera_movement=shot.camera_movement,
        )
        logger.info(
            f"[Live2D] 情绪映射: expression={config.expression}, "
            f"motion={config.motion_group}, body_sway={config.body_sway}"
        )

        # 3. 确定角色样式
        style = self._resolve_character_style(scene, shot)
        selected_model = self._select_model_config(style.role)

        # 4. 渲染角色帧序列
        logger.info(f"[Live2D] 渲染角色帧: {lipsync_data.duration_seconds:.2f}s @ {self.fps}fps")
        render_backend = "pillow_fallback"
        if selected_model and self.web_renderer.available:
            try:
                frame_paths = self.web_renderer.render_frames(
                    output_dir=frames_dir,
                    duration_seconds=lipsync_data.duration_seconds,
                    lipsync=lipsync_data,
                    config=config,
                    model_path=selected_model.model_path,
                    expression_name=config.expression,
                    prefix="frame",
                )
                render_backend = "live2d_web"
            except Exception as exc:
                logger.warning(f"[Live2D] 真实模型渲染失败，回退 Pillow：{exc}")
                frame_paths = self.renderer.render_frames(
                    output_dir=frames_dir,
                    duration_seconds=lipsync_data.duration_seconds,
                    lipsync=lipsync_data,
                    config=config,
                    style=style,
                    prefix="frame",
                )
        else:
            frame_paths = self.renderer.render_frames(
                output_dir=frames_dir,
                duration_seconds=lipsync_data.duration_seconds,
                lipsync=lipsync_data,
                config=config,
                style=style,
                prefix="frame",
            )
        logger.info(f"[Live2D] 渲染完成: {len(frame_paths)} 帧")

        # 5. 确保有背景图
        if not background_path or not os.path.isfile(background_path):
            background_path = os.path.join(clip_dir, "background.png")
            bg_color = self._scene_background_color(scene, genre)
            self.compositor.generate_background(
                output_path=background_path,
                color=bg_color,
                width=self.width,
                height=self.height,
            )

        # 6. 合成最终视频片段
        output_path = os.path.join(output_dir, f"{shot.shot_id}.mp4")
        logger.info(f"[Live2D] 合成视频片段: {output_path}")
        self.compositor.composite_clip(
            background_path=background_path,
            character_frames_dir=frames_dir,
            audio_path=audio_path,
            output_path=output_path,
            duration_seconds=lipsync_data.duration_seconds,
            fps=self.fps,
            width=self.width,
            height=self.height,
        )

        # 7. 清理帧文件（节省磁盘空间）
        try:
            shutil.rmtree(clip_dir, ignore_errors=True)
        except Exception:
            pass

        logger.info(f"[Live2D] 镜头 {shot.shot_id} 渲染完成")

        return AssetRecord(
            asset_id=f"video-{shot.shot_id}",
            asset_type="video_live2d",
            scene_id=shot.scene_id,
            shot_id=shot.shot_id,
            provider="live2d",
            file_path=output_path,
            prompt=shot.video_prompt,
            duration_seconds=lipsync_data.duration_seconds,
            metadata={
                "expression": config.expression,
                "motion_group": config.motion_group,
                "lip_sync_frames": len(lipsync_data.frames),
                "character_role": style.role,
                "render_backend": render_backend,
                "live2d_model_path": selected_model.model_path if selected_model else "",
                "live2d_model_name": selected_model.character_name if selected_model else "",
            },
        )

    def _resolve_character_style(self, scene: Scene, shot: Shot) -> CharacterStyle:
        """根据镜头焦点角色确定渲染样式。"""
        focus = (shot.character_focus or "").strip()
        if not focus and shot.characters:
            focus = shot.characters[0]

        female_markers = ["晚", "夏", "薇", "晴", "瑶", "宁", "雪", "柔", "雅", "娜", "琳", "颖", "婷", "姐", "妈"]
        male_markers = ["川", "默", "泽", "辰", "凯", "邦", "晏", "骁", "霆", "宸", "骏", "峰", "叔", "爷", "哥"]

        if any(m in focus for m in female_markers):
            return ROLE_STYLES["female_lead"]
        if any(m in focus for m in male_markers):
            return ROLE_STYLES["male_lead"]

        scene_chars = [c for c in (scene.characters or []) if isinstance(c, str) and c.strip()]
        if scene_chars:
            if focus == scene_chars[0]:
                return ROLE_STYLES["female_lead"]
            if len(scene_chars) > 1 and focus == scene_chars[1]:
                return ROLE_STYLES["male_lead"]

        if shot.dialogue:
            return ROLE_STYLES.get("female_lead", CharacterStyle())
        return ROLE_STYLES.get("narrator", CharacterStyle())

    def _scene_background_color(self, scene: Scene, genre: str) -> tuple[int, int, int]:
        """根据场景氛围和题材生成背景色调。"""
        mood = (scene.mood or "").lower()
        env = (scene.environment_details or "").lower()

        if "夜" in env or "晚" in (scene.time_of_day or ""):
            base = (15, 15, 30)
        elif "室内" in env:
            base = (35, 30, 28)
        elif "雨" in env or "阴" in env:
            base = (25, 30, 35)
        else:
            base = (30, 35, 45)

        genre_tints: dict[str, tuple[int, int, int]] = {
            "古风": (40, 30, 25), "仙侠": (20, 25, 45), "武侠": (30, 25, 20),
            "科幻": (15, 20, 40), "赛博朋克": (20, 10, 35), "末世": (25, 20, 18),
            "校园": (35, 40, 50), "甜宠": (45, 35, 40),
        }
        for key, tint in genre_tints.items():
            if key in genre:
                return tint
        return base

    def _load_model_configs(self) -> None:
        """加载 Live2D 模型配置（如果有的话）。"""
        if not self.models_dir or not os.path.isdir(self.models_dir):
            return
        config_path = os.path.join(self.models_dir, "model_config.json")
        if os.path.isfile(config_path):
            try:
                with open(config_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                for role, cfg in data.get("models", {}).items():
                    model_path = cfg.get("model_path", "")
                    if model_path and not os.path.isabs(model_path):
                        model_path = os.path.join(self.models_dir, model_path)
                    self._model_configs[role] = Live2DModelConfig(
                        model_path=model_path,
                        character_name=cfg.get("character_name", role),
                        role=role,
                        expressions=cfg.get("expressions", []),
                        motions=cfg.get("motions", {}),
                    )
                logger.info(f"[Live2D] 已加载 {len(self._model_configs)} 个模型配置")
            except Exception as exc:
                logger.warning(f"[Live2D] 模型配置加载失败: {exc}")
        if not self._model_configs:
            self._discover_official_sample_models()

    def _discover_official_sample_models(self) -> None:
        """自动发现本地 Live2D Cubism SDK Samples 中的官方示例模型。"""
        candidate_roots = [
            self.models_dir,
            os.path.join(self.models_dir, "Samples"),
            os.path.join(self.models_dir, "CubismSdkForWeb-5-r.1", "Samples"),
            os.path.join(self.models_dir, "CubismSdkForWeb-4-r.7", "Samples"),
            os.path.join(self.models_dir, "CubismSdkForWeb-4-r.6", "Samples"),
            os.path.join(self.models_dir, "src", "Samples", "Resources"),
            os.path.join(self.models_dir, "Samples", "Resources"),
        ]
        resolved_roots: list[str] = []
        for root in candidate_roots:
            if root and os.path.isdir(root) and root not in resolved_roots:
                resolved_roots.append(root)
        self._sample_model_roots = resolved_roots
        sample_preferences = {
            "female_lead": ["Haru", "Mark", "Natori", "Rice"],
            "male_lead": ["Hiyori", "Mao", "Kei", "Mark"],
            "supporting": ["Mao", "Kei", "Haru"],
            "narrator": ["Mark", "Haru"],
        }
        discovered: dict[str, Live2DModelConfig] = {}
        for role, names in sample_preferences.items():
            for sample_name in names:
                model_path = self._find_sample_model_path(sample_name)
                if model_path:
                    discovered[role] = Live2DModelConfig(
                        model_path=model_path,
                        character_name=sample_name,
                        role=role,
                        expressions=["F01", "F02", "exp_01", "exp_02"],
                        motions={"idle": [0], "tap_body": [0], "idle_group": [0]},
                    )
                    break
        if discovered:
            self._model_configs.update(discovered)
            logger.info(f"[Live2D] 已自动发现 {len(discovered)} 个官方示例模型")

    def _find_sample_model_path(self, sample_name: str) -> str:
        """在 Cubism SDK Samples 目录中查找示例模型 .model3.json。"""
        normalized = sample_name.lower()
        for root in self._sample_model_roots:
            for current_root, _, files in os.walk(root):
                for file_name in files:
                    if not file_name.endswith(".model3.json"):
                        continue
                    file_stem = file_name[:-12].lower()
                    folder_name = os.path.basename(current_root).lower()
                    if normalized in {file_stem, folder_name} or normalized in file_name.lower() or normalized in current_root.lower():
                        return os.path.join(current_root, file_name)
        return ""

    def _select_model_config(self, role: str) -> Live2DModelConfig | None:
        """按角色优先选择已配置或自动发现的官方 Live2D 模型。"""
        if role in self._model_configs:
            return self._model_configs[role]
        if "female_lead" in self._model_configs:
            return self._model_configs["female_lead"]
        for config in self._model_configs.values():
            return config
        return None
