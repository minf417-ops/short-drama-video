"""Live2D 角色渲染器

提供两种渲染模式：
1. PillowCharacterRenderer（默认）- 纯 Python 绘制动画角色帧序列，无外部依赖
2. Live2DWebRenderer（可选）- 通过 Playwright 驱动 Web 页面加载真实 Live2D 模型

两者输出相同：透明通道 PNG 帧序列 → FFmpeg 编码为带 Alpha 的视频。
"""

from __future__ import annotations

import math
import os
import struct
import subprocess
import shutil
import wave
from dataclasses import dataclass
from urllib.parse import quote

from .lipsync import LipSyncData
from .mood_motion_map import ExpressionMotionConfig

try:
    from PIL import Image, ImageDraw, ImageFont, ImageFilter
except ImportError:
    Image = ImageDraw = ImageFont = ImageFilter = None

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sync_playwright = None


@dataclass
class CharacterStyle:
    role: str = "female_lead"
    hair_color: tuple = (30, 30, 50)
    skin_color: tuple = (255, 228, 210)
    eye_color: tuple = (80, 130, 200)
    outfit_color: tuple = (60, 60, 80)
    accent_color: tuple = (200, 80, 100)
    hair_style: str = "long"


ROLE_STYLES: dict[str, CharacterStyle] = {
    "female_lead": CharacterStyle(
        role="female_lead",
        hair_color=(25, 20, 45),
        skin_color=(255, 232, 215),
        eye_color=(100, 140, 220),
        outfit_color=(180, 50, 70),
        accent_color=(220, 180, 80),
        hair_style="long",
    ),
    "male_lead": CharacterStyle(
        role="male_lead",
        hair_color=(30, 28, 35),
        skin_color=(240, 215, 195),
        eye_color=(60, 80, 120),
        outfit_color=(40, 45, 60),
        accent_color=(80, 100, 140),
        hair_style="short",
    ),
    "supporting": CharacterStyle(
        role="supporting",
        hair_color=(80, 50, 30),
        skin_color=(248, 225, 205),
        eye_color=(90, 110, 80),
        outfit_color=(100, 90, 80),
        accent_color=(150, 130, 100),
        hair_style="medium",
    ),
    "narrator": CharacterStyle(
        role="narrator",
        hair_color=(50, 50, 60),
        skin_color=(245, 225, 210),
        eye_color=(70, 70, 90),
        outfit_color=(50, 50, 60),
        accent_color=(100, 100, 120),
        hair_style="short",
    ),
}


