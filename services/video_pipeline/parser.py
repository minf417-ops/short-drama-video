from __future__ import annotations

import re
from collections import OrderedDict

from .models import CharacterProfile, DialogueLine, Scene, ScriptProject, Shot


class ScriptParser:
    def parse(self, title: str, theme: str, script_text: str, ratio: str = "9:16", resolution: str = "1080x1920") -> ScriptProject:
        scenes = self._parse_scenes(script_text)
        characters = self._collect_characters(scenes)
        self._apply_character_consistency(scenes, characters, theme)
        project_id = self._slugify(title)
        return ScriptProject(
            project_id=project_id,
            title=title or "未命名短剧",
            theme=theme or "未指定主题",
            ratio=ratio,
            resolution=resolution,
            script_text=script_text,
            characters=characters,
            scenes=scenes,
            metadata={"scene_count": len(scenes)},
        )

    def _parse_scenes(self, script_text: str) -> list[Scene]:
        normalized = script_text.replace("\r\n", "\n").replace("\r", "\n").strip()
        matches = list(re.finditer(r"(?:^|\n)\s*(?:【)?场景\s*(\d+)(?:】)?[^\n]*", normalized, flags=re.MULTILINE))
        scenes: list[Scene] = []
        if not matches:
            return [self._build_scene("scene-1", 1, "场景1", normalized)]
        for index, match in enumerate(matches, start=1):
            start = match.start()
            end = matches[index].start() if index < len(matches) else len(normalized)
            block = normalized[start:end].strip()
            heading = block.splitlines()[0].strip()
            scenes.append(self._build_scene(f"scene-{index}", index, heading, block))
        return scenes

    def _build_scene(self, scene_id: str, index: int, heading: str, block: str) -> Scene:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        body_lines = lines[1:] if len(lines) > 1 else []
        dialogue: list[DialogueLine] = []
        summary_parts: list[str] = []
        characters: OrderedDict[str, None] = OrderedDict()
        for line in body_lines:
            dialogue_match = re.match(r"^([\u4e00-\u9fa5A-Za-z0-9_·]+)[：:](.+)$", line)
            if dialogue_match:
                speaker = dialogue_match.group(1).strip()
                text = dialogue_match.group(2).strip()
                dialogue.append(DialogueLine(speaker=speaker, text=text))
                characters.setdefault(speaker, None)
            else:
                summary_parts.append(line)
        mood = self._derive_mood(block)
        location = self._extract_location(heading, block)
        time_of_day = self._extract_time(heading, block)
        environment_details = "；".join(summary_parts[:2]) or "环境具备明确空间层次与光影对比。"
        summary = "；".join(summary_parts[:3]) or "角色推进剧情并发生冲突。"
        shots = self._build_shots(scene_id, summary, list(characters.keys()), dialogue, environment_details, mood)
        return Scene(
            scene_id=scene_id,
            index=index,
            heading=heading,
            location=location,
            time_of_day=time_of_day,
            mood=mood,
            summary=summary,
            environment_details=environment_details,
            characters=list(characters.keys()),
            dialogue=dialogue,
            shots=shots,
        )

    def _build_shots(
        self,
        scene_id: str,
        summary: str,
        characters: list[str],
        dialogue: list[DialogueLine],
        environment_details: str,
        scene_mood: str,
    ) -> list[Shot]:
        shots: list[Shot] = []
        description_segments = self._split_summary_segments(summary)
        movement_plan = ["缓慢推进", "横向平移跟随", "近景轻微环绕", "手持逼近", "快速变焦压迫"]
        framing_plan = ["大全景", "中景双人构图", "近景单人构图", "特写", "情绪特写"]
        camera_plan = ["建立镜头", "关系镜头", "情绪推进镜头", "冲突镜头", "收束镜头"]
        transition_plan = ["淡入", "切入", "硬切", "动作切", "叠化收尾"]
        total_shots = min(max(len(description_segments), 1) + min(len(dialogue), 2), 5)
        for index in range(total_shots):
            dialogue_slice = dialogue[index:index + 1] if index < len(dialogue) else []
            segment = description_segments[min(index, len(description_segments) - 1)] if description_segments else summary
            focal_character = dialogue_slice[0].speaker if dialogue_slice else (characters[min(index, len(characters) - 1)] if characters else "主角")
            visual_description = segment.replace("：", "，")
            expression = self._derive_expression(dialogue_slice[0].text if dialogue_slice else visual_description, scene_mood)
            body_action = self._derive_body_action(visual_description, index)
            shot_id = f"{scene_id}-shot-{index + 1}"
            narration = self._build_narration(index, total_shots, dialogue_slice, visual_description, scene_mood)
            tts_text = dialogue_slice[0].text if dialogue_slice else narration
            shots.append(
                Shot(
                    shot_id=shot_id,
                    scene_id=scene_id,
                    index=index + 1,
                    duration_seconds=3.2 if index < total_shots - 1 else 4.2,
                    visual_description=visual_description,
                    camera=camera_plan[min(index, len(camera_plan) - 1)],
                    framing=framing_plan[min(index, len(framing_plan) - 1)],
                    camera_movement=movement_plan[min(index, len(movement_plan) - 1)],
                    transition=transition_plan[min(index, len(transition_plan) - 1)],
                    emotion=self._derive_emotion(dialogue_slice[0].text if dialogue_slice else visual_description, scene_mood),
                    expression=expression,
                    body_action=body_action,
                    scene_details=environment_details,
                    character_focus=focal_character,
                    characters=characters,
                    dialogue=dialogue_slice,
                    narration=narration,
                    tts_text=tts_text,
                    image_prompt=visual_description,
                    video_prompt=visual_description,
                )
            )
        return shots

    def _collect_characters(self, scenes: list[Scene]) -> list[CharacterProfile]:
        names: OrderedDict[str, None] = OrderedDict()
        for scene in scenes:
            for name in scene.characters:
                if name and 1 < len(name) <= 8:
                    names.setdefault(name, None)
        profiles: list[CharacterProfile] = []
        for index, name in enumerate(names.keys(), start=1):
            appearance, costume, temperament, traits, voice_style = self._build_character_profile(index, name)
            profiles.append(
                CharacterProfile(
                    name=name,
                    traits=traits,
                    voice_style=voice_style,
                    appearance=appearance,
                    costume=costume,
                    temperament=temperament,
                )
            )
        return profiles

    def _apply_character_consistency(self, scenes: list[Scene], characters: list[CharacterProfile], theme: str) -> None:
        appearance_map = {character.name: character for character in characters}
        roster_prompt = "；".join(
            (
                f"{character.name}：外貌{character.appearance}，服装{character.costume}，"
                f"气质{character.temperament}，性格{('、'.join(character.traits) or '克制')}"
            )
            for character in characters
        )
        theme_prompt = theme or "短剧冲突"
        for scene in scenes:
            location_text = f"{scene.location} {scene.time_of_day}".strip()
            for shot in scene.shots:
                active_names = [line.speaker for line in shot.dialogue if line.speaker in appearance_map]
                if not active_names:
                    active_names = [shot.character_focus] if shot.character_focus in appearance_map else [name for name in shot.characters if name in appearance_map][:2]
                active_prompt = "；".join(
                    (
                        f"{name}保持固定形象：外貌{appearance_map[name].appearance}，"
                        f"服装{appearance_map[name].costume}，气质{appearance_map[name].temperament}"
                    )
                    for name in active_names
                ) or "角色形象保持前后一致"
                dialogue_hint = " ".join(line.text for line in shot.dialogue)
                story_focus = dialogue_hint or shot.narration or shot.visual_description or scene.summary
                shot.image_prompt = (
                    f"竖屏短剧电影感分镜，主题：{theme_prompt}。场景：{location_text}，环境细节：{scene.environment_details}。"
                    f"镜头性质：{shot.camera}，景别：{shot.framing}，运镜：{shot.camera_movement}，转场：{shot.transition}。"
                    f"剧情重点：{story_focus}。人物设定：{roster_prompt}。当前焦点：{active_prompt}。"
                    f"人物表情：{shot.expression}。人物动作：{shot.body_action}。"
                    f"要求多人关系、站位、目光方向、服装层次、妆发、肢体动作清晰，画面真实，细节具体，构图稳定，适合后续视频化。"
                )
                shot.video_prompt = (
                    f"竖屏短剧视频镜头，场景{location_text}，环境{scene.environment_details}，"
                    f"焦点人物{shot.character_focus}，景别{shot.framing}，镜头{shot.camera}，运镜{shot.camera_movement}，"
                    f"转场{shot.transition}，情绪{shot.emotion}，表情{shot.expression}，动作{shot.body_action}，"
                    f"剧情表现：{story_focus}。要求多主体动作逻辑清晰，镜头语言强，符合短剧节奏。"
                )
                if shot.dialogue:
                    shot.tts_text = " ".join(line.text for line in shot.dialogue)
                else:
                    shot.tts_text = shot.narration or shot.visual_description

    def _build_narration(self, index: int, total_shots: int, dialogue_slice: list[DialogueLine], visual_description: str, scene_mood: str) -> str:
        if dialogue_slice:
            return ""
        if total_shots <= 1:
            return visual_description
        if index == 0:
            return visual_description
        if index == total_shots - 1 and any(word in scene_mood for word in ["高压", "压抑", "戏剧"]):
            return ""
        if any(word in visual_description for word in ["沉默", "对视", "雨", "灯光", "背影"]):
            return ""
        return visual_description[:36].rstrip("，。；")

    def _build_character_profile(self, index: int, name: str) -> tuple[str, str, str, list[str], str]:
        if index == 1:
            return (
                "二十多岁年轻女性，黑色长发，肤色冷白，眼神清醒锋利，五官精致立体",
                "米色风衣叠穿丝质衬衫，利落高腰长裤，低饱和都市职场配色",
                "冷静克制、压抑中带锋芒的都市精英感",
                ["克制", "敏锐", "有压抑情绪"],
                "female_lead",
            )
        if index == 2:
            return (
                "二十多岁年轻男性，短黑发，眉眼深邃，轮廓利落，身形挺拔",
                "深色西装或长款大衣，层次简洁，质感高级，商务都市风",
                "冷静强势、压迫感明显、情绪内敛",
                ["冷静", "压迫感强", "情绪内敛"],
                "male_lead",
            )
        return (
            "年轻都市配角，外形轮廓清晰，妆发与身份匹配",
            "现代都市穿着，服装设定固定并与剧情身份相符",
            "服务剧情推进，人物关系明确",
            ["推动剧情"],
            "supporting",
        )

    def _derive_mood(self, text: str) -> str:
        if any(word in text for word in ["怒", "恨", "质问", "逼近", "压迫", "冷笑"]):
            return "高压对峙"
        if any(word in text for word in ["雨", "夜", "沉默", "克制"]):
            return "压抑克制"
        return "戏剧化"

    def _derive_emotion(self, text: str, default: str) -> str:
        if any(word in text for word in ["你敢", "为什么", "凭什么", "棋子", "真相"]):
            return "激烈紧绷"
        if any(word in text for word in ["等你", "沉默", "慢慢", "克制"]):
            return "克制压抑"
        return default

    def _derive_expression(self, text: str, mood: str) -> str:
        if any(word in text for word in ["质问", "为什么", "凭什么", "棋子", "两清"]):
            return "眉头收紧，眼神逼视，对方情绪明显绷紧"
        if "压抑" in mood or "克制" in mood:
            return "神情克制，唇角压紧，眼神带压抑情绪"
        return "面部情绪有层次，目光交流明确"

    def _derive_body_action(self, text: str, index: int) -> str:
        if "站" in text:
            return "人物站位明确，一人前景压场，另一人停顿后回应"
        if "推" in text or "递" in text:
            return "手部动作清晰，物件推动形成情绪触发点"
        if "雨" in text:
            return "人物在风雨中对峙，衣摆和发丝有动态"
        return ["缓慢转身", "视线跟随对方", "抬手压住情绪", "短暂停顿后逼近", "动作收束定格"][min(index, 4)]

    def _extract_location(self, heading: str, block: str) -> str:
        label_value = self._extract_tag(block, ["地点"])
        if label_value:
            return label_value
        heading_parts = re.split(r"[：:]", heading, maxsplit=1)
        if len(heading_parts) > 1:
            meta = heading_parts[1]
            tokens = [token for token in re.split(r"\s+", meta.strip()) if token]
            filtered = [token for token in tokens if token not in {"内景", "外景", "日", "夜", "白天", "雨夜"}]
            if filtered:
                return filtered[-1]
        return "未指定地点"

    def _extract_time(self, heading: str, block: str) -> str:
        label_value = self._extract_tag(block, ["时间"])
        if label_value:
            return label_value
        for candidate in ["雨夜", "深夜", "夜", "白天", "日"]:
            if candidate in heading or candidate in block:
                return candidate
        return "未指定时间"

    def _split_summary_segments(self, summary: str) -> list[str]:
        segments = [segment.strip(" ，。；") for segment in re.split(r"[；。]", summary) if segment.strip(" ，。；")]
        return segments[:3] or ["角色沉默对峙，情绪逐渐升高"]

    def _extract_tag(self, block: str, labels: list[str]) -> str:
        for label in labels:
            matched = re.search(rf"{label}[：:]\s*([^\n]+)", block)
            if matched:
                return matched.group(1).strip()
        return ""

    def _slugify(self, value: str) -> str:
        base = re.sub(r"[^A-Za-z0-9\u4e00-\u9fa5]+", "-", value.strip()).strip("-")
        return base.lower() or "drama-project"
