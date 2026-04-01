from __future__ import annotations

import base64
import contextlib
import json
import struct
import re
import os
import pathlib
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import wave
from abc import ABC, abstractmethod

from PIL import Image, ImageDraw, ImageFont

try:
    from volcengine.visual.VisualService import VisualService
except ImportError:
    VisualService = None

from .models import AssetRecord, DialogueLine, Scene, Shot


class BaseImageProvider(ABC):
    provider_name = "base-image"

    @abstractmethod
    def generate(self, shot: Shot, output_dir: str) -> AssetRecord:
        raise NotImplementedError


class BaseTTSProvider(ABC):
    provider_name = "base-tts"

    @abstractmethod
    def synthesize(self, scene: Scene, shot: Shot, output_dir: str) -> AssetRecord:
        raise NotImplementedError


class BaseVideoProvider(ABC):
    provider_name = "base-video"

    @abstractmethod
    def render(self, shot: Shot, image_asset: AssetRecord, audio_asset: AssetRecord, output_dir: str, target_duration: float | None = None) -> AssetRecord:
        raise NotImplementedError


class VolcImageProvider(BaseImageProvider):
    provider_name = "volc-image"

    def __init__(self) -> None:
        self.endpoint = os.getenv("VOLC_IMAGE_ENDPOINT", "https://visual.volcengineapi.com").strip()
        self.region = os.getenv("VOLC_REGION", "cn-north-1").strip() or "cn-north-1"
        self.service = os.getenv("VOLC_SERVICE", "cv").strip() or "cv"
        self.submit_action = os.getenv("VOLC_IMAGE_ACTION", "CVSync2AsyncSubmitTask").strip() or "CVSync2AsyncSubmitTask"
        self.version = os.getenv("VOLC_IMAGE_VERSION", "2022-08-31").strip() or "2022-08-31"
        self.req_key = os.getenv("VOLC_IMAGE_REQ_KEY", "").strip()
        self.access_key_id = os.getenv("VOLC_ACCESS_KEY_ID", "").strip()
        self.secret_access_key = os.getenv("VOLC_SECRET_ACCESS_KEY", "").strip()
        self.poll_interval_seconds = max(float(os.getenv("VOLC_IMAGE_POLL_INTERVAL_SECONDS", "2").strip() or "2"), 0.2)
        self.poll_timeout_seconds = max(float(os.getenv("VOLC_IMAGE_POLL_TIMEOUT_SECONDS", "120").strip() or "120"), 5.0)

    def generate(self, shot: Shot, output_dir: str) -> AssetRecord:
        self._ensure_ready()
        os.makedirs(output_dir, exist_ok=True)
        prompt, response, image_log = self._generate_with_fallback(shot)
        image_bytes = self._extract_image_bytes(response)
        path = os.path.join(output_dir, f"{shot.shot_id}.png")
        with open(path, "wb") as file:
            file.write(image_bytes)
        return AssetRecord(
            asset_id=f"image-{shot.shot_id}",
            asset_type="image",
            scene_id=shot.scene_id,
            shot_id=shot.shot_id,
            provider=self.provider_name,
            file_path=path,
            prompt=prompt,
            duration_seconds=shot.duration_seconds,
            metadata={"raw_response": response, "original_prompt": shot.image_prompt, "image_log": image_log},
        )

    def _ensure_ready(self) -> None:
        if not self.req_key:
            raise RuntimeError("缺少 VOLC_IMAGE_REQ_KEY 配置，通用3.0 文生图需提供视觉服务 req_key")
        if not self.access_key_id or not self.secret_access_key:
            raise RuntimeError("缺少 VOLC_ACCESS_KEY_ID 或 VOLC_SECRET_ACCESS_KEY 配置")
        if VisualService is None:
            raise RuntimeError("未安装 volcengine SDK，请先安装 requirements.txt 中的依赖")

    def _extract_image_bytes(self, response: dict) -> bytes:
        candidates = []
        if isinstance(response, dict):
            candidates.extend(
                [
                    response.get("image_urls"),
                    response.get("image_url"),
                    response.get("image_base64"),
                    response.get("data"),
                    response.get("image"),
                    response.get("images"),
                    response.get("result"),
                    response.get("binary_data_base64"),
                    response.get("binary_data"),
                ]
            )
            for key in ["data", "result"]:
                nested = response.get(key)
                if isinstance(nested, dict):
                    candidates.extend(
                        [
                            nested.get("image_urls"),
                            nested.get("image_url"),
                            nested.get("image_base64"),
                            nested.get("binary_data_base64"),
                            nested.get("binary_data"),
                            nested.get("image"),
                            nested.get("images"),
                        ]
                    )
        for candidate in candidates:
            image_bytes = self._decode_image_candidate(candidate)
            if image_bytes:
                return image_bytes
        raise RuntimeError(f"文生图响应中未找到图片内容，可检查返回结构：{json.dumps(response, ensure_ascii=False)[:500]}")

    def _decode_image_candidate(self, candidate: object) -> bytes | None:
        if isinstance(candidate, str):
            if candidate.startswith("http://") or candidate.startswith("https://"):
                with urllib.request.urlopen(candidate, timeout=60) as response:
                    return response.read()
            try:
                return base64.b64decode(candidate)
            except Exception:
                return None
        if isinstance(candidate, list):
            for item in candidate:
                decoded = self._decode_image_candidate(item)
                if decoded:
                    return decoded
        if isinstance(candidate, dict):
            for key in ["image_base64", "base64", "url", "binary_data", "binary_data_base64", "image_urls"]:
                if key in candidate:
                    decoded = self._decode_image_candidate(candidate[key])
                    if decoded:
                        return decoded
        return None

    def _post_visual_sdk(self, payload: dict) -> tuple[dict, dict]:
        visual_service = VisualService()
        visual_service.set_ak(self.access_key_id)
        visual_service.set_sk(self.secret_access_key)
        visual_service.set_host(urllib.parse.urlparse(self.endpoint).netloc or "visual.volcengineapi.com")
        log_payload = {
            "endpoint": self.endpoint,
            "region": self.region,
            "service": self.service,
            "action": self.submit_action,
            "version": self.version,
            "req_key": payload.get("req_key"),
            "seed": payload.get("seed"),
            "return_url": payload.get("return_url"),
            "prompt_preview": str(payload.get("prompt", ""))[:240],
            "poll_interval_seconds": self.poll_interval_seconds,
            "poll_timeout_seconds": self.poll_timeout_seconds,
        }
        try:
            with self._without_proxy():
                submit_response = self._invoke_submit(visual_service, payload)
                response, query_log = self._wait_for_result(visual_service, payload, submit_response)
        except Exception as error:
            raise RuntimeError(f"文生图请求失败：{error}") from error
        normalized_response = self._normalize_visual_response(response)
        return normalized_response, {
            "request": log_payload,
            "submit_response": self._truncate_for_log(submit_response),
            "query_log": query_log,
            "final_response": self._truncate_for_log(normalized_response),
        }

    def _invoke_submit(self, visual_service: VisualService, payload: dict) -> dict:
        if self.submit_action == "CVSync2AsyncSubmitTask":
            response = visual_service.cv_sync2async_submit_task(payload)
        elif self.submit_action == "CVProcess":
            response = visual_service.cv_process(payload)
        else:
            raise RuntimeError(f"暂不支持的文生图 Action：{self.submit_action}")
        return self._normalize_visual_response(response)

    def _wait_for_result(self, visual_service: VisualService, payload: dict, submit_response: dict) -> tuple[dict, dict]:
        if self.submit_action != "CVSync2AsyncSubmitTask":
            return submit_response, {"mode": "sync", "poll_count": 0, "task_id": self._extract_task_id(submit_response), "polls": []}
        if self._response_has_image(submit_response):
            return submit_response, {"mode": "submit_contains_result", "poll_count": 0, "task_id": self._extract_task_id(submit_response), "polls": []}
        task_id = self._extract_task_id(submit_response)
        if not task_id:
            raise RuntimeError(f"文生图提交成功但未返回 task_id：{json.dumps(submit_response, ensure_ascii=False)[:500]}")
        deadline = time.time() + self.poll_timeout_seconds
        last_response = submit_response
        polls: list[dict] = []
        while time.time() < deadline:
            query_payload = {"req_key": payload.get("req_key"), "task_id": task_id}
            query_response = self._normalize_visual_response(visual_service.cv_sync2async_get_result(query_payload))
            last_response = query_response
            polls.append(self._build_query_log_entry(query_response, len(polls) + 1))
            if self._response_has_image(query_response):
                return query_response, {"mode": "async", "task_id": task_id, "poll_count": len(polls), "polls": polls}
            if self._is_task_failed(query_response):
                raise RuntimeError(f"文生图任务失败：{json.dumps(query_response, ensure_ascii=False)[:800]}")
            time.sleep(self.poll_interval_seconds)
        raise RuntimeError(f"文生图任务轮询超时 task_id={task_id}，最后响应：{json.dumps(last_response, ensure_ascii=False)[:800]}")

    def _normalize_visual_response(self, response: object) -> dict:
        if isinstance(response, str):
            try:
                response = json.loads(response)
            except json.JSONDecodeError:
                raise RuntimeError(f"文生图返回非 JSON 内容：{response[:500]}")
        if not isinstance(response, dict):
            raise RuntimeError(f"文生图返回类型异常：{type(response).__name__}")
        return response

    def _extract_task_id(self, response: dict) -> str:
        candidates = [
            response.get("task_id"),
            response.get("id"),
            response.get("data", {}).get("task_id") if isinstance(response.get("data"), dict) else None,
            response.get("result", {}).get("task_id") if isinstance(response.get("result"), dict) else None,
        ]
        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return ""

    def _response_has_image(self, response: dict) -> bool:
        try:
            return bool(self._extract_image_bytes(response))
        except RuntimeError:
            return False

    def _is_task_failed(self, response: dict) -> bool:
        values = [
            str(response.get("status", "")).lower(),
            str(response.get("state", "")).lower(),
            str(response.get("message", "")).lower(),
        ]
        for key in ["data", "result"]:
            nested = response.get(key)
            if isinstance(nested, dict):
                values.extend(
                    [
                        str(nested.get("status", "")).lower(),
                        str(nested.get("state", "")).lower(),
                        str(nested.get("message", "")).lower(),
                    ]
                )
        if any(value in {"failed", "fail", "error"} for value in values):
            return True
        code = response.get("code")
        if code not in (None, 0, 10000, 200, "0", "10000", "200"):
            return True
        return False

    def _generate_with_fallback(self, shot: Shot) -> tuple[str, dict, dict]:
        prompt_candidates: list[str] = []
        for candidate in [
            shot.image_prompt,
            self._soften_prompt(shot.image_prompt),
            self._build_safe_prompt(shot),
        ]:
            normalized = candidate.strip()
            if normalized and normalized not in prompt_candidates:
                prompt_candidates.append(normalized)
        last_error: RuntimeError | None = None
        for prompt in prompt_candidates:
            payload = {
                "req_key": self.req_key,
                "prompt": prompt,
                "return_url": False,
                "seed": abs(hash(shot.shot_id)) % (2**31),
            }
            try:
                response, image_log = self._post_visual_sdk(payload)
                return prompt, response, image_log
            except RuntimeError as error:
                last_error = error
                if "50511" not in str(error) and "Risk Not Pass" not in str(error):
                    break
        if last_error:
            raise last_error
        raise RuntimeError("文生图生成失败，未获得可用响应")

    def _build_query_log_entry(self, response: dict, index: int) -> dict:
        nested = response.get("data") if isinstance(response.get("data"), dict) else response.get("result") if isinstance(response.get("result"), dict) else {}
        return {
            "index": index,
            "status": response.get("status"),
            "code": response.get("code"),
            "message": response.get("message"),
            "task_status": nested.get("status") if isinstance(nested, dict) else None,
            "task_state": nested.get("state") if isinstance(nested, dict) else None,
            "has_image": self._response_has_image(response),
        }

    def _truncate_for_log(self, value: object, max_length: int = 1200) -> object:
        text = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
        if len(text) <= max_length:
            return value
        return text[:max_length] + "..."

    def _soften_prompt(self, prompt: str) -> str:
        softened = prompt
        replacements = {
            "棋子": "误会",
            "压迫感强": "气场强",
            "冲突": "对峙",
            "质问": "对话",
            "救你": "安慰你",
            "冷白皮": "肤色白皙",
            "戏剧张力强": "戏剧感明确",
        }
        for source, target in replacements.items():
            softened = softened.replace(source, target)
        return softened

    def _build_safe_prompt(self, shot: Shot) -> str:
        return (
            f"竖屏短剧电影感空镜背景，场景细节：{shot.scene_details or shot.visual_description}，"
            f"镜头{shot.camera}，景别{shot.framing}，运镜{shot.camera_movement}，情绪氛围{shot.emotion}。"
            "只生成环境与背景布置，不出现人物、不出现人脸、不出现身体、不出现剪影，"
            "突出景深、灯光、道具与空间层次，作为短剧视频背景底图。"
        )

    @contextlib.contextmanager
    def _without_proxy(self):
        proxy_keys = ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"]
        original = {key: os.environ.get(key) for key in proxy_keys}
        no_proxy_keys = ["NO_PROXY", "no_proxy"]
        original_no_proxy = {key: os.environ.get(key) for key in no_proxy_keys}
        try:
            for key in proxy_keys:
                os.environ.pop(key, None)
            for key in no_proxy_keys:
                os.environ[key] = "visual.volcengineapi.com,openspeech.bytedance.com,127.0.0.1,localhost"
            yield
        finally:
            for key, value in original.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            for key, value in original_no_proxy.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value


