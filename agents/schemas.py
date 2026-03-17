from dataclasses import dataclass, field
from typing import List, Dict


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
