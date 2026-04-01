"""Mood/Motion → Live2D Expression/Motion 映射表

覆盖全部短剧题材：都市、霸总、重生、复仇、甜宠、古风、仙侠、武侠、末世、
年代、穿越、穿书、校园、谍战、权谋、宫斗、职场、商战、科幻、赛博朋克、
无限流、悬疑、惊悚、轻喜剧

Live2D 表情/动作名称遵循 Cubism SDK 通用命名约定。
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ExpressionMotionConfig:
    expression: str = "neutral"
    motion_group: str = "idle"
    motion_index: int = 0
    mouth_form: str = "normal"
    eye_state: str = "open"
    brow_state: str = "normal"
    body_sway: float = 0.0
    head_angle: float = 0.0
    extra: dict = field(default_factory=dict)


# ── 表情映射：中文情绪关键词 → Live2D expression name ──────────────────────
EXPRESSION_MAP: dict[str, str] = {
    # 基础情绪
    "开心": "happy", "高兴": "happy", "甜蜜": "happy", "欢喜": "happy",
    "愤怒": "angry", "暴怒": "angry", "怒火": "angry", "盛怒": "angry",
    "悲伤": "sad", "伤心": "sad", "哀痛": "sad", "心碎": "sad",
    "惊讶": "surprised", "震惊": "surprised", "错愕": "surprised", "意外": "surprised",
    "恐惧": "scared", "害怕": "scared", "惊恐": "scared", "畏惧": "scared",
    "厌恶": "disgust", "鄙夷": "disgust", "嫌弃": "disgust",
    "平静": "neutral", "淡然": "neutral", "从容": "neutral",
    # 复合情绪
    "隐忍": "suppressed", "压抑": "suppressed", "克制": "suppressed",
    "冷酷": "cold", "冰冷": "cold", "冷漠": "cold", "冷峻": "cold",
    "得意": "smug", "嘲讽": "smug", "轻蔑": "smug", "傲慢": "smug",
    "疑惑": "confused", "困惑": "confused", "迷茫": "confused", "不解": "confused",
    "坚定": "determined", "决心": "determined", "狠劲": "determined",
    "温柔": "gentle", "柔情": "gentle", "宠溺": "gentle", "慈爱": "gentle",
    "紧张": "nervous", "焦虑": "nervous", "不安": "nervous",
    "害羞": "shy", "羞涩": "shy", "脸红": "shy",
    "邪魅": "evil", "阴险": "evil", "狡诈": "evil",
    "痛苦": "pain", "挣扎": "pain", "崩溃": "pain",
    "思考": "thinking", "沉思": "thinking",
    # 场景氛围
    "高压对峙": "cold", "剑拔弩张": "angry", "暗流涌动": "suppressed",
    "感情升温": "gentle", "甜蜜互动": "happy", "误会激化": "angry",
    "真相大白": "surprised", "绝地反击": "determined", "生死存亡": "scared",
    "计谋得逞": "smug", "阴谋败露": "surprised", "悬疑紧张": "nervous",
    "喜剧冲突": "happy", "深情告白": "gentle", "悲情离别": "sad",
}

# ── 动作映射：中文动作/运镜关键词 → Live2D motion group + index ─────────────
MOTION_MAP: dict[str, tuple[str, int]] = {
    # 基础动作
    "站立": ("idle", 0), "静止": ("idle", 0), "待机": ("idle", 0),
    "说话": ("talk", 0), "对话": ("talk", 0), "开口": ("talk", 0),
    "点头": ("nod", 0), "应答": ("nod", 0),
    "摇头": ("shake", 0), "否定": ("shake", 0), "拒绝": ("shake", 0),
    "转身": ("turn", 0), "回头": ("turn", 0),
    "鞠躬": ("bow", 0), "行礼": ("bow", 0),
    "挥手": ("wave", 0), "招手": ("wave", 0),
    "叹气": ("sigh", 0), "深呼吸": ("sigh", 0),
    # 情绪动作
    "大笑": ("laugh", 0), "微笑": ("smile", 0),
    "哭泣": ("cry", 0), "流泪": ("cry", 0),
    "拍桌": ("slam", 0), "摔东西": ("slam", 0),
    "抱拳": ("fist", 0), "握拳": ("fist", 0),
    "拥抱": ("hug", 0), "相拥": ("hug", 0),
    "推开": ("push", 0), "推搡": ("push", 0),
    "后退": ("step_back", 0), "退后": ("step_back", 0),
    "上前": ("step_forward", 0), "逼近": ("step_forward", 0),
    "跪下": ("kneel", 0), "下跪": ("kneel", 0),
    # 战斗/武侠/仙侠
    "拔剑": ("draw_sword", 0), "出剑": ("sword_attack", 0),
    "运功": ("channel", 0), "施法": ("cast", 0),
    "飞翔": ("fly", 0), "御剑飞行": ("fly", 0),
    "打斗": ("fight", 0), "交手": ("fight", 0),
    # 运镜映射
    "缓慢推进": ("idle", 0), "横向平移跟随": ("walk", 0),
    "环绕": ("turn", 0), "变焦": ("idle", 0),
    "手持": ("idle", 1), "快速闪切": ("idle", 0),
}

# ── 题材 → 默认情绪/表情 预设 ─────────────────────────────────────────────
GENRE_DEFAULTS: dict[str, ExpressionMotionConfig] = {
    "都市": ExpressionMotionConfig(expression="neutral", motion_group="idle", body_sway=0.3),
    "霸总": ExpressionMotionConfig(expression="cold", motion_group="idle", body_sway=0.2, head_angle=-5),
    "重生": ExpressionMotionConfig(expression="determined", motion_group="idle", body_sway=0.4),
    "复仇": ExpressionMotionConfig(expression="cold", motion_group="idle", body_sway=0.2),
    "甜宠": ExpressionMotionConfig(expression="happy", motion_group="idle", body_sway=0.5),
    "古风": ExpressionMotionConfig(expression="gentle", motion_group="idle", body_sway=0.6, head_angle=3),
    "仙侠": ExpressionMotionConfig(expression="neutral", motion_group="idle", body_sway=0.8),
    "武侠": ExpressionMotionConfig(expression="determined", motion_group="idle", body_sway=0.5),
    "末世": ExpressionMotionConfig(expression="suppressed", motion_group="idle", body_sway=0.3),
    "年代": ExpressionMotionConfig(expression="gentle", motion_group="idle", body_sway=0.4),
    "穿越": ExpressionMotionConfig(expression="surprised", motion_group="idle", body_sway=0.5),
    "穿书": ExpressionMotionConfig(expression="confused", motion_group="idle", body_sway=0.5),
    "校园": ExpressionMotionConfig(expression="happy", motion_group="idle", body_sway=0.6),
    "谍战": ExpressionMotionConfig(expression="suppressed", motion_group="idle", body_sway=0.2),
    "权谋": ExpressionMotionConfig(expression="cold", motion_group="idle", body_sway=0.2),
    "宫斗": ExpressionMotionConfig(expression="smug", motion_group="idle", body_sway=0.3),
    "职场": ExpressionMotionConfig(expression="neutral", motion_group="idle", body_sway=0.3),
    "商战": ExpressionMotionConfig(expression="determined", motion_group="idle", body_sway=0.2),
    "科幻": ExpressionMotionConfig(expression="neutral", motion_group="idle", body_sway=0.4),
    "赛博朋克": ExpressionMotionConfig(expression="cold", motion_group="idle", body_sway=0.3),
    "无限流": ExpressionMotionConfig(expression="nervous", motion_group="idle", body_sway=0.5),
    "悬疑": ExpressionMotionConfig(expression="nervous", motion_group="idle", body_sway=0.2),
    "惊悚": ExpressionMotionConfig(expression="scared", motion_group="idle", body_sway=0.4),
    "轻喜剧": ExpressionMotionConfig(expression="happy", motion_group="idle", body_sway=0.6),
}


class MoodMotionMapper:
    """根据剧本情绪/动作/题材文本，输出 Live2D 表情和动作参数。"""

    def resolve(
        self,
        mood: str = "",
        action: str = "",
        genre: str = "",
        camera_movement: str = "",
    ) -> ExpressionMotionConfig:
        base = self._genre_default(genre)
        expression = self._resolve_expression(mood) or base.expression
        motion_group, motion_index = self._resolve_motion(action, camera_movement)
        if motion_group == "idle" and motion_index == 0:
            motion_group = base.motion_group
            motion_index = base.extra.get("motion_index", 0)
        return ExpressionMotionConfig(
            expression=expression,
            motion_group=motion_group,
            motion_index=motion_index,
            mouth_form=self._mouth_form(expression),
            eye_state=self._eye_state(expression),
            brow_state=self._brow_state(expression),
            body_sway=base.body_sway,
            head_angle=base.head_angle,
        )

    def _genre_default(self, genre: str) -> ExpressionMotionConfig:
        if not genre:
            return ExpressionMotionConfig()
        for key, config in GENRE_DEFAULTS.items():
            if key in genre:
                return config
        return ExpressionMotionConfig()

    def _resolve_expression(self, mood: str) -> str:
        if not mood:
            return ""
        for keyword, expression in EXPRESSION_MAP.items():
            if keyword in mood:
                return expression
        return ""

    def _resolve_motion(self, action: str, camera_movement: str = "") -> tuple[str, int]:
        for text in [action, camera_movement]:
            if not text:
                continue
            for keyword, (group, index) in MOTION_MAP.items():
                if keyword in text:
                    return group, index
        return "idle", 0

    def _mouth_form(self, expression: str) -> str:
        mapping = {
            "happy": "smile", "sad": "frown", "angry": "tight",
            "surprised": "open_wide", "scared": "open_wide",
            "smug": "smirk", "gentle": "smile", "shy": "small",
            "pain": "open_wide", "evil": "smirk",
        }
        return mapping.get(expression, "normal")

    def _eye_state(self, expression: str) -> str:
        mapping = {
            "happy": "happy_squint", "sad": "droopy", "angry": "glare",
            "surprised": "wide", "scared": "wide", "cold": "narrow",
            "suppressed": "narrow", "smug": "half_closed",
            "gentle": "soft", "shy": "averted", "evil": "narrow",
            "nervous": "darting", "thinking": "up_look",
        }
        return mapping.get(expression, "open")

    def _brow_state(self, expression: str) -> str:
        mapping = {
            "happy": "raised", "sad": "inner_up", "angry": "furrowed",
            "surprised": "raised_high", "scared": "raised_high",
            "cold": "flat", "determined": "furrowed", "smug": "one_raised",
            "confused": "one_raised", "gentle": "relaxed",
        }
        return mapping.get(expression, "normal")