class VolcTTSProvider(BaseTTSProvider):
    provider_name = "volc-tts"

    def __init__(self) -> None:
        self.url = os.getenv("VOLC_TTS_URL", "https://openspeech.bytedance.com/api/v3/tts/unidirectional").strip()
        self.app_id = os.getenv("VOLC_TTS_APP_ID", "").strip()
        self.access_key = os.getenv("VOLC_TTS_ACCESS_KEY", "").strip()
        self.resource_id = os.getenv("VOLC_TTS_RESOURCE_ID", "volc.service_type.10029").strip()
        self.model = os.getenv("VOLC_TTS_MODEL", "seed-tts-2.0").strip()
        self.voice = os.getenv("VOLC_TTS_VOICE", "").strip()
        self.narrator_voice = os.getenv("VOLC_TTS_VOICE_NARRATOR", self.voice).strip()
        self.male_lead_voice = os.getenv("VOLC_TTS_VOICE_MALE_LEAD", self.voice).strip()
        self.female_lead_voice = os.getenv("VOLC_TTS_VOICE_FEMALE_LEAD", self.voice).strip()
        self.encoding = os.getenv("VOLC_TTS_ENCODING", "mp3").strip()
        self.speed_ratio = float(os.getenv("VOLC_TTS_SPEED_RATIO", "1.0").strip() or "1.0")
        self.sample_rate = int(os.getenv("VOLC_TTS_SAMPLE_RATE", "24000").strip() or "24000")
        self.bit_rate = int(os.getenv("VOLC_TTS_BIT_RATE", "128000").strip() or "128000")
        self.loudness_rate = int(os.getenv("VOLC_TTS_LOUDNESS_RATE", "0").strip() or "0")
        self.emotion = os.getenv("VOLC_TTS_EMOTION", "").strip()
        self.emotion_scale = int(os.getenv("VOLC_TTS_EMOTION_SCALE", "4").strip() or "4")
        self.enable_subtitle = os.getenv("VOLC_TTS_ENABLE_SUBTITLE", "0").strip().lower() in {"1", "true", "yes", "on"}
        self.silence_duration = int(os.getenv("VOLC_TTS_SILENCE_DURATION", "0").strip() or "0")
        self.compatible_fallback_voices = [
            os.getenv("VOLC_TTS_VOICE_COMPAT_FEMALE", "zh_female_shuangkuaisisi_moon_bigtts").strip(),
            os.getenv("VOLC_TTS_VOICE_COMPAT_EMO", "zh_female_roumeinvyou_emo_v2_mars_bigtts").strip(),
            os.getenv("VOLC_TTS_VOICE_COMPAT_MALE", "zh_male_M392_conversation_wvae_bigtts").strip(),
        ]

    def synthesize(self, scene: Scene, shot: Shot, output_dir: str) -> AssetRecord:
        self._ensure_ready()
        os.makedirs(output_dir, exist_ok=True)
        lines = shot.dialogue or []
        tts_text = (shot.tts_text or " ".join(line.text for line in lines)).strip()
        if not tts_text:
            path = os.path.join(output_dir, f"{shot.shot_id}.wav")
            duration = max(shot.duration_seconds, 1.2)
            self._write_silence(path, duration)
            return AssetRecord(
                asset_id=f"audio-{shot.shot_id}",
                asset_type="audio",
                scene_id=shot.scene_id,
                shot_id=shot.shot_id,
                provider=self.provider_name,
                file_path=path,
                prompt="",
                duration_seconds=duration,
                metadata={"transcript": "", "raw_response": None},
            )
        audio_bytes, response_meta = self._request_tts(scene, shot, lines, tts_text)
        extension = ".wav" if self.encoding.lower() == "wav" else f".{self.encoding.lower()}"
        path = os.path.join(output_dir, f"{shot.shot_id}{extension}")
        with open(path, "wb") as file:
            file.write(audio_bytes)
        duration = self._estimate_duration(path, shot.duration_seconds)
        return AssetRecord(
            asset_id=f"audio-{shot.shot_id}",
            asset_type="audio",
            scene_id=shot.scene_id,
            shot_id=shot.shot_id,
            provider=self.provider_name,
            file_path=path,
            prompt=tts_text,
            duration_seconds=duration,
            metadata={"transcript": tts_text, "raw_response": response_meta},
        )

    def _request_tts(self, scene: Scene, shot: Shot, lines: list[DialogueLine], transcript: str) -> tuple[bytes, dict]:
        selected_voice = self._select_voice(scene, shot, lines)
        fallback_voices: list[str | None] = []
        for candidate in [selected_voice, self.voice, self.narrator_voice, *self.compatible_fallback_voices, None]:
            if candidate not in fallback_voices:
                fallback_voices.append(candidate)
        last_error: RuntimeError | None = None
        for candidate in fallback_voices:
            payload = self._build_tts_payload(transcript, candidate)
            if not candidate:
                payload["req_params"].pop("speaker")
            try:
                audio_bytes, response_meta = self._post_chunked_audio(self.url, payload, self._headers())
                return audio_bytes, {**response_meta, "speaker": candidate or "default"}
            except RuntimeError as error:
                last_error = error
            message = str(last_error).lower() if last_error else ""
            if "mismatched" not in message and "speaker" not in message:
                break
        if last_error:
            raise last_error
        raise RuntimeError("TTS 请求失败，未获取到有效音频")

    def _build_tts_payload(self, transcript: str, speaker: str | None) -> dict:
        audio_params: dict[str, object] = {
            "format": self.encoding,
            "sample_rate": self.sample_rate,
        }
        if self.encoding.lower() == "mp3":
            audio_params["bit_rate"] = self.bit_rate
        if self.emotion:
            audio_params["emotion"] = self.emotion
            audio_params["emotion_scale"] = self.emotion_scale
        payload = {
            "user": {
                "uid": str(uuid.uuid4()),
            },
            "req_params": {
                "text": transcript,
                "speaker": speaker,
                "audio_params": audio_params,
            },
        }
        return payload

    def _speech_rate_value(self) -> int:
        if self.speed_ratio <= 0:
            return 0
        speech_rate = round((self.speed_ratio - 1.0) * 100)
        return max(-50, min(100, speech_rate))

    def _select_voice(self, scene: Scene, shot: Shot, lines: list[DialogueLine]) -> str:
        speakers = [line.speaker.strip() for line in lines if line.speaker.strip()]
        if not speakers:
            return self.narrator_voice or self.voice
        first_speaker = speakers[0]
        if "旁白" in first_speaker or "画外音" in first_speaker or "OS" in first_speaker:
            return self.narrator_voice or self.voice
        voice_style = self._character_voice_style(scene, shot, first_speaker)
        if voice_style == "female_lead":
            return self.female_lead_voice or self.voice or self.narrator_voice
        if voice_style == "male_lead":
            return self.male_lead_voice or self.voice or self.narrator_voice
        if shot.speaker_gender == "female":
            return self.female_lead_voice or self.voice or self.narrator_voice
        if shot.speaker_gender == "male":
            return self.male_lead_voice or self.voice or self.narrator_voice
        female_keywords = ["女主", "女", "姐", "妹", "母", "妈", "嫂", "婶", "姑", "娘", "妃", "后", "公主", "夫人", "小姐"]
        male_keywords = ["男主", "男", "哥", "弟", "父", "爸", "叔", "伯", "爷", "王", "帝", "太子", "少爷", "先生"]
        for kw in female_keywords:
            if kw in first_speaker:
                return self.female_lead_voice or self.voice
        for kw in male_keywords:
            if kw in first_speaker:
                return self.male_lead_voice or self.voice
        return self.voice or self.narrator_voice

    def _character_voice_style(self, scene: Scene, shot: Shot, speaker: str) -> str:
        female_markers = ["晚", "夏", "薇", "晴", "瑶", "宁", "雪", "柔", "雅", "娜", "琳", "颖", "婷", "倩", "姝", "姐", "妈"]
        male_markers = ["川", "默", "泽", "辰", "凯", "邦", "晏", "骁", "霆", "宸", "骏", "峰", "叔", "爷", "父", "哥"]
        normalized = speaker.strip()
        if shot.character_focus and normalized == shot.character_focus:
            if shot.speaker_gender == "female":
                return "female_lead"
            if shot.speaker_gender == "male":
                return "male_lead"
        scene_profile = getattr(scene, "character_profiles", {}).get(normalized, {}) if hasattr(scene, "character_profiles") else {}
        if scene_profile.get("gender") == "female":
            return "female_lead"
        if scene_profile.get("gender") == "male":
            return "male_lead"
        combined = " ".join(
            item
            for item in [
                normalized,
                shot.character_identity,
                shot.delivery_style,
                shot.speaker_age_group,
                shot.speaker_gender,
                scene_profile.get("identity", ""),
                scene_profile.get("speech_style", ""),
            ]
            if item
        )
        if any(keyword in combined for keyword in ["小姐", "夫人", "公主", "母亲", "新娘", "姐姐", "闺蜜"]):
            return "female_lead"
        if any(keyword in combined for keyword in ["先生", "少爷", "总裁", "父亲", "新郎", "哥哥", "老板"]):
            return "male_lead"
        if any(marker in normalized for marker in female_markers):
            return "female_lead"
        if any(marker in normalized for marker in male_markers):
            return "male_lead"
        ordered_names = [name.strip() for name in getattr(scene, "characters", []) if isinstance(name, str) and name.strip()]
        if ordered_names:
            if normalized == ordered_names[0]:
                return "female_lead"
            if len(ordered_names) > 1 and normalized == ordered_names[1]:
                return "male_lead"
        return ""

    def _headers(self) -> dict[str, str]:
        return {
            "Accept": "*/*",
            "Content-Type": "application/json",
            "X-Api-App-Id": self.app_id,
            "X-Api-Access-Key": self.access_key,
            "X-Api-Resource-Id": self.resource_id,
        }

    def _ensure_ready(self) -> None:
        if not self.app_id or not self.access_key:
            raise RuntimeError("缺少 VOLC_TTS_APP_ID 或 VOLC_TTS_ACCESS_KEY 配置")

    def _post_chunked_audio(self, url: str, payload: dict, headers: dict[str, str]) -> tuple[bytes, dict]:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(url=url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                body = response.read()
                content_type = response.headers.get("Content-Type", "")
                meta = {
                    "status": getattr(response, "status", None),
                    "content_type": content_type,
                    "content_length": len(body),
                }
        except urllib.error.HTTPError as error:
            error_bytes = error.read()
            error_text = error_bytes.decode("utf-8", errors="ignore")
            raise RuntimeError(f"TTS 请求失败，HTTP {error.code}: {error_text}") from error
        if body and self._looks_like_audio(body):
            return body, meta
        parsed = self._parse_streaming_json(body.decode("utf-8", errors="ignore")) if body else {}
        if isinstance(parsed, dict):
            code = parsed.get("code")
            message = str(parsed.get("message", ""))
            audio_bytes = self._extract_audio_bytes(parsed)
            if code in (0, 20000000, None) and audio_bytes:
                return audio_bytes, {
                    **meta,
                    "response_code": code,
                    "response_message": message,
                }
            if code not in (0, 20000000, None) or message:
                raise RuntimeError(f"TTS 返回异常 code={code}, message={message}")
        raise RuntimeError(f"TTS 未返回有效音频，响应信息：{json.dumps(meta, ensure_ascii=False)}")

    def _extract_audio_bytes(self, response: dict) -> bytes:
        candidates = []
        chunk_audio_parts: list[str] = []
        if isinstance(response, dict):
            candidates.extend([response.get("data"), response.get("audio"), response.get("result"), response.get("audio_data")])
            for chunk in response.get("chunks", []):
                if isinstance(chunk, dict):
                    code = chunk.get("code")
                    data = chunk.get("data")
                    if code == 0 and isinstance(data, str):
                        candidates.append(data)
                        chunk_audio_parts.append(data)
        if chunk_audio_parts:
            decoded_parts = [self._decode_audio_candidate(part) for part in chunk_audio_parts]
            decoded_parts = [part for part in decoded_parts if part]
            if decoded_parts:
                merged_bytes = self._merge_wav_chunks(decoded_parts)
                if merged_bytes and self._looks_like_audio(merged_bytes):
                    return merged_bytes
        for candidate in candidates:
            decoded = self._decode_audio_candidate(candidate)
            if decoded and self._looks_like_audio(decoded):
                return decoded
        raise RuntimeError(f"TTS 响应中未找到音频内容，可检查返回结构：{json.dumps(response, ensure_ascii=False)[:500]}")

    def _decode_audio_candidate(self, candidate: object) -> bytes | None:
        if isinstance(candidate, str):
            if candidate.startswith("http://") or candidate.startswith("https://"):
                with urllib.request.urlopen(candidate, timeout=60) as response:
                    return response.read()
            try:
                return base64.b64decode(candidate)
            except Exception:
                return None
        if isinstance(candidate, dict):
            for key in ["audio_base64", "base64", "url", "binary_data", "data"]:
                if key in candidate:
                    decoded = self._decode_audio_candidate(candidate[key])
                    if decoded:
                        return decoded
        return None

    def _looks_like_audio(self, audio_bytes: bytes) -> bool:
        if len(audio_bytes) < 16:
            return False
        if audio_bytes[:4] == b"RIFF" and audio_bytes[8:12] == b"WAVE":
            return True
        if audio_bytes[:3] == b"ID3":
            return True
        return False

    def _merge_wav_chunks(self, decoded_parts: list[bytes]) -> bytes | None:
        first = decoded_parts[0]
        if first[:4] != b"RIFF" or first[8:12] != b"WAVE":
            return b"".join(decoded_parts)
        data_index = first.find(b"data")
        if data_index == -1 or data_index + 8 > len(first):
            return b"".join(decoded_parts)
        header = bytearray(first[: data_index + 8])
        pcm_payload_parts = [first[data_index + 8 :]]
        for part in decoded_parts[1:]:
            if part[:4] == b"RIFF" and part[8:12] == b"WAVE":
                nested_index = part.find(b"data")
                if nested_index != -1 and nested_index + 8 <= len(part):
                    pcm_payload_parts.append(part[nested_index + 8 :])
            else:
                pcm_payload_parts.append(part)
        pcm_payload = b"".join(pcm_payload_parts)
        data_size = len(pcm_payload)
        riff_size = len(header) - 8 + data_size
        header[4:8] = struct.pack("<I", riff_size)
        header[data_index + 4 : data_index + 8] = struct.pack("<I", data_size)
        return bytes(header) + pcm_payload

    def _post_json(self, url: str, payload: dict, headers: dict[str, str]) -> dict:
        data = json.dumps(payload).encode("utf-8")
        request = urllib.request.Request(url=url, data=data, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                text = response.read().decode("utf-8")
        except urllib.error.HTTPError as error:
            error_text = error.read().decode("utf-8", errors="ignore")
            raise RuntimeError(f"TTS 请求失败，HTTP {error.code}: {error_text}") from error
        return self._parse_streaming_json(text)

    def _parse_streaming_json(self, text: str) -> dict:
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass
        decoder = json.JSONDecoder()
        index = 0
        objects = []
        while index < len(text):
            while index < len(text) and text[index].isspace():
                index += 1
            if index >= len(text):
                break
            if text[index] != "{":
                next_json = text.find("{", index)
                if next_json == -1:
                    break
                index = next_json
            try:
                obj, next_index = decoder.raw_decode(text, index)
            except json.JSONDecodeError:
                next_json = text.find("{", index + 1)
                if next_json == -1:
                    break
                index = next_json
                continue
            objects.append(obj)
            index = next_index
        if not objects:
            compact = text.strip()
            match = re.search(r"\{.*\}", compact, flags=re.DOTALL)
            if match:
                return json.loads(match.group(0))
            raise RuntimeError(f"TTS 返回内容无法解析为 JSON：{compact[:500]}")
        if len(objects) == 1:
            return objects[0]
        merged = {"chunks": objects}
        for obj in objects:
            if isinstance(obj, dict):
                code = obj.get("code", 0)
                if code not in (0, 20000000, None):
                    return obj
                for key, value in obj.items():
                    if key not in {"code"}:
                        merged[key] = value
                if code is not None:
                    merged["code"] = code
        for obj in objects:
            if isinstance(obj, dict) and obj.get("code") == 0 and isinstance(obj.get("data"), str):
                merged["data"] = obj["data"]
                break
        return merged

    def _estimate_duration(self, file_path: str, fallback: float) -> float:
        if file_path.lower().endswith(".wav"):
            try:
                with wave.open(file_path, "rb") as wav_file:
                    duration = wav_file.getnframes() / float(wav_file.getframerate())
                    if duration <= 0 or duration > 120:
                        return fallback
                    return max(duration, 0.1)
            except Exception:
                return fallback
        ffprobe_binary = shutil.which("ffprobe")
        if ffprobe_binary:
            command = [
                ffprobe_binary,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                file_path,
            ]
            completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="ignore")
            if completed.returncode == 0:
                try:
                    duration = float(completed.stdout.strip())
                    if 0 < duration <= 120:
                        return max(duration, 0.1)
                except ValueError:
                    pass
        ffmpeg_binary = shutil.which(os.getenv("FFMPEG_BINARY", "ffmpeg"))
        if ffmpeg_binary:
            command = [ffmpeg_binary, "-i", file_path]
            completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="ignore")
            stderr = completed.stderr or ""
            match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", stderr)
            if match:
                hours = int(match.group(1))
                minutes = int(match.group(2))
                seconds = float(match.group(3))
                duration = hours * 3600 + minutes * 60 + seconds
                if 0 < duration <= 120:
                    return max(duration, 0.1)
        return fallback

    def _write_silence(self, file_path: str, duration_seconds: float, sample_rate: int = 16000) -> None:
        frame_count = int(duration_seconds * sample_rate)
        with wave.open(file_path, "w") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(b"\x00\x00" * frame_count)


