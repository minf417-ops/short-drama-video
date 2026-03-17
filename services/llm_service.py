import json
import os
import re
import time
from typing import Any, Dict

from dotenv import load_dotenv

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None


load_dotenv(dotenv_path=".env", override=True, encoding="utf-8")


class LLMService:
    def __init__(self) -> None:
        self.api_key = ""
        self.base_url = ""
        self.model = ""
        self.request_timeout = 300.0
        self.retry_on_timeout = 2
        self.max_completion_tokens = 8192
        self.strict_api = True
        self.last_call_mode = "uninitialized"
        self.client = None
        self.refresh_config(force_rebuild=True)

    def refresh_config(self, force_rebuild: bool = False) -> bool:
        next_api_key = os.getenv("ARK_API_KEY", "").strip().strip('"').strip("'")
        next_base_url = os.getenv("ARK_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3").strip().strip('"').strip("'")
        next_model = os.getenv("ARK_MODEL", "doubao-seed-2-0-pro-260215").strip().strip('"').strip("'")
        next_timeout = float(os.getenv("ARK_TIMEOUT_SECONDS", "300").strip().strip('"').strip("'") or "300")
        next_retry = int(os.getenv("ARK_RETRY_ON_TIMEOUT", "2").strip().strip('"').strip("'") or "2")
        next_max_tokens = int(os.getenv("ARK_MAX_TOKENS", "8192").strip().strip('"').strip("'") or "8192")
        next_strict = os.getenv("STRICT_LLM_API", "1").strip().lower() not in {"0", "false", "no"}
        changed = force_rebuild or any(
            [
                next_api_key != self.api_key,
                next_base_url != self.base_url,
                next_model != self.model,
                next_timeout != self.request_timeout,
                next_retry != self.retry_on_timeout,
                next_max_tokens != self.max_completion_tokens,
                next_strict != self.strict_api,
            ]
        )
        self.api_key = next_api_key
        self.base_url = next_base_url
        self.model = next_model
        self.request_timeout = next_timeout
        self.retry_on_timeout = next_retry
        self.max_completion_tokens = next_max_tokens
        self.strict_api = next_strict
        if changed:
            self._init_client()
        return changed

    def _init_client(self) -> None:
        if self.api_key and self.model and OpenAI is not None:
            self.client = OpenAI(api_key=self.api_key, base_url=self.base_url, timeout=self.request_timeout, max_retries=0)
        else:
            self.client = None

    def _rebuild_client(self) -> None:
        self.client = None
        self._init_client()

    def _is_connection_error(self, error_text: str) -> bool:
        keywords = [
            "connection error",
            "server disconnected",
            "remoteprotocolerror",
            "connection reset",
            "connection aborted",
            "apiconnectionerror",
            "temporarily unavailable",
            "eof occurred",
        ]
        lowered = error_text.lower()
        return any(keyword in lowered for keyword in keywords)

    def _initial_timeout_for_tokens(self, max_tokens: int) -> float:
        if max_tokens <= 900:
            return min(self.request_timeout, 70.0)
        if max_tokens <= 1300:
            return min(self.request_timeout, 90.0)
        if max_tokens <= 2200:
            return min(self.request_timeout, 120.0)
        return self.request_timeout

    def is_available(self) -> bool:
        return self.client is not None

    def runtime_info(self) -> Dict[str, Any]:
        self.refresh_config()
        return {
            "available": self.is_available(),
            "base_url": self.base_url,
            "model": self.model,
            "has_api_key": bool(self.api_key),
            "sdk_loaded": OpenAI is not None,
            "last_call_mode": self.last_call_mode,
            "strict_api": self.strict_api,
            "timeout_seconds": self.request_timeout,
            "retry_on_timeout": self.retry_on_timeout,
            "max_completion_tokens": self.max_completion_tokens,
        }

    def complete(self, system_prompt: str, user_prompt: str, temperature: float = 0.7, max_tokens: int = 3200) -> str:
        self.refresh_config()
        if not self.client:
            self.last_call_mode = "unavailable"
            raise RuntimeError("未检测到可用的模型客户端，请检查 ARK_API_KEY / ARK_BASE_URL / ARK_MODEL 配置")
        attempts = max(self.retry_on_timeout + 1, 2)
        current_max_tokens = min(max(max_tokens, 256), self.max_completion_tokens)
        current_timeout = self._initial_timeout_for_tokens(current_max_tokens)
        length_retry_done = False

        while True:
            for index in range(attempts):
                try:
                    self.last_call_mode = "api_chat"
                    response = self.client.chat.completions.create(
                        model=self.model,
                        messages=[
                            {"role": "system", "content": system_prompt},
                            {"role": "user", "content": user_prompt},
                        ],
                        temperature=temperature,
                        max_tokens=current_max_tokens,
                        timeout=current_timeout,
                    )
                    content = self._extract_chat_text(response)
                    if content:
                        return content
                    raise RuntimeError("模型返回内容为空或格式不符合预期")
                except Exception as exc:
                    error_text = str(exc).lower()
                    if ("timed out" in error_text or "timeout" in error_text) and index < attempts - 1:
                        self.last_call_mode = "api_retry"
                        current_timeout = min(current_timeout * 1.2, 180)
                        time.sleep(0.8)
                        continue
                    if self._is_connection_error(error_text) and index < attempts - 1:
                        self.last_call_mode = "api_retry_connection"
                        self._rebuild_client()
                        current_timeout = min(max(current_timeout * 1.1, self._initial_timeout_for_tokens(current_max_tokens)), 180)
                        time.sleep(0.8 + index * 0.5)
                        continue
                    if ("length" in error_text or "maximum context" in error_text or "max_tokens" in error_text) and not length_retry_done:
                        self.last_call_mode = "api_retry_length"
                        current_max_tokens = min(int(current_max_tokens * 1.25), self.max_completion_tokens)
                        current_timeout = self._initial_timeout_for_tokens(current_max_tokens)
                        length_retry_done = True
                        break
                    raise RuntimeError(f"模型调用失败：{exc}") from exc

    def complete_json(self, system_prompt: str, user_prompt: str, required_fields: list[str], temperature: float = 0.3, max_tokens: int = 3200) -> Dict[str, Any]:
        raw = self.complete(system_prompt, user_prompt, temperature=temperature, max_tokens=max_tokens)
        try:
            return self.parse_json_text(raw)
        except Exception as first_error:
            repair_prompt = f"""
你上一次输出不是合法JSON。请修复为严格JSON，仅输出一个JSON对象，必须满足：
1) 全部键名使用英文双引号
2) 不要注释、不要Markdown、不要代码块
3) 字段必须包含：{", ".join(required_fields)}

待修复内容：
{raw}
"""
            repaired = self.complete(system_prompt, repair_prompt, temperature=0.1, max_tokens=max_tokens)
            try:
                return self.parse_json_text(repaired)
            except Exception as second_error:
                raise RuntimeError(f"JSON解析失败，初次错误：{first_error}；修复后错误：{second_error}") from second_error

    def _extract_chat_text(self, response: Any) -> str:
        choices = getattr(response, "choices", None) or []
        if not choices:
            return ""
        message = getattr(choices[0], "message", None)
        if not message:
            return ""
        content = getattr(message, "content", None)
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                text = getattr(item, "text", None)
                if isinstance(text, str) and text.strip():
                    parts.append(text.strip())
                    continue
                if isinstance(item, dict):
                    value = item.get("text")
                    if isinstance(value, str) and value.strip():
                        parts.append(value.strip())
            return "\n".join(parts).strip()
        return ""

    def parse_json_text(self, text: str) -> Dict[str, Any]:
        if not isinstance(text, str):
            raise json.JSONDecodeError("JSON文本为空或非字符串", str(text), 0)
        cleaned = text.strip()
        if not cleaned:
            raise json.JSONDecodeError("JSON文本为空", cleaned, 0)
        for candidate in self._candidate_json_texts(cleaned):
            try:
                return self._try_load_json(candidate)
            except json.JSONDecodeError:
                continue

        raise json.JSONDecodeError("No JSON object found", cleaned, 0)

    def _candidate_json_texts(self, cleaned: str) -> list[str]:
        candidates = [cleaned]

        if "```json" in cleaned:
            fenced = cleaned.split("```json", 1)[1]
            fenced = fenced.split("```", 1)[0].strip()
            candidates.append(fenced)

        if "```" in cleaned:
            fenced = cleaned.split("```", 1)[1]
            fenced = fenced.split("```", 1)[0].strip()
            candidates.append(fenced)

        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start != -1 and end != -1 and end > start:
            candidates.append(cleaned[start:end + 1])
        return candidates

    def _try_load_json(self, raw: str) -> Dict[str, Any]:
        candidates = [raw]
        normalized = raw.replace("“", "\"").replace("”", "\"")
        normalized = normalized.replace("‘", "'").replace("’", "'")
        normalized = re.sub(r",\s*([}\]])", r"\1", normalized)
        candidates.append(normalized)
        for candidate in candidates:
            try:
                data = json.loads(candidate)
                if isinstance(data, dict):
                    return data
            except json.JSONDecodeError:
                continue
        raise json.JSONDecodeError("No JSON object found", raw, 0)

