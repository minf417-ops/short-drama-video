import time
from typing import Any, Callable, Dict, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from .planner import PlannerAgent
from .writer import WriterAgent
from .reviewer import ReviewerAgent
from .schemas import PlanOutline, ReviewResult, ScriptDraft, UserRequest
from services.llm_service import LLMService


class GraphState(TypedDict, total=False):
    """LangGraph 在节点之间传递的统一状态。

    这里集中保存 request / outline / draft / review / logs 等关键数据，
    让各个 Agent 节点通过同一个状态对象协作。
    """
    request: UserRequest
    outline: PlanOutline
    draft: ScriptDraft
    review: ReviewResult
    final_script: str
    logs: list[str]
    revision_count: int
    latest_feedback: str


class DirectorAgent:
    """多 Agent 总调度器。

    自身不直接写大纲或剧本，而是负责：
    - 组织 Planner / Writer / Reviewer 的执行顺序
    - 管理修订次数
    - 根据审校结果决定重写、结束或输出当前最优稿
    """
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
        """第一阶段：把用户需求收敛成结构化剧情大纲。"""
        request = state["request"]
        state = self._append_log(state, "Planner：生成大纲、冲突点与反转设计")
        started_at = time.time()
        outline = self.planner.run(request, logger=self._runtime_logger)
        state["outline"] = outline
        if "开场3秒内抛出与" in outline.opening_hook:
            state = self._append_log(state, "Planner：API生成大纲失败，已切换为结构化兜底大纲")
        elapsed = time.time() - started_at
        state = self._append_log(state, f"Planner：完成，剧本标题暂定为{outline.title} | 耗时 {elapsed:.1f}s")
        return state

    def _writer_node(self, state: GraphState) -> GraphState:
        """第二阶段：根据大纲生成剧本，或根据上一轮反馈进行修订。"""
        request = state["request"]
        outline = state["outline"]
        revision_count = state.get("revision_count", 0)
        if revision_count > 0:
            state = self._append_log(state, f"Writer：根据审校意见进行第 {revision_count} 次修订")
        else:
            state = self._append_log(state, "Writer：开始编写场景、动作与对白")
        started_at = time.time()
        draft = self.writer.run(request, outline, state.get("latest_feedback", ""), logger=self._runtime_logger)
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
        """第三阶段：审校当前剧本，判断是否通过或需要修补。"""
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
        """状态图分叉逻辑。

        - 审校通过：直接 finalize
        - 可低成本修补：回到 writer
        - 修订次数达到上限：直接 finalize
        """
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
        """把审校意见回填到状态中，供下一轮 Writer 定向修补。"""
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
        """决定最终输出哪一版剧本。"""
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
        """防止 Reviewer 返回的润色稿过短、缺场景或整体退化时覆盖原稿。"""
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
        """兼容多种场景头格式，统计剧本里的实际场景数。"""
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
        """根据单集时长与集数推算目标场景数。"""
        if request.episode_duration <= 45:
            per_episode = 3
        elif request.episode_duration <= 90:
            per_episode = 4
        else:
            per_episode = 5
        return per_episode * max(request.episodes, 1)

    def _script_completeness(self, request: UserRequest, script: str) -> Dict[str, Any]:
        """对最终脚本做完整性判断，供接口层决定是否允许继续导出 / 视频化。"""
        import re as _re
        valid_endings = ("\u3002", "\uff01", "\uff1f", "\u201d", "\u2019", "\u3011", "\uff09", ")", "\u2026", "\u2014", "\u2014\u2014", "\u300b", ".")
        if request.episodes > 1:
            ep_headers = _re.findall(r"第\d+集[：:]", script)
            actual_ep_count = len(ep_headers)
            target_ep_count = max(request.episodes, 1)
            ep_ok = actual_ep_count >= max(target_ep_count - 1, 1)
            is_complete = bool(script.strip()) and ep_ok and script.strip().endswith(valid_endings)
            scenes_per_ep = self.writer._scenes_per_episode(request.episode_duration)
            return {
                "is_complete": is_complete,
                "target_scene_count": target_ep_count * scenes_per_ep,
                "actual_scene_count": actual_ep_count * scenes_per_ep,
                "target_episodes": target_ep_count,
                "actual_episodes": actual_ep_count,
            }
        target_scene_count = self._target_scene_count(request)
        actual_scene_count = self._scene_marker_count(script)
        scene_ok = actual_scene_count >= max(target_scene_count - 1, 1)
        is_complete = bool(script.strip()) and scene_ok and script.strip().endswith(valid_endings)
        return {
            "is_complete": is_complete,
            "target_scene_count": target_scene_count,
            "actual_scene_count": actual_scene_count,
        }

    def _build_graph(self):
        """定义 LangGraph 状态图，把节点和条件路由显式表达出来。"""
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
        """对外统一入口：执行多 Agent 工作流并返回结构化结果。"""
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