class PlaceholderImageProvider(BaseImageProvider):
    provider_name = "placeholder-image"

    def generate(self, shot: Shot, output_dir: str) -> AssetRecord:
        os.makedirs(output_dir, exist_ok=True)
        path = os.path.join(output_dir, f"{shot.shot_id}.png")
        image = Image.new("RGB", (1080, 1920), color=(20, 20, 24))
        draw = ImageDraw.Draw(image)
        try:
            title_font = ImageFont.truetype("msyh.ttc", 44)
            body_font = ImageFont.truetype("msyh.ttc", 30)
        except Exception:
            title_font = ImageFont.load_default()
            body_font = ImageFont.load_default()
        draw.rectangle([(60, 80), (1020, 1840)], outline=(255, 255, 255), width=2)
        draw.text((90, 120), f"{shot.scene_id} / {shot.shot_id}", fill=(255, 255, 255), font=title_font)
        body_lines = [
            f"镜头：{shot.camera} | 景别：{shot.framing}",
            f"运镜：{shot.camera_movement}",
            f"情绪：{shot.emotion}",
            f"画面：{shot.visual_description[:120]}",
            f"朗读：{(shot.tts_text or '（无台词/无旁白）')[:120]}",
        ]
        y = 240
        for line in body_lines:
            draw.text((90, y), line, fill=(220, 220, 220), font=body_font)
            y += 110
        image.save(path)
        return AssetRecord(
            asset_id=f"image-{shot.shot_id}",
            asset_type="image_placeholder",
            scene_id=shot.scene_id,
            shot_id=shot.shot_id,
            provider=self.provider_name,
            file_path=path,
            prompt=shot.image_prompt,
            duration_seconds=shot.duration_seconds,
        )


