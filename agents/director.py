import time
from typing import Any, Callable, Dict, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from .planner import PlannerAgent
from .writer import WriterAgent
from .reviewer import ReviewerAgent
from .schemas import PlanOutline, ReviewResult, ScriptDraft, UserRequest
from services.llm_service import LLMService


class GraphState(TypedDict, total=False):
    request: UserRequest
    outline: PlanOutline
    draft: ScriptDraft
    review: ReviewResult
    final_script: str
    logs: list[str]
    revision_count: int
    latest_feedback: str


class DirectorAgent:
    def __init__(self, llm: LLMService) -> None:
        self.llm = llm
        self.planner = PlannerAgent(llm)
        self.writer = WriterAgent(llm)
        self.reviewer = ReviewerAgent(llm)
        self._runtime_logger: Optional[Callable[[str], None]] = None
        self.graph = self._build_graph()

    def _append_log(self, state: GraphState, message: str) -> GraphState:
        logs = list(state.get("logs", []))
        logs.append(message)
        state["logs"] = logs
        if self._runtime_logger:
            self._runtime_logger(message)
        return state

    def _planner_node(self, state: GraphState) -> GraphState:
        request = state["request"]
        state = self._append_log(state, "Planner：生成大纲、冲突点与反转设计")
        started_at = time.time()
        outline = self.planner.run(request)
        state["outline"] = outline
        if "开场3秒内抛出与" in outline.opening_hook:
            state = self._append_log(state, "Planner：API生成大纲失败，已切换为结构化兜底大纲")
        elapsed = time.time() - started_at
        state = self._append_log(state, f"Planner：完成，剧本标题暂定为《{outline.title}》 | 耗时 {elapsed:.1f}s")
        return state

    def _writer_node(self, state: GraphState) -> GraphState:
        request = state["request"]
        outline = state["outline"]
        revision_count = state.get("revision_count", 0)
        if revision_count > 0:
            state = self._append_log(state, f"Writer：根据审校意见进行第 {revision_count} 次修订")
        else:
            state = self._append_log(state, "Writer：开始编写场景、动作与对白")
        started_at = time.time()
        draft = self.writer.run(request, outline, state.get("latest_feedback", ""))
        mode = draft.metadata.get("generation_mode", "unknown")
        elapsed = time.time() - started_at
        if mode == "fallback":
            state = self._append_log(state, f"Writer：API生成失败，已切换为兜底剧本生成 | 耗时 {elapsed:.1f}s")
        elif mode == "api_tail_repair":
            state = self._append_log(state, f"Writer：已通过API定向补尾 | 耗时 {elapsed:.1f}s")
        else:
            state = self._append_log(state, f"Writer：已通过API生成初稿 | 耗时 {elapsed:.1f}s")
        state["draft"] = draft
        return state

    def _reviewer_node(self, state: GraphState) -> GraphState:
        request = state["request"]
        draft = state["draft"]
        state = self._append_log(state, "Reviewer：检查逻辑、格式、节奏与风格统一性")
        started_at = time.time()
        review = self.reviewer.run(request, draft)
        review_mode = review.metadata.get("review_mode", "unknown")
        elapsed = time.time() - started_at
        if review_mode == "fallback":
            state = self._append_log(state, f"Reviewer：API审校失败，已保留当前稿件 | 耗时 {elapsed:.1f}s")
        elif review_mode == "local_fastpath":
            state = self._append_log(state, f"Reviewer：本地快速审校通过，跳过远程润色 | 耗时 {elapsed:.1f}s")
        else:
            state = self._append_log(state, f"Reviewer：已通过API完成审校 | 耗时 {elapsed:.1f}s")
        state["review"] = review
        return state

    def _review_route(self, state: GraphState) -> str:
        review = state["review"]
        revision_count = state.get("revision_count", 0)
        if review.approved:
            return "approved"
        if review.metadata.get("tail_repair_only") == "true" and revision_count < 1:
            return "rewrite"
        if revision_count >= 1:
            return "finalize"
        return "rewrite"

    def _rewrite_node(self, state: GraphState) -> GraphState:
        review = state["review"]
        draft = state.get("draft")
        state = self._append_log(state, f"Reviewer：未通过，修订建议：{review.feedback}")
        state["revision_count"] = state.get("revision_count", 0) + 1
        latest_feedback = review.feedback
        if draft and draft.content.strip():
            latest_feedback = f"{review.feedback}\n\n当前剧本：\n{draft.content}"
        state["latest_feedback"] = latest_feedback
        return state

    def _finalize_node(self, state: GraphState) -> GraphState:
        review: Optional[ReviewResult] = state.get("review")
        draft: Optional[ScriptDraft] = state.get("draft")
        if review and review.approved:
            state = self._append_log(state, "Reviewer：审校通过，输出最终剧本")
        elif review:
            state = self._append_log(state, "Director：达到单次修订上限，输出当前最优在线版本")
        final_script = draft.content if draft else ""
        if review and self._should_use_review_script(review, draft):
            final_script = review.polished_script
        state["final_script"] = final_script
        return state

    def _should_use_review_script(self, review: ReviewResult, draft: Optional[ScriptDraft]) -> bool:
        polished = (review.polished_script or "").strip()
        if not polished:
            return False
        review_mode = review.metadata.get("review_mode", "unknown")
        if review_mode == "fallback":
            return False
        if not draft or not draft.content.strip():
            return True
        draft_text = draft.content.strip()
        if len(polished) < max(400, int(len(draft_text) * 0.8)):
            return False
        polished_scene_count = self._scene_marker_count(polished)
        draft_scene_count = self._scene_marker_count(draft_text)
        if draft_scene_count and polished_scene_count < draft_scene_count:
            return False
        return True

    def _scene_marker_count(self, content: str) -> int:
        import re

        scene_numbers = set()
        for match in re.finditer(r"(?:^|\n)\s*场景\s*(\d+)(?:\s|[:：]|$)", content, flags=re.MULTILINE):
            scene_numbers.add(match.group(1))
        for match in re.finditer(r"(?:^|\n)\s*【场景\s*(\d+)】", content, flags=re.MULTILINE):
            scene_numbers.add(match.group(1))
        for match in re.finditer(r"(?:^|\n)\s*场景号[:：]?\s*(\d+)", content):
            scene_numbers.add(match.group(1))
        return len(scene_numbers)

    def _target_scene_count(self, request: UserRequest) -> int:
        if request.episode_duration <= 45:
            per_episode = 3
        elif request.episode_duration <= 90:
            per_episode = 4
        else:
            per_episode = 5
        return per_episode * max(request.episodes, 1)

    def _script_completeness(self, request: UserRequest, script: str) -> Dict[str, Any]:
        target_scene_count = self._target_scene_count(request)
        actual_scene_count = self._scene_marker_count(script)
        is_complete = bool(script.strip()) and actual_scene_count >= target_scene_count and script.strip().endswith(("。", "！", "？"))
        return {
            "is_complete": is_complete,
            "target_scene_count": target_scene_count,
            "actual_scene_count": actual_scene_count,
        }

    def _build_graph(self):
        graph = StateGraph(GraphState)
        graph.add_node("planner", self._planner_node)
        graph.add_node("writer", self._writer_node)
        graph.add_node("reviewer", self._reviewer_node)
        graph.add_node("rewrite", self._rewrite_node)
        graph.add_node("finalize", self._finalize_node)

        graph.add_edge(START, "planner")
        graph.add_edge("planner", "writer")
        graph.add_edge("writer", "reviewer")
        graph.add_conditional_edges(
            "reviewer",
            self._review_route,
            {
                "approved": "finalize",
                "rewrite": "rewrite",
                "finalize": "finalize",
            },
        )
        graph.add_edge("rewrite", "writer")
        graph.add_edge("finalize", END)
        return graph.compile()

    def run(self, request: UserRequest, logger: Callable[[str], None]) -> Dict[str, Any]:
        self._runtime_logger = logger
        try:
            logger("Director：开始协调多Agent工作流")
            state = self.graph.invoke(
                {
                    "request": request,
                    "logs": [],
                    "revision_count": 0,
                    "latest_feedback": "",
                }
            )
        finally:
            self._runtime_logger = None

        outline = state["outline"]
        review = state["review"]
        final_script = state.get("final_script", "")
        completeness = self._script_completeness(request, final_script)
        return {
            "title": outline.title,
            "request_meta": {
                "styles": request.styles,
                "writing_tone": request.writing_tone,
                "episodes": request.episodes,
                "episode_duration": request.episode_duration,
            },
            "outline": {
                "opening_hook": outline.opening_hook,
                "core_conflict": outline.core_conflict,
                "reversals": outline.reversals,
                "ending_hook": outline.ending_hook,
                "three_act_outline": outline.three_act_outline,
            },
            "review": {
                "approved": review.approved,
                "feedback": review.feedback,
                "mode": review.metadata.get("review_mode", "unknown"),
            },
            "generation": {
                "writer_mode": state.get("draft").metadata.get("generation_mode", "unknown") if state.get("draft") else "unknown",
            },
            "script": final_script,
            "script_status": completeness,
            "orchestration": {
                "engine": "langgraph",
                "revision_count": state.get("revision_count", 0),
            },
        }
