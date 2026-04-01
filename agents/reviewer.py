import re

from .schemas import UserRequest, ScriptDraft, ReviewResult
from services.llm_service import LLMService


class ReviewerAgent:
    def __init__(self, llm: LLMService) -> None:
        self.llm = llm

    def run(self, request: UserRequest, draft: ScriptDraft) -> ReviewResult:
        local_issues = self._validate_locally(request, draft.content)
        if not local_issues:
            return ReviewResult(
                approved=True,
                feedback="快速审校通过：结构、主题、关键词与风格约束满足要求，保留当前稿件。",
                polished_script=draft.content,
                metadata={"review_mode": "local_fastpath"},
            )
        system_prompt = "你是短剧审校Agent。你要检查逻辑、节奏、格式、风格统一性，并过滤违规内容。不要输出分析过程、解释或Markdown，只输出JSON。不要在JSON中包含剧本原文。"
        style_text = "、".join(request.styles)
        user_prompt = f"""
请审校以下短剧剧本并输出JSON：
需求风格：{style_text}
文字风格：{request.writing_tone}
目标受众：{request.audience}
已知待重点检查问题：{'; '.join(local_issues)}
剧本内容：
{draft.content}

JSON字段（只输出以下两个字段，不要输出剧本原文）：
- approved: bool（是否通过审校）
- feedback: string（简要修订建议，100字以内）
"""
        try:
            data = self.llm.complete_json(
                system_prompt,
                user_prompt,
                required_fields=["approved", "feedback"],
                temperature=0.2,
                max_tokens=2000,
            )
        except Exception as exc:
            serious_issues = {"缺少场景结构", "缺少镜头结构", "篇幅过短", "内容疑似截断"}
            has_serious = any(issue in serious_issues for issue in local_issues)
            return ReviewResult(
                approved=not has_serious,
                feedback=f"审校阶段降级处理：{exc}",
                polished_script=draft.content,
                metadata={
                    "review_mode": "fallback",
                    "tail_repair_only": "true" if self._should_tail_repair(local_issues) else "false",
                    "local_issues": "; ".join(local_issues),
                },
            )
        approved = bool(data.get("approved", False))
        feedback = data.get("feedback", "审校完成")
        return ReviewResult(
            approved=approved,
            feedback=feedback,
            polished_script=draft.content,
            metadata={
                "review_mode": "api",
                "tail_repair_only": "true" if self._should_tail_repair(local_issues) else "false",
            },
        )

    def _validate_locally(self, request: UserRequest, content: str) -> list[str]:
        issues: list[str] = []
        normalized = self._normalize_text(content)
        target_scene_count = self._target_scene_count(request.episode_duration, request.episodes)
        if "场景" not in content:
            issues.append("缺少场景结构")
        if "镜头" not in content:
            issues.append("缺少镜头结构")
        if "对白" not in content:
            issues.append("缺少对白结构")
        if len(content.strip()) < 280:
            issues.append("篇幅过短")
        theme = self._normalize_text(request.theme)
        if theme and theme not in normalized:
            segments = [theme[index:index + 4] for index in range(0, max(len(theme) - 3, 1))]
            if not any(segment and segment in normalized for segment in segments):
                issues.append("主题覆盖不足")
        keyword_hits = 0
        for keyword in request.keywords[:4]:
            normalized_keyword = self._normalize_text(keyword)
            if normalized_keyword and normalized_keyword in normalized:
                keyword_hits += 1
        if request.keywords and keyword_hits < min(2, len(request.keywords[:4])):
            issues.append("关键词覆盖不足")
        normalized_styles = set(request.styles)
        if "甜宠" not in normalized_styles and any(word in content for word in ["宝贝", "亲爱的", "吻上", "宠溺"]):
            issues.append("风格口吻漂移")
        if self._looks_truncated(content):
            issues.append("内容疑似截断")
        if not self._has_complete_final_scene(content, target_scene_count):
            issues.append("结尾场景不完整")
        return issues

    def _normalize_text(self, text: str) -> str:
        return re.sub(r"[\W_]+", "", text).lower()

    def _target_scene_count(self, episode_duration: int, episodes: int) -> int:
        if episode_duration <= 45:
            per_episode = 3
        elif episode_duration <= 90:
            per_episode = 4
        else:
            per_episode = 5
        return per_episode * max(episodes, 1)

    def _looks_truncated(self, content: str) -> bool:
        stripped = content.strip()
        if len(stripped) < 700:
            return True
        if any(stripped.endswith(token) for token in ("：", "（", "【", "“", "、", "-", "——")):
            return True
        if re.search(r"[\d一二三四五六七八九十]+\s*$", stripped):
            return True
        if re.search(r"(镜头|对白|动作描述|分镜提示)\s*[:：]?\s*$", stripped):
            return True
        return False

    def _has_complete_final_scene(self, content: str, scene_count: int) -> bool:
        patterns = [
            f"场景{scene_count}",
            f"【场景{scene_count}】",
            f"场景号：{scene_count}",
        ]
        final_index = max((content.rfind(pattern) for pattern in patterns), default=-1)
        if final_index == -1:
            return False
        tail = content[final_index:]
        return all(marker in tail for marker in ["动作描述", "对白", "分镜提示"]) and tail.strip().endswith(("。", "！", "？"))

    def _should_tail_repair(self, issues: list[str]) -> bool:
        if not issues:
            return False
        repairable_issues = {"内容疑似截断", "结尾场景不完整", "场景数量不足", "场景数量超出"}
        return all(issue in repairable_issues for issue in issues)