class PlaceholderTTSProvider(BaseTTSProvider):
    provider_name = "placeholder-tts"

    def synthesize(self, scene: Scene, shot: Shot, output_dir: str) -> AssetRecord:
        os.makedirs(output_dir, exist_ok=True)
        lines = shot.dialogue or []
        path = os.path.join(output_dir, f"{shot.shot_id}.wav")
        duration = max(shot.duration_seconds, 2.0)
        self._write_silence(path, duration_seconds=duration)
        transcript = (shot.tts_text or " ".join(line.text for line in lines)).strip()
        return AssetRecord(
            asset_id=f"audio-{shot.shot_id}",
            asset_type="audio_placeholder",
            scene_id=shot.scene_id,
            shot_id=shot.shot_id,
            provider=self.provider_name,
            file_path=path,
            prompt=transcript,
            duration_seconds=duration,
            metadata={"transcript": transcript},
        )

    def _write_silence(self, file_path: str, duration_seconds: float, sample_rate: int = 16000) -> None:
        frame_count = int(duration_seconds * sample_rate)
        with wave.open(file_path, "w") as wav_file:
            wav_file.setnchannels(1)
            wav_file.setsampwidth(2)
            wav_file.setframerate(sample_rate)
            wav_file.writeframes(b"\x00\x00" * frame_count)


