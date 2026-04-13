import threading
import time
import uuid
from typing import Any, Dict, List


class JobStore:
    def __init__(self) -> None:
        self._jobs: Dict[str, Dict[str, Any]] = {}
        self._sessions: Dict[str, Dict[str, Any]] = {}
        self._lock = threading.Lock()

    def create_job(self, payload: Dict[str, Any]) -> str:
        job_id = str(uuid.uuid4())
        with self._lock:
            self._jobs[job_id] = {
                "id": job_id,
                "status": "queued",
                "logs": [],
                "result": None,
                "error": None,
                "payload": payload,
                "created_at": time.time(),
            }
        return job_id

    def add_log(self, job_id: str, message: str) -> None:
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id]["logs"].append({
                    "message": message,
                    "timestamp": time.time(),
                })

    def set_status(self, job_id: str, status: str) -> None:
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id]["status"] = status

    def set_result(self, job_id: str, result: Dict[str, Any]) -> None:
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id]["result"] = result
                self._jobs[job_id]["status"] = "completed"

    def set_error(self, job_id: str, error: str) -> None:
        with self._lock:
            if job_id in self._jobs:
                self._jobs[job_id]["error"] = error
                self._jobs[job_id]["status"] = "failed"

    def get_job(self, job_id: str) -> Dict[str, Any]:
        with self._lock:
            return self._jobs.get(job_id, {})

    # ========== 会话级大纲存储（用于大纲交互式审阅） ==========

    def save_session_outline(self, session_id: str, data: Dict[str, Any]) -> None:
        """保存大纲和用户请求到会话，供后续交互编辑使用。"""
        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = {
                    "result": {},
                    "conversation_history": [],
                    "updated_at": time.time(),
                }
            self._sessions[session_id]["outline_data"] = data
            self._sessions[session_id]["outline_chat"] = []
            self._sessions[session_id]["updated_at"] = time.time()

    def get_session_outline(self, session_id: str) -> Dict[str, Any]:
        """获取会话中保存的大纲数据。"""
        with self._lock:
            session = self._sessions.get(session_id, {})
            return session.get("outline_data", {})

    def update_session_outline(self, session_id: str, outline: Dict[str, Any]) -> None:
        """更新会话中的大纲。"""
        with self._lock:
            if session_id in self._sessions and "outline_data" in self._sessions[session_id]:
                self._sessions[session_id]["outline_data"]["outline"] = outline
                self._sessions[session_id]["updated_at"] = time.time()

    def add_outline_chat(self, session_id: str, role: str, content: str) -> None:
        """追加大纲审阅阶段的对话记录。"""
        with self._lock:
            if session_id in self._sessions:
                if "outline_chat" not in self._sessions[session_id]:
                    self._sessions[session_id]["outline_chat"] = []
                self._sessions[session_id]["outline_chat"].append({
                    "role": role,
                    "content": content,
                    "timestamp": time.time(),
                })

    def get_outline_chat(self, session_id: str) -> List[Dict[str, str]]:
        """获取大纲审阅阶段的对话历史。"""
        with self._lock:
            session = self._sessions.get(session_id, {})
            return session.get("outline_chat", [])

    # ========== 会话级剧本存储（用于多轮编辑） ==========

    def save_session_script(self, session_id: str, result: Dict[str, Any]) -> None:
        """保存一次生成的完整结果到会话，供后续多轮编辑使用。保留已有的大纲数据。"""
        with self._lock:
            if session_id not in self._sessions:
                self._sessions[session_id] = {}
            self._sessions[session_id]["result"] = result
            self._sessions[session_id]["conversation_history"] = []
            self._sessions[session_id]["updated_at"] = time.time()

    def get_session_script(self, session_id: str) -> Dict[str, Any]:
        """获取会话中最新的剧本结果。"""
        with self._lock:
            session = self._sessions.get(session_id, {})
            return session.get("result", {})

    def update_session_script(self, session_id: str, new_script: str) -> None:
        """更新会话中的剧本文本（场景编辑后）。"""
        with self._lock:
            if session_id in self._sessions:
                self._sessions[session_id]["result"]["script"] = new_script
                self._sessions[session_id]["updated_at"] = time.time()

    def add_conversation_message(self, session_id: str, role: str, content: str) -> None:
        """追加一条对话记录到会话历史。"""
        with self._lock:
            if session_id in self._sessions:
                self._sessions[session_id]["conversation_history"].append({
                    "role": role,
                    "content": content,
                    "timestamp": time.time(),
                })

    def get_conversation_history(self, session_id: str) -> List[Dict[str, str]]:
        """获取会话的对话历史。"""
        with self._lock:
            session = self._sessions.get(session_id, {})
            return session.get("conversation_history", [])

    def has_session(self, session_id: str) -> bool:
        with self._lock:
            return session_id in self._sessions
