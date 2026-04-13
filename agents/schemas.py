from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class UserRequest:
    theme: str
    keywords: List[str]
    audience: str
    styles: List[str]
    writing_tone: str
    episodes: int
    episode_duration: int
    extra_requirements: str

    @property
    def style(self) -> str:
        if self.styles:
            return self.styles[0]
        return "都市悬疑"


@dataclass
class PlanOutline:
    title: str
    opening_hook: str
    core_conflict: str
    reversals: List[str]
    ending_hook: str
    three_act_outline: List[Dict[str, str]]


@dataclass
class ScriptDraft:
    title: str
    content: str
    metadata: Dict[str, str] = field(default_factory=dict)


@dataclass
class ReviewResult:
    approved: bool
    feedback: str
    polished_script: str
    metadata: Dict[str, str] = field(default_factory=dict)


@dataclass
class EditRequest:
    """用户针对已生成剧本的某个场景发起的修改请求。"""
    scene_number: int
    instruction: str
    original_script: str
    title: str
    outline: Optional[Dict[str, Any]] = None
    request_meta: Optional[Dict[str, Any]] = None
    conversation_history: List[Dict[str, str]] = field(default_factory=list)
