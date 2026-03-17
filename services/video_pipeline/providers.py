from __future__ import annotations

import base64
import contextlib
import json
import struct
import re
import os
import shutil
import subprocess
import urllib.error
import urllib.parse
import urllib.request
import uuid
import wave
from abc import ABC, abstractmethod

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
        self.req_key = os.getenv("VOLC_IMAGE_REQ_KEY", "").strip()
        self.access_key_id = os.getenv("VOLC_ACCESS_KEY_ID", "").strip()
        self.secret_access_key = os.getenv("VOLC_SECRET_ACCESS_KEY", "").strip()

    def generate(self, shot: Shot, output_dir: str) -> AssetRecord:
        self._ensure_ready()
        os.makedirs(output_dir, exist_ok=True)
        prompt, response = self._generate_with_fallback(shot)
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
            metadata={"raw_response": response, "original_prompt": shot.image_prompt},
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
                    response.get("data"),
                    response.get("image"),
                    response.get("images"),
                    response.get("result"),
                    response.get("binary_data_base64"),
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

    def _post_visual_sdk(self, payload: dict) -> dict:
        visual_service = VisualService()
        visual_service.set_ak(self.access_key_id)
        visual_service.set_sk(self.secret_access_key)
        try:
            with self._without_proxy():
                response = visual_service.cv_process(payload)
        except Exception as error:
            raise RuntimeError(f"文生图请求失败：{error}") from error
        if isinstance(response, str):
            try:
                return json.loads(response)
            except json.JSONDecodeError:
                raise RuntimeError(f"文生图返回非 JSON 内容：{response[:500]}")
        if not isinstance(response, dict):
            raise RuntimeError(f"文生图返回类型异常：{type(response).__name__}")
        return response

    def _generate_with_fallback(self, shot: Shot) -> tuple[str, dict]:
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
                return prompt, self._post_visual_sdk(payload)
            except RuntimeError as error:
                last_error = error
                if "50511" not in str(error) and "Risk Not Pass" not in str(error):
                    break
        if last_error:
            raise last_error
        raise RuntimeError("文生图生成失败，未获得可用响应")

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
        characters = "、".join(shot.characters[:2]) or "男女主"
        return (
            f"竖屏都市短剧电影感画面，{characters}在现代室内场景中交流，"
            f"镜头{shot.camera}，情绪{shot.emotion}，人物形象固定一致，"
            "服装统一，真实人像，构图稳定，光线自然。"
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
        self.resource_id = os.getenv("VOLC_TTS_RESOURCE_ID", "seed-tts-1.0").strip()
        self.voice = os.getenv("VOLC_TTS_VOICE", "").strip()
        self.narrator_voice = os.getenv("VOLC_TTS_VOICE_NARRATOR", self.voice).strip()
        self.male_lead_voice = os.getenv("VOLC_TTS_VOICE_MALE_LEAD", self.voice).strip()
        self.female_lead_voice = os.getenv("VOLC_TTS_VOICE_FEMALE_LEAD", self.voice).strip()
        self.encoding = os.getenv("VOLC_TTS_ENCODING", "wav").strip()
        self.speed_ratio = float(os.getenv("VOLC_TTS_SPEED_RATIO", "1.0").strip() or "1.0")

    def synthesize(self, scene: Scene, shot: Shot, output_dir: str) -> AssetRecord:
        self._ensure_ready()
        os.makedirs(output_dir, exist_ok=True)
        lines = shot.dialogue or [DialogueLine(speaker="旁白", text=shot.tts_text or shot.narration or scene.summary)]
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
        response = self._request_tts(lines, tts_text)
        audio_bytes = self._extract_audio_bytes(response)
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
            metadata={"transcript": tts_text, "raw_response": response},
        )

    def _request_tts(self, lines: list[DialogueLine], transcript: str) -> dict:
        selected_voice = self._select_voice(lines)
        fallback_voices: list[str | None] = []
        for candidate in [selected_voice, self.voice, self.narrator_voice, None]:
            if candidate not in fallback_voices:
                fallback_voices.append(candidate)
        last_error: RuntimeError | None = None
        for candidate in fallback_voices:
            payload = {
                "user": {
                    "uid": str(uuid.uuid4()),
                },
                "req_params": {
                    "text": transcript,
                    "speaker": candidate,
                    "audio_params": {
                        "format": self.encoding,
                        "speed_ratio": self.speed_ratio,
                    },
                },
            }
            if not candidate:
                payload["req_params"].pop("speaker")
            response = self._post_json(self.url, payload, self._headers())
            code = response.get("code") if isinstance(response, dict) else None
            message = str(response.get("message", "")) if isinstance(response, dict) else ""
            if code in (0, 20000000, None):
                try:
                    self._extract_audio_bytes(response)
                    return response
                except RuntimeError as error:
                    last_error = error
                    continue
            last_error = RuntimeError(f"TTS 返回异常 code={code}, message={message}")
            if "mismatched" not in message.lower() and "speaker" not in message.lower():
                break
        if last_error:
            raise last_error
        raise RuntimeError("TTS 请求失败，未获取到有效音频")

    def _select_voice(self, lines: list[DialogueLine]) -> str:
        speakers = [line.speaker.strip() for line in lines if line.speaker.strip()]
        if not speakers:
            return self.narrator_voice or self.voice
        first_speaker = speakers[0]
        if "旁白" in first_speaker:
            return self.narrator_voice or self.voice
        if "男主" in first_speaker:
            return self.male_lead_voice or self.voice
        if "女主" in first_speaker:
            return self.female_lead_voice or self.voice
        if "林晚" in first_speaker:
            return self.female_lead_voice or self.voice or self.narrator_voice
        if "顾川" in first_speaker:
            return self.male_lead_voice or self.voice or self.narrator_voice
        return self.voice or self.narrator_voice

    def _headers(self) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "X-Api-App-Id": self.app_id,
            "X-Api-Access-Key": self.access_key,
            "X-Api-Resource-Id": self.resource_id,
        }

    def _ensure_ready(self) -> None:
        if not self.app_id or not self.access_key:
            raise RuntimeError("缺少 VOLC_TTS_APP_ID 或 VOLC_TTS_ACCESS_KEY 配置")

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
        return fallback