class PlaceholderVideoProvider(BaseVideoProvider):
    provider_name = "placeholder-video"

    def render(self, shot: Shot, image_asset: AssetRecord, audio_asset: AssetRecord, output_dir: str, target_duration: float | None = None) -> AssetRecord:
        os.makedirs(output_dir, exist_ok=True)
        path = os.path.join(output_dir, f"{shot.shot_id}.json")
        duration = target_duration if target_duration is not None else shot.duration_seconds
        with open(path, "w", encoding="utf-8") as file:
            json.dump(
                {
                    "shot_id": shot.shot_id,
                    "type": "video_placeholder",
                    "video_prompt": shot.video_prompt,
                    "source_image": image_asset.file_path,
                    "source_audio": audio_asset.file_path,
                    "duration_seconds": duration,
                },
                file,
                ensure_ascii=False,
                indent=2,
            )
        return AssetRecord(
            asset_id=f"video-{shot.shot_id}",
            asset_type="video_placeholder",
            scene_id=shot.scene_id,
            shot_id=shot.shot_id,
            provider=self.provider_name,
            file_path=path,
            prompt=shot.video_prompt,
            duration_seconds=duration,
        )


class FFmpegVideoProvider(BaseVideoProvider):
    provider_name = "ffmpeg-video"

    def __init__(self, ffmpeg_binary: str = "ffmpeg") -> None:
        self.ffmpeg_binary = ffmpeg_binary

    def render(self, shot: Shot, image_asset: AssetRecord, audio_asset: AssetRecord, output_dir: str, target_duration: float | None = None) -> AssetRecord:
        if not shutil.which(self.ffmpeg_binary):
            raise RuntimeError("未找到 ffmpeg，可先安装 ffmpeg 并确保在 PATH 中可用")
        os.makedirs(output_dir, exist_ok=True)
        path = os.path.join(output_dir, f"{shot.shot_id}.mp4")
        duration = max(target_duration if target_duration is not None else audio_asset.duration_seconds, 0.5)
        filter_chain = self._build_motion_filter(shot, duration)
        command = [
            self.ffmpeg_binary,
            "-y",
            "-loop",
            "1",
            "-i",
            image_asset.file_path,
            "-i",
            audio_asset.file_path,
            "-t",
            f"{duration:.3f}",
            "-vf",
            filter_chain,
            "-af",
            f"atrim=0:{duration:.3f},asetpts=N/SR/TB,aresample=async=1:first_pts=0",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-c:a",
            "aac",
            "-r",
            "25",
            "-shortest",
            path,
        ]
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="ignore")
        if completed.returncode != 0:
            raise RuntimeError(f"FFmpeg 片段渲染失败：{completed.stderr}")
        return AssetRecord(
            asset_id=f"video-{shot.shot_id}",
            asset_type="video",
            scene_id=shot.scene_id,
            shot_id=shot.shot_id,
            provider=self.provider_name,
            file_path=path,
            prompt=shot.video_prompt,
            duration_seconds=duration,
            metadata={"command": " ".join(command)},
        )

    def _build_motion_filter(self, shot: Shot, duration: float) -> str:
        fps = 25
        total_frames = max(int(duration * fps), 1)
        base = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1"
        movement = shot.camera_movement
        if "环绕" in movement:
            motion = f"zoompan=z='min(1.14,1.0+on/{max(total_frames,1)}*0.14)':x='iw/2-(iw/zoom/2)+sin(on/10)*18':y='ih/2-(ih/zoom/2)+cos(on/12)*12':d={total_frames}:s=1080x1920:fps={fps}"
        elif "平移" in movement or "跟随" in movement:
            motion = f"zoompan=z='1.05':x='(iw-iw/zoom)*(on/{max(total_frames - 1, 1)})':y='ih/2-(ih/zoom/2)':d={total_frames}:s=1080x1920:fps={fps}"
        elif "变焦" in movement or "推进" in movement or "逼近" in movement:
            motion = f"zoompan=z='min(1.16,1.0+on/{max(total_frames,1)}*0.16)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={total_frames}:s=1080x1920:fps={fps}"
        elif "手持" in movement:
            motion = f"zoompan=z='1.08+sin(on/8)*0.01':x='iw/2-(iw/zoom/2)+sin(on/7)*12':y='ih/2-(ih/zoom/2)+cos(on/9)*8':d={total_frames}:s=1080x1920:fps={fps}"
        else:
            motion = f"zoompan=z='1.04':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={total_frames}:s=1080x1920:fps={fps}"
        fade_out_start = max(duration - 0.22, 0)
        fade = f"fade=t=in:st=0:d=0.18,fade=t=out:st={fade_out_start:.3f}:d=0.18"
        return f"{base},{motion},{fade},format=yuv420p"


