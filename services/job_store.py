import threading
import time
import uuid
from typing import Dict, Any


class JobStore:
    def __init__(self) -> None:
        self._jobs: Dict[str, Dict[str, Any]] = {}
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
