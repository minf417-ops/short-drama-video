from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class CharacterProfile:
    name: str
    traits: list[str] = field(default_factory=list)
    voice_style: str = "neutral"
    gender: str = "unknown"
    age_group: str = "adult"
    identity: str = ""
    speech_style: str = "自然克制"
    appearance: str = ""
    costume: str = ""
    temperament: str = ""


@dataclass
class DialogueLine:
    speaker: str
    text: str


@dataclass
class Shot:
    shot_id: str
    scene_id: str
    index: int
    duration_seconds: float
    visual_description: str
    viewpoint: str
    camera: str
    framing: str
    camera_movement: str
    lens_language: str
    shot_purpose: str
    transition: str
    emotion: str
    expression: str = ""
    body_action: str = ""
    scene_details: str = ""
    character_focus: str = ""
    character_identity: str = ""
    speaker_gender: str = "unknown"
    speaker_age_group: str = "adult"
    delivery_style: str = "自然克制"
    characters: list[str] = field(default_factory=list)
    dialogue: list[DialogueLine] = field(default_factory=list)
    narration: str = ""
    tts_text: str = ""
    image_prompt: str = ""
    video_prompt: str = ""


@dataclass
class Scene:
    scene_id: str
    index: int
    heading: str
    location: str
    time_of_day: str
    mood: str
    summary: str
    environment_details: str = ""
    characters: list[str] = field(default_factory=list)
    character_profiles: dict[str, dict[str, str]] = field(default_factory=dict)
    dialogue: list[DialogueLine] = field(default_factory=list)
    shots: list[Shot] = field(default_factory=list)


@dataclass
class ScriptProject:
    project_id: str
    title: str
    theme: str
    ratio: str
    resolution: str
    script_text: str
    characters: list[CharacterProfile] = field(default_factory=list)
    scenes: list[Scene] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class AssetRecord:
    asset_id: str
    asset_type: str
    scene_id: str
    shot_id: str
    provider: str
    file_path: str
    prompt: str = ""
    duration_seconds: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class TimelineClip:
    clip_id: str
    scene_id: str
    shot_id: str
    start_seconds: float
    end_seconds: float
    visual_asset_id: str
    audio_asset_id: str
    subtitle_text: str
    subtitle_events: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class RenderPlan:
    project_id: str
    output_video_path: str
    subtitle_path: str
    timeline: list[TimelineClip] = field(default_factory=list)
    assets: list[AssetRecord] = field(default_factory=list)
    commands: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def to_dict(value: Any) -> Any:
    if hasattr(value, "__dataclass_fields__"):
        return asdict(value)
    if isinstance(value, list):
        return [to_dict(item) for item in value]
    if isinstance(value, dict):
        return {key: to_dict(item) for key, item in value.items()}
    return value