class JimengVideoProvider(BaseVideoProvider):
    provider_name = "jimeng-video"

    def __init__(self, ffmpeg_binary: str = "ffmpeg") -> None:
        self.endpoint = os.getenv("VOLC_VIDEO_ENDPOINT", "https://visual.volcengineapi.com").strip()
        self.region = os.getenv("VOLC_REGION", "cn-north-1").strip() or "cn-north-1"
        self.service = os.getenv("VOLC_SERVICE", "cv").strip() or "cv"
        self.submit_action = os.getenv("VOLC_VIDEO_ACTION", "CVSync2AsyncSubmitTask").strip() or "CVSync2AsyncSubmitTask"
        self.version = os.getenv("VOLC_VIDEO_VERSION", "2022-08-31").strip() or "2022-08-31"
        self.req_key = os.getenv("VOLC_VIDEO_REQ_KEY", "jimeng_t2v_v30").strip() or "jimeng_t2v_v30"
        self.access_key_id = os.getenv("VOLC_ACCESS_KEY_ID", "").strip()
        self.secret_access_key = os.getenv("VOLC_SECRET_ACCESS_KEY", "").strip()
        self.poll_interval_seconds = max(float(os.getenv("VOLC_VIDEO_POLL_INTERVAL_SECONDS", "3").strip() or "3"), 0.5)
        self.poll_timeout_seconds = max(float(os.getenv("VOLC_VIDEO_POLL_TIMEOUT_SECONDS", "600").strip() or "600"), 10.0)
        self.ffmpeg_binary = ffmpeg_binary

    def render(self, shot: Shot, image_asset: AssetRecord, audio_asset: AssetRecord, output_dir: str, target_duration: float | None = None) -> AssetRecord:
        self._ensure_ready()
        os.makedirs(output_dir, exist_ok=True)
        prompt = (shot.video_prompt or shot.visual_description or shot.image_prompt).strip()
        if len(prompt) > 800:
            prompt = prompt[:800]
        payload = {"req_key": self.req_key, "prompt": prompt}
        response, query_log = self._post_visual_sdk(payload)
        video_bytes, source_url = self._extract_video_bytes(response)
        source_path = os.path.join(output_dir, f"{shot.shot_id}.source.mp4")
        with open(source_path, "wb") as file:
            file.write(video_bytes)
        output_path = os.path.join(output_dir, f"{shot.shot_id}.mp4")
        duration = max(target_duration if target_duration is not None else audio_asset.duration_seconds, 0.5)
        final_duration = self._mux_audio(source_path, audio_asset.file_path, output_path, duration)
        return AssetRecord(
            asset_id=f"video-{shot.shot_id}",
            asset_type="video",
            scene_id=shot.scene_id,
            shot_id=shot.shot_id,
            provider=self.provider_name,
            file_path=output_path,
            prompt=prompt,
            duration_seconds=final_duration,
            metadata={
                "raw_response": self._truncate_for_log(response),
                "query_log": query_log,
                "source_video_url": source_url,
                "source_video_path": source_path,
            },
        )

    def _ensure_ready(self) -> None:
        if not self.access_key_id or not self.secret_access_key:
            raise RuntimeError("缺少 VOLC_ACCESS_KEY_ID 或 VOLC_SECRET_ACCESS_KEY 配置")
        if VisualService is None:
            raise RuntimeError("未安装 volcengine SDK，请先安装 requirements.txt 中的依赖")
        if not shutil.which(self.ffmpeg_binary):
            raise RuntimeError("未找到 ffmpeg，接入即梦 AI 视频后处理需要 ffmpeg")

    def _post_visual_sdk(self, payload: dict, _max_retries: int = 3) -> tuple[dict, dict]:
        visual_service = VisualService()
        visual_service.set_ak(self.access_key_id)
        visual_service.set_sk(self.secret_access_key)
        visual_service.set_host(urllib.parse.urlparse(self.endpoint).netloc or "visual.volcengineapi.com")
        last_error: Exception | None = None
        for attempt in range(_max_retries):
            try:
                with self._without_proxy():
                    submit_response = self._invoke_submit(visual_service, payload)
                    response, query_log = self._wait_for_result(visual_service, payload, submit_response)
                return self._normalize_visual_response(response), query_log
            except Exception as error:
                last_error = error
                if attempt < _max_retries - 1:
                    wait = 5 * (attempt + 1)
                    time.sleep(wait)
        raise RuntimeError(f"即梦视频请求失败（重试{_max_retries}次）：{last_error}") from last_error

    def _normalize_visual_response(self, response: object) -> dict:
        if isinstance(response, str):
            try:
                response = json.loads(response)
            except json.JSONDecodeError:
                raise RuntimeError(f"即梦视频返回非 JSON 内容：{response[:500]}")
        if not isinstance(response, dict):
            raise RuntimeError(f"即梦视频返回类型异常：{type(response).__name__}")
        return response

    def _extract_task_id(self, response: dict) -> str:
        candidates = [
            response.get("task_id"),
            response.get("id"),
            response.get("data", {}).get("task_id") if isinstance(response.get("data"), dict) else None,
            response.get("result", {}).get("task_id") if isinstance(response.get("result"), dict) else None,
        ]
        for candidate in candidates:
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip()
        return ""

    def _is_task_failed(self, response: dict) -> bool:
        values = [
            str(response.get("status", "")).lower(),
            str(response.get("state", "")).lower(),
            str(response.get("message", "")).lower(),
        ]
        for key in ["data", "result"]:
            nested = response.get(key)
            if isinstance(nested, dict):
                values.extend(
                    [
                        str(nested.get("status", "")).lower(),
                        str(nested.get("state", "")).lower(),
                        str(nested.get("message", "")).lower(),
                    ]
                )
        if any(value in {"failed", "fail", "error"} for value in values):
            return True
        code = response.get("code")
        if code not in (None, 0, 10000, 200, "0", "10000", "200"):
            return True
        return False

    def _build_query_log_entry(self, response: dict, index: int) -> dict:
        nested = response.get("data") if isinstance(response.get("data"), dict) else response.get("result") if isinstance(response.get("result"), dict) else {}
        return {
            "index": index,
            "status": response.get("status"),
            "code": response.get("code"),
            "message": response.get("message"),
            "task_status": nested.get("status") if isinstance(nested, dict) else None,
            "task_state": nested.get("state") if isinstance(nested, dict) else None,
            "has_video": self._response_has_video(response),
        }

    def _truncate_for_log(self, value: object, max_length: int = 1200) -> object:
        text = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
        if len(text) <= max_length:
            return value
        return text[:max_length] + "..."

    def _invoke_submit(self, visual_service: VisualService, payload: dict) -> dict:
        if self.submit_action != "CVSync2AsyncSubmitTask":
            raise RuntimeError(f"暂不支持的视频 Action：{self.submit_action}")
        return self._normalize_visual_response(visual_service.cv_sync2async_submit_task(payload))

    def _wait_for_result(self, visual_service: VisualService, payload: dict, submit_response: dict) -> tuple[dict, dict]:
        if self._response_has_video(submit_response):
            return submit_response, {"mode": "submit_contains_result", "poll_count": 0, "task_id": self._extract_task_id(submit_response), "polls": []}
        task_id = self._extract_task_id(submit_response)
        if not task_id:
            raise RuntimeError(f"即梦视频提交成功但未返回 task_id：{json.dumps(submit_response, ensure_ascii=False)[:500]}")
        deadline = time.time() + self.poll_timeout_seconds
        last_response = submit_response
        polls: list[dict] = []
        while time.time() < deadline:
            query_payload = {"req_key": payload.get("req_key"), "task_id": task_id}
            query_response = self._normalize_visual_response(visual_service.cv_sync2async_get_result(query_payload))
            last_response = query_response
            polls.append(self._build_query_log_entry(query_response, len(polls) + 1))
            if self._response_has_video(query_response):
                return query_response, {"mode": "async", "task_id": task_id, "poll_count": len(polls), "polls": polls}
            if self._is_task_failed(query_response):
                raise RuntimeError(f"即梦视频任务失败：{json.dumps(query_response, ensure_ascii=False)[:800]}")
            time.sleep(self.poll_interval_seconds)
        raise RuntimeError(f"即梦视频任务轮询超时 task_id={task_id}，最后响应：{json.dumps(last_response, ensure_ascii=False)[:800]}")

    def _extract_video_bytes(self, response: dict) -> tuple[bytes, str]:
        candidates = []
        if isinstance(response, dict):
            candidates.extend([
                response.get("video_url"),
                response.get("video_urls"),
                response.get("binary_data_base64"),
                response.get("binary_data"),
                response.get("videos"),
                response.get("data"),
                response.get("result"),
            ])
            for key in ["data", "result"]:
                nested = response.get(key)
                if isinstance(nested, dict):
                    candidates.extend([
                        nested.get("video_url"),
                        nested.get("video_urls"),
                        nested.get("binary_data_base64"),
                        nested.get("binary_data"),
                        nested.get("videos"),
                    ])
        for candidate in candidates:
            video_bytes, video_url = self._decode_video_candidate(candidate)
            if video_bytes:
                return video_bytes, video_url
        raise RuntimeError(f"即梦视频响应中未找到视频内容：{json.dumps(response, ensure_ascii=False)[:500]}")

    def _decode_video_candidate(self, candidate: object, _max_retries: int = 3) -> tuple[bytes | None, str]:
        if isinstance(candidate, str):
            if candidate.startswith("http://") or candidate.startswith("https://"):
                last_error: Exception | None = None
                for attempt in range(_max_retries):
                    try:
                        with urllib.request.urlopen(candidate, timeout=180) as response:
                            return response.read(), candidate
                    except Exception as exc:
                        last_error = exc
                        if attempt < _max_retries - 1:
                            time.sleep(2 * (attempt + 1))
                if last_error:
                    raise RuntimeError(f"视频下载失败（重试{_max_retries}次）：{last_error}") from last_error
            try:
                return base64.b64decode(candidate), ""
            except Exception:
                return None, ""
        if isinstance(candidate, list):
            for item in candidate:
                decoded, url = self._decode_video_candidate(item)
                if decoded:
                    return decoded, url
        if isinstance(candidate, dict):
            for key in ["video_url", "url", "binary_data", "binary_data_base64", "video_urls", "videos", "data"]:
                if key in candidate:
                    decoded, url = self._decode_video_candidate(candidate[key])
                    if decoded:
                        return decoded, url
        return None, ""

    def _response_has_video(self, response: dict) -> bool:
        try:
            self._extract_video_bytes(response)
            return True
        except Exception:
            return False

    def _mux_audio(self, source_video_path: str, audio_path: str, output_path: str, duration: float) -> float:
        command = [
            self.ffmpeg_binary,
            "-y",
            "-stream_loop", "-1", "-i", source_video_path,
            "-i", audio_path,
            "-t", f"{duration:.3f}",
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-vf", "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,fps=25,format=yuv420p",
            "-af", f"atrim=0:{duration:.3f},asetpts=N/SR/TB,aresample=async=1:first_pts=0",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-c:a", "aac",
            "-shortest",
            output_path,
        ]
        completed = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="ignore")
        if completed.returncode != 0:
            raise RuntimeError(f"即梦视频音画合成失败：{completed.stderr}")
        return duration

    @contextlib.contextmanager
    def _without_proxy(self):
        proxy_keys = ["HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"]
        original = {key: os.environ.get(key) for key in proxy_keys}
        no_proxy_keys = ["NO_PROXY", "no_proxy"]
        original_no_proxy = {key: os.environ.get(key) for key in no_proxy_keys}
        try:
            for key in proxy_keys:
                os.environ.pop(key, None)
            for key in no_proxy_keys:
                os.environ[key] = "visual.volcengineapi.com,openspeech.bytedance.com,127.0.0.1,localhost"
            yield
        finally:
            for key, value in original.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
            for key, value in original_no_proxy.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