class PillowCharacterRenderer:
    """用 Pillow 绘制二次元风格动画角色帧，支持表情/口型/体态动画。"""

    def __init__(self, width: int = 1080, height: int = 1920, fps: int = 25) -> None:
        if Image is None:
            raise RuntimeError("Pillow 未安装，请 pip install Pillow")
        self.width = width
        self.height = height
        self.fps = fps

    def render_frames(
        self,
        output_dir: str,
        duration_seconds: float,
        lipsync: LipSyncData,
        config: ExpressionMotionConfig,
        style: CharacterStyle | None = None,
        prefix: str = "frame",
    ) -> list[str]:
        os.makedirs(output_dir, exist_ok=True)
        if style is None:
            style = ROLE_STYLES.get("female_lead", CharacterStyle())

        total_frames = max(int(duration_seconds * self.fps), 1)
        frame_paths: list[str] = []

        for i in range(total_frames):
            t = i / self.fps
            mouth_open = lipsync.mouth_value_at(t) if lipsync else 0.0
            frame = self._draw_frame(i, total_frames, t, mouth_open, config, style)
            path = os.path.join(output_dir, f"{prefix}_{i:05d}.png")
            frame.save(path, "PNG")
            frame_paths.append(path)

        return frame_paths

    def render_to_video(
        self,
        output_path: str,
        duration_seconds: float,
        lipsync: LipSyncData,
        config: ExpressionMotionConfig,
        style: CharacterStyle | None = None,
    ) -> str:
        ffmpeg = shutil.which(os.environ.get("FFMPEG_BINARY", "ffmpeg")) or "ffmpeg"
        if not shutil.which(ffmpeg):
            raise RuntimeError("FFmpeg 未安装")

        if style is None:
            style = ROLE_STYLES.get("female_lead", CharacterStyle())

        total_frames = max(int(duration_seconds * self.fps), 1)
        command = [
            ffmpeg, "-y",
            "-f", "rawvideo",
            "-pixel_format", "rgba",
            "-video_size", f"{self.width}x{self.height}",
            "-framerate", str(self.fps),
            "-i", "pipe:0",
            "-c:v", "png",
            "-pix_fmt", "rgba",
            "-frames:v", str(total_frames),
            output_path,
        ]

        process = subprocess.Popen(
            command, stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        )

        for i in range(total_frames):
            t = i / self.fps
            mouth_open = lipsync.mouth_value_at(t) if lipsync else 0.0
            frame = self._draw_frame(i, total_frames, t, mouth_open, config, style)
            process.stdin.write(frame.tobytes())

        process.stdin.close()
        process.wait()

        if process.returncode != 0:
            stderr = process.stderr.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"FFmpeg 角色视频编码失败：{stderr}")

        return output_path

    def _draw_frame(
        self,
        frame_idx: int,
        total_frames: int,
        t: float,
        mouth_open: float,
        config: ExpressionMotionConfig,
        style: CharacterStyle,
    ) -> Image.Image:
        img = Image.new("RGBA", (self.width, self.height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        progress = frame_idx / max(total_frames - 1, 1)
        sway = math.sin(t * 1.8) * config.body_sway * 12
        breath = math.sin(t * 2.5) * 3

        cx = self.width // 2 + int(sway)
        body_top = int(self.height * 0.38)

        self._draw_body(draw, cx, body_top, style, breath, config)
        self._draw_neck(draw, cx, body_top, style)

        head_cx = cx + int(math.sin(t * 1.2) * config.body_sway * 5)
        head_cy = body_top - 80
        head_angle_rad = math.radians(config.head_angle) + math.sin(t * 0.8) * 0.02
        self._draw_hair_back(draw, head_cx, head_cy, style)
        self._draw_head(draw, head_cx, head_cy, style)
        self._draw_face(draw, head_cx, head_cy, mouth_open, config, style, t)
        self._draw_hair_front(draw, head_cx, head_cy, style)

        return img

    def _draw_body(self, draw: ImageDraw.Draw, cx: int, top: int,
                   style: CharacterStyle, breath: float, config: ExpressionMotionConfig) -> None:
        outfit = style.outfit_color
        accent = style.accent_color
        w = 220
        h = int(self.height * 0.65)
        body_points = [
            (cx - w // 2 - 20, top + h),
            (cx - w // 2 + 10, top + 40),
            (cx - 60, top),
            (cx + 60, top),
            (cx + w // 2 - 10, top + 40),
            (cx + w // 2 + 20, top + h),
        ]
        draw.polygon(body_points, fill=(*outfit, 245))

        shoulder_y = top + 30
        for side in [-1, 1]:
            sx = cx + side * 80
            draw.ellipse(
                [sx - 35, shoulder_y - 15, sx + 35, shoulder_y + 25],
                fill=(*outfit, 240),
            )

        collar_points = [
            (cx - 50, top), (cx, top + 50 + int(breath)),
            (cx + 50, top),
        ]
        draw.polygon(collar_points, fill=(*accent, 200))

    def _draw_neck(self, draw: ImageDraw.Draw, cx: int, top: int, style: CharacterStyle) -> None:
        neck_w = 36
        neck_h = 40
        draw.rectangle(
            [cx - neck_w // 2, top - neck_h, cx + neck_w // 2, top + 5],
            fill=(*style.skin_color, 255),
        )

    def _draw_head(self, draw: ImageDraw.Draw, cx: int, cy: int, style: CharacterStyle) -> None:
        head_w, head_h = 140, 160
        draw.ellipse(
            [cx - head_w, cy - head_h, cx + head_w, cy + head_h],
            fill=(*style.skin_color, 255),
        )
        chin_points = [
            (cx - 80, cy + 60),
            (cx, cy + head_h + 20),
            (cx + 80, cy + 60),
        ]
        draw.polygon(chin_points, fill=(*style.skin_color, 255))

    def _draw_face(self, draw: ImageDraw.Draw, cx: int, cy: int,
                   mouth_open: float, config: ExpressionMotionConfig,
                   style: CharacterStyle, t: float) -> None:
        eye_y = cy - 15
        eye_spacing = 55
        self._draw_eye(draw, cx - eye_spacing, eye_y, style, config, t, is_left=True)
        self._draw_eye(draw, cx + eye_spacing, eye_y, style, config, t, is_left=False)

        brow_y = eye_y - 40
        brow_offset = 0
        if config.brow_state == "furrowed":
            brow_offset = 5
        elif config.brow_state == "raised" or config.brow_state == "raised_high":
            brow_offset = -8
        for bx in [cx - eye_spacing, cx + eye_spacing]:
            draw.line(
                [(bx - 25, brow_y + brow_offset), (bx + 25, brow_y - brow_offset // 2)],
                fill=(*style.hair_color, 200), width=4,
            )

        nose_y = cy + 15
        draw.line(
            [(cx - 3, nose_y - 10), (cx, nose_y + 5), (cx + 3, nose_y - 10)],
            fill=(*self._darken(style.skin_color, 30), 120), width=2,
        )

        mouth_y = cy + 45
        self._draw_mouth(draw, cx, mouth_y, mouth_open, config, style)

        if config.expression in ("shy", "happy"):
            blush_alpha = 60 if config.expression == "happy" else 90
            for bx in [cx - 70, cx + 70]:
                draw.ellipse(
                    [bx - 22, eye_y + 15, bx + 22, eye_y + 35],
                    fill=(255, 150, 150, blush_alpha),
                )

    def _draw_eye(self, draw: ImageDraw.Draw, ex: int, ey: int,
                  style: CharacterStyle, config: ExpressionMotionConfig,
                  t: float, is_left: bool) -> None:
        eye_state = config.eye_state

        blink = abs(math.sin(t * 3.0)) < 0.05
        if blink:
            draw.line([(ex - 18, ey), (ex + 18, ey)], fill=(*style.hair_color, 220), width=3)
            return

        if eye_state == "narrow" or eye_state == "half_closed":
            h = 10
        elif eye_state == "wide":
            h = 28
        elif eye_state == "happy_squint":
            draw.arc([ex - 18, ey - 8, ex + 18, ey + 12], 0, 180,
                     fill=(*style.hair_color, 220), width=3)
            return
        else:
            h = 20

        draw.ellipse(
            [ex - 20, ey - h, ex + 20, ey + h],
            fill=(255, 255, 255, 240),
            outline=(*style.hair_color, 200),
            width=2,
        )

        iris_r = min(h - 2, 12)
        draw.ellipse(
            [ex - iris_r, ey - iris_r, ex + iris_r, ey + iris_r],
            fill=(*style.eye_color, 240),
        )

        pupil_r = max(iris_r // 2, 3)
        draw.ellipse(
            [ex - pupil_r, ey - pupil_r, ex + pupil_r, ey + pupil_r],
            fill=(10, 10, 20, 250),
        )

        hl_x = ex - 5
        hl_y = ey - 5
        draw.ellipse(
            [hl_x - 3, hl_y - 3, hl_x + 3, hl_y + 3],
            fill=(255, 255, 255, 220),
        )

    def _draw_mouth(self, draw: ImageDraw.Draw, cx: int, my: int,
                    mouth_open: float, config: ExpressionMotionConfig,
                    style: CharacterStyle) -> None:
        mouth_form = config.mouth_form
        mouth_w = 30
        opening = max(int(mouth_open * 22), 0)

        if mouth_form == "smile" or config.expression in ("happy", "gentle"):
            draw.arc(
                [cx - mouth_w, my - 8, cx + mouth_w, my + 12 + opening],
                10, 170,
                fill=(200, 80, 80, 220), width=3,
            )
            if opening > 3:
                draw.ellipse(
                    [cx - mouth_w + 5, my, cx + mouth_w - 5, my + opening],
                    fill=(180, 50, 60, 200),
                )
        elif mouth_form == "frown" or config.expression in ("sad", "pain"):
            draw.arc(
                [cx - mouth_w, my - opening, cx + mouth_w, my + 10],
                190, 350,
                fill=(180, 80, 80, 200), width=3,
            )
        elif mouth_form == "tight" or config.expression in ("angry", "cold"):
            draw.line(
                [(cx - mouth_w, my), (cx + mouth_w, my)],
                fill=(180, 70, 70, 220), width=3,
            )
            if opening > 5:
                draw.rectangle(
                    [cx - mouth_w + 5, my, cx + mouth_w - 5, my + opening // 2],
                    fill=(160, 40, 50, 200),
                )
        elif mouth_form == "open_wide" or config.expression in ("surprised", "scared"):
            draw.ellipse(
                [cx - 15 - opening // 2, my - 8 - opening // 2,
                 cx + 15 + opening // 2, my + 8 + opening],
                fill=(180, 50, 60, 200),
                outline=(160, 40, 50, 180), width=2,
            )
        elif mouth_form == "smirk" or config.expression in ("smug", "evil"):
            draw.arc(
                [cx - mouth_w - 5, my - 5, cx + mouth_w + 5, my + 10 + opening // 2],
                10, 140,
                fill=(180, 70, 80, 220), width=3,
            )
        else:
            if opening < 3:
                draw.line(
                    [(cx - mouth_w + 5, my), (cx + mouth_w - 5, my)],
                    fill=(200, 100, 100, 180), width=2,
                )
            else:
                draw.ellipse(
                    [cx - 12, my - 2, cx + 12, my + opening],
                    fill=(180, 50, 60, 200),
                )

    def _draw_hair_back(self, draw: ImageDraw.Draw, cx: int, cy: int,
                        style: CharacterStyle) -> None:
        hc = style.hair_color
        if style.hair_style == "long":
            for offset in [-90, -60, 0, 60, 90]:
                x = cx + offset
                draw.ellipse(
                    [x - 70, cy - 170, x + 70, cy + 280],
                    fill=(*hc, 200),
                )
        elif style.hair_style == "medium":
            draw.ellipse(
                [cx - 160, cy - 180, cx + 160, cy + 100],
                fill=(*hc, 210),
            )
        else:
            draw.ellipse(
                [cx - 155, cy - 175, cx + 155, cy + 20],
                fill=(*hc, 210),
            )

    def _draw_hair_front(self, draw: ImageDraw.Draw, cx: int, cy: int,
                         style: CharacterStyle) -> None:
        hc = style.hair_color
        draw.ellipse(
            [cx - 150, cy - 180, cx + 150, cy - 60],
            fill=(*hc, 240),
        )

        bang_points = [
            [(cx - 80, cy - 170), (cx - 100, cy - 30), (cx - 60, cy - 50)],
            [(cx - 30, cy - 175), (cx - 50, cy - 20), (cx - 10, cy - 40)],
            [(cx + 20, cy - 175), (cx + 0, cy - 25), (cx + 40, cy - 45)],
            [(cx + 70, cy - 170), (cx + 50, cy - 35), (cx + 90, cy - 50)],
        ]
        for pts in bang_points:
            draw.polygon(pts, fill=(*hc, 235))

        if style.hair_style == "long":
            for side in [-1, 1]:
                sx = cx + side * 130
                pts = [
                    (sx, cy - 80),
                    (sx + side * 30, cy + 200),
                    (sx - side * 10, cy + 180),
                    (sx - side * 20, cy - 60),
                ]
                draw.polygon(pts, fill=(*hc, 220))

    def _darken(self, color: tuple, amount: int) -> tuple:
        return tuple(max(0, c - amount) for c in color[:3])


class Live2DWebRenderer:
    """通过本地 Web 页面加载官方 Live2D 示例模型并截图输出透明帧。"""

    def __init__(self, width: int = 1080, height: int = 1920, fps: int = 25, renderer_html_path: str = "") -> None:
        self.width = width
        self.height = height
        self.fps = fps
        self.renderer_html_path = renderer_html_path

    @property
    def available(self) -> bool:
        return sync_playwright is not None and bool(self.renderer_html_path) and os.path.isfile(self.renderer_html_path)

    def render_frames(
        self,
        output_dir: str,
        duration_seconds: float,
        lipsync: LipSyncData,
        config: ExpressionMotionConfig,
        model_path: str,
        motion_index: int = 0,
        expression_name: str | None = None,
        prefix: str = "frame",
    ) -> list[str]:
        if sync_playwright is None:
            raise RuntimeError("Playwright 未安装，无法使用真实 Live2D Web 渲染")
        if not model_path or not os.path.isfile(model_path):
            raise RuntimeError("未找到 Live2D 模型文件，无法使用真实模型渲染")
        if not self.renderer_html_path or not os.path.isfile(self.renderer_html_path):
            raise RuntimeError("未找到 Live2D Web 渲染页面")

        os.makedirs(output_dir, exist_ok=True)
        total_frames = max(int(duration_seconds * self.fps), 1)
        frame_paths: list[str] = []

        renderer_url = self._to_file_url(self.renderer_html_path)
        model_url = self._to_file_url(model_path)

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": self.width, "height": self.height}, device_scale_factor=1)
            page.goto(renderer_url, wait_until="domcontentloaded")
            page.wait_for_timeout(1500)
            load_ok = page.evaluate(
                """
                async ({ modelUrl, expressionName, motionIndex }) => {
                    if (typeof loadModel !== 'function') {
                        return { ok: false, error: 'loadModel not found' };
                    }
                    const ok = await loadModel(modelUrl);
                    if (!ok) {
                        return { ok: false, error: 'model load failed' };
                    }
                    if (expressionName && typeof setExpression === 'function') {
                        try { setExpression(expressionName); } catch (e) {}
                    }
                    if (typeof playMotion === 'function') {
                        try { playMotion('idle', motionIndex || 0); } catch (e) {}
                    }
                    return { ok: true };
                }
                """,
                {"modelUrl": model_url, "expressionName": expression_name or config.expression, "motionIndex": motion_index},
            )
            if not load_ok.get("ok"):
                browser.close()
                raise RuntimeError(f"Live2D Web 模型加载失败：{load_ok.get('error', 'unknown error')}")

            for i in range(total_frames):
                t = i / self.fps
                mouth_open = lipsync.mouth_value_at(t) if lipsync else 0.0
                page.evaluate(
                    """
                    ({ expressionName, mouthOpen, motionIndex }) => {
                        if (typeof setExpression === 'function' && expressionName) {
                            try { setExpression(expressionName); } catch (e) {}
                        }
                        if (typeof setLipSync === 'function') {
                            try { setLipSync(mouthOpen); } catch (e) {}
                        }
                        if (typeof playMotion === 'function' && motionIndex === 0) {
                            try { playMotion('idle', 0); } catch (e) {}
                        }
                    }
                    """,
                    {"expressionName": expression_name or config.expression, "mouthOpen": mouth_open, "motionIndex": motion_index},
                )
                path = os.path.join(output_dir, f"{prefix}_{i:05d}.png")
                page.screenshot(path=path, omit_background=True)
                frame_paths.append(path)

            browser.close()
        return frame_paths

    def _to_file_url(self, path: str) -> str:
        normalized = os.path.abspath(path).replace("\\", "/")
        return f"file:///{quote(normalized, safe='/:.-_')}"