class PlaceholderImageProvider(BaseImageProvider):
    provider_name = "placeholder-image"

    def generate(self, shot: Shot, output_dir: str) -> AssetRecord:
        os.makedirs(output_dir, exist_ok=True)
        path = os.path.join(output_dir, f"{shot.shot_id}.json")
        with open(path, "w", encoding="utf-8") as file:
            json.dump(
                {
                    "shot_id": shot.shot_id,
                    "type": "image_placeholder",
                    "prompt": shot.image_prompt,
                    "visual_description": shot.visual_description,
                },
                file,
                ensure_ascii=False,
                indent=2,
            )
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
        lines = shot.dialogue or [DialogueLine(speaker="旁白", text=shot.tts_text or shot.narration or scene.summary)]
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
            motion = f"zoompan=z='min(1.18,1.0+on/{max(total_frames,1)}*0.18)':x='iw/2-(iw/zoom/2)+sin(on/8)*28':y='ih/2-(ih/zoom/2)+cos(on/10)*18':d={total_frames}:s=1080x1920:fps={fps}"
        elif "平移" in movement or "跟随" in movement:
            motion = f"zoompan=z='1.08':x='(iw-iw/zoom)*(on/{max(total_frames - 1, 1)})':y='ih/2-(ih/zoom/2)':d={total_frames}:s=1080x1920:fps={fps}"
        elif "变焦" in movement or "推进" in movement or "逼近" in movement:
            motion = f"zoompan=z='min(1.22,1.0+on/{max(total_frames,1)}*0.22)':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={total_frames}:s=1080x1920:fps={fps}"
        elif "手持" in movement:
            motion = f"zoompan=z='1.1+sin(on/6)*0.015':x='iw/2-(iw/zoom/2)+sin(on/5)*20':y='ih/2-(ih/zoom/2)+cos(on/7)*14':d={total_frames}:s=1080x1920:fps={fps}"
        else:
            motion = f"zoompan=z='1.06':x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d={total_frames}:s=1080x1920:fps={fps}"
        fade_out_start = max(duration - 0.35, 0)
        fade = f"fade=t=in:st=0:d=0.24,fade=t=out:st={fade_out_start:.3f}:d=0.3"
        return f"{base},{motion},{fade},format=yuv420p"
