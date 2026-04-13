from __future__ import annotations

import re
from collections import OrderedDict

from .models import CharacterProfile, DialogueLine, Scene, ScriptProject, Shot


class ScriptParser:
    def parse(self, title: str, theme: str, script_text: str, ratio: str = "9:16", resolution: str = "1080x1920") -> ScriptProject:
        scenes = self._parse_scenes(script_text)
        characters = self._collect_characters(scenes, theme)
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
        matches = list(re.finditer(r"(?m)^\s*(?:【场景\s*(\d+)】|场景\s*(\d+))\s*$", normalized))
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
        label_map: OrderedDict[str, str] = OrderedDict()
        for line in body_lines:
            dialogue_match = re.match(r"^([\u4e00-\u9fa5A-Za-z0-9_·&＆（）()，、\s]+)[：:](.+)$", line)
            if dialogue_match:
                speaker = dialogue_match.group(1).strip()
                speaker = self._clean_speaker_name(speaker)
                text = dialogue_match.group(2).strip()
                if self._is_meta_label(speaker):
                    label_map[speaker] = text
                    if speaker in {"对白", "台词", "画外音", "旁白", "OS"}:
                        parsed_dialogue = self._parse_dialogue_text(text)
                        if parsed_dialogue:
                            for item in parsed_dialogue:
                                dialogue.append(item)
                                characters.setdefault(item.speaker, None)
                        elif text:
                            dialogue.append(DialogueLine(speaker="旁白", text=text))
                    elif speaker in {"动作描述", "分镜提示", "镜头提示", "剧情", "剧情重点", "角色造型&情绪"}:
                        if text:
                            summary_parts.append(text)
                else:
                    dialogue.append(DialogueLine(speaker=speaker, text=text))
                    characters.setdefault(speaker, None)
            else:
                summary_parts.append(line)
        mood = self._derive_mood(block)
        location = self._extract_location(heading, block)
        time_of_day = self._extract_time(heading, block)
        environment_details = self._build_environment_details(label_map, summary_parts)
        summary = self._build_scene_summary(label_map, summary_parts, dialogue)
        shots = self._build_shots(scene_id, summary, list(characters.keys()), dialogue, environment_details, mood)
        character_profiles = self._extract_scene_character_profiles(label_map)
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
            character_profiles=character_profiles,
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
        movement_plan = ["缓慢推进", "轻微横移跟随", "稳定近景轻移", "慢速压近", "停驻收束"]
        framing_plan = ["大全景", "中景双人构图", "近景单人构图", "特写", "情绪特写"]
        camera_plan = ["建立镜头", "关系镜头", "情绪推进镜头", "冲突镜头", "收束镜头"]
        viewpoint_plan = ["环境观察视角", "人物对位视角", "角色主观压迫视角", "近身对峙视角", "收束凝视视角"]
        lens_language_plan = ["前景遮挡建立空间层次", "通过对切强化人物关系", "利用浅景深锁定焦点情绪", "通过压缩空间制造冲突压迫", "以停顿和定格形成尾钩"]
        shot_purpose_plan = ["交代时空与人物站位", "建立人物关系与情绪落差", "推进角色内心变化", "放大冲突并制造戏剧爆点", "收束信息并抛出悬念"]
        transition_plan = ["淡入", "切入", "硬切", "动作切", "叠化收尾"]
        if dialogue:
            spoken_lines = [line for line in dialogue if line.text.strip()]
            total_shots = min(max(len(spoken_lines), 1), 6)
        else:
            total_shots = min(max(len(description_segments), 1), 3)
        dialogue_groups = self._chunk_dialogue(dialogue, total_shots)
        for index in range(total_shots):
            dialogue_slice = dialogue_groups[index] if index < len(dialogue_groups) else []
            segment = description_segments[min(index, len(description_segments) - 1)] if description_segments else summary
            focal_character = dialogue_slice[0].speaker if dialogue_slice else (characters[min(index, len(characters) - 1)] if characters else "主角")
            visual_description = segment.replace("：", "，")
            expression = self._derive_expression(dialogue_slice[0].text if dialogue_slice else visual_description, scene_mood, has_dialogue=bool(dialogue_slice))
            body_action = self._derive_body_action(visual_description, index)
            shot_id = f"{scene_id}-shot-{index + 1}"
            narration = self._build_narration(index, total_shots, dialogue_slice, visual_description, scene_mood)
            speaker_gender = self._infer_gender(focal_character, visual_description)
            speaker_age_group = self._infer_age_group(focal_character, visual_description)
            delivery_style = self._derive_delivery_style(dialogue_slice[0].text if dialogue_slice else visual_description, scene_mood, speaker_age_group)
            tts_text = self._build_tts_text(dialogue_slice, delivery_style)
            if dialogue_slice:
                camera = ["对白建立镜头", "稳定人物镜头", "近景人物镜头", "情绪对话镜头", "反应特写镜头", "收束人物镜头"][min(index, 5)]
                framing = ["中景单人构图", "中近景双人构图", "近景单人构图", "近景对话反打", "情绪特写", "收束特写"][min(index, 5)]
                viewpoint = ["人物平视视角", "人物对位视角", "近身平视视角", "肩后反打视角", "人物凝视视角", "收束凝视视角"][min(index, 5)]
                camera_movement = ["固定镜头", "缓慢推进", "轻微横移", "固定近景", "慢速压近", "轻微收束"][min(index, 5)]
                lens_language = ["保持构图稳定并突出说话主体", "用轻推强化情绪变化", "用稳定景别承接对白节奏", "用反打保持视线连续", "锁定嘴部与眼神细节", "以停顿收束尾钩"][min(index, 5)]
                transition = "淡入" if index == 0 else ("叠化收尾" if index == total_shots - 1 else "切入")
                duration_seconds = 2.4 if index < total_shots - 1 else 3.0
            else:
                camera = camera_plan[min(index, len(camera_plan) - 1)]
                framing = framing_plan[min(index, len(framing_plan) - 1)]
                viewpoint = viewpoint_plan[min(index, len(viewpoint_plan) - 1)]
                camera_movement = movement_plan[min(index, len(movement_plan) - 1)]
                lens_language = lens_language_plan[min(index, len(lens_language_plan) - 1)]
                transition = transition_plan[min(index, len(transition_plan) - 1)]
                duration_seconds = 3.0 if index < total_shots - 1 else 3.8
            shots.append(
                Shot(
                    shot_id=shot_id,
                    scene_id=scene_id,
                    index=index + 1,
                    duration_seconds=duration_seconds,
                    visual_description=visual_description,
                    viewpoint=viewpoint,
                    camera=camera,
                    framing=framing,
                    camera_movement=camera_movement,
                    lens_language=lens_language,
                    shot_purpose=shot_purpose_plan[min(index, len(shot_purpose_plan) - 1)],
                    transition=transition,
                    emotion=self._derive_emotion(dialogue_slice[0].text if dialogue_slice else visual_description, scene_mood),
                    expression=expression,
                    body_action=body_action,
                    scene_details=environment_details,
                    character_focus=focal_character,
                    character_identity=self._infer_identity(focal_character, visual_description),
                    speaker_gender=speaker_gender,
                    speaker_age_group=speaker_age_group,
                    delivery_style=delivery_style,
                    characters=characters,
                    dialogue=dialogue_slice,
                    narration=narration,
                    tts_text=tts_text,
                    image_prompt=visual_description,
                    video_prompt=visual_description,
                )
            )
        return shots

    def _chunk_dialogue(self, dialogue: list[DialogueLine], total_shots: int) -> list[list[DialogueLine]]:
        if total_shots <= 0:
            return []
        if not dialogue:
            return [[] for _ in range(total_shots)]
        if len(dialogue) <= total_shots:
            chunks = [[line] for line in dialogue]
            while len(chunks) < total_shots:
                chunks.append([])
            return chunks[:total_shots]
        chunk_size = max((len(dialogue) + total_shots - 1) // total_shots, 1)
        chunks = [dialogue[index:index + chunk_size] for index in range(0, len(dialogue), chunk_size)]
        while len(chunks) < total_shots:
            chunks.append([])
        return chunks[:total_shots]

    def _build_tts_text(self, dialogue_slice: list[DialogueLine], delivery_style: str = "自然克制") -> str:
        if not dialogue_slice:
            return ""
        spoken_parts: list[str] = []
        for line in dialogue_slice:
            speaker = line.speaker.strip()
            text = line.text.strip()
            if not text:
                continue
            if speaker in {"旁白", "画外音", "OS"}:
                spoken_parts.append(text)
            else:
                spoken_parts.append(text)
        transcript = " ".join(part for part in spoken_parts if part)
        return transcript.strip()

    def _is_meta_label(self, speaker: str) -> bool:
        return speaker in {
            "场景号",
            "内外景",
            "时间",
            "地点",
            "角色造型&情绪",
            "角色造型",
            "情绪",
            "动作描述",
            "对白",
            "台词",
            "分镜提示",
            "镜头提示",
            "剧情",
            "剧情重点",
            "旁白",
            "画外音",
            "OS",
        }

    def _parse_dialogue_text(self, text: str) -> list[DialogueLine]:
        normalized = text.strip()
        if not normalized:
            return []
        matches = list(re.finditer(r"([\u4e00-\u9fa5A-Za-z0-9_\u00b7\uff08\uff09()\uff0c\u3001\uff1b\s]+?)[\uff1a:][\u201c\u201d\"]?(.+?)(?=(?:\s+[\u4e00-\u9fa5A-Za-z0-9_\u00b7\uff08\uff09()\uff0c\u3001\uff1b\s]+?[\uff1a:])|$)", normalized))
        parsed: list[DialogueLine] = []
        for match in matches:
            speaker = self._clean_speaker_name(match.group(1).strip())
            content = match.group(2).strip().strip('"“”')
            if speaker and content and not self._is_meta_label(speaker):
                parsed.append(DialogueLine(speaker=speaker, text=content))
        if parsed:
            return parsed
        fallback = normalized.strip('"“”')
        return [DialogueLine(speaker="旁白", text=fallback)] if fallback else []

    def _build_environment_details(self, label_map: OrderedDict[str, str], summary_parts: list[str]) -> str:
        parts = [
            label_map.get("内外景", "").strip(),
            label_map.get("地点", "").strip(),
            label_map.get("角色造型&情绪", "").strip(),
        ]
        parts.extend(summary_parts[:1])
        filtered = [part for part in parts if part]
        return "；".join(filtered[:3]) or "环境具备明确空间层次与光影对比。"

    def _clean_speaker_name(self, speaker: str) -> str:
        cleaned = re.sub(r"[（(].*?[）)]", "", speaker or "").strip()
        return cleaned

    def _build_scene_summary(self, label_map: OrderedDict[str, str], summary_parts: list[str], dialogue: list[DialogueLine]) -> str:
        role_state = label_map.get("角色造型&情绪", "").strip() or label_map.get("角色造型", "").strip()
        action = label_map.get("动作描述", "").strip() or label_map.get("剧情", "").strip() or label_map.get("剧情重点", "").strip()
        camera = label_map.get("分镜提示", "").strip() or label_map.get("镜头提示", "").strip()
        dialogue_summary = " ".join(f"{line.speaker}：{line.text}" for line in dialogue[:2] if line.text.strip())
        fallback = "；".join(part.strip(" ，。；") for part in summary_parts[:2] if part.strip(" ，。；"))
        merged_parts = [part for part in [role_state, action, dialogue_summary, camera, fallback] if part]
        if not merged_parts:
            return "角色推进剧情并发生冲突。"
        dense = "。".join(part.strip("。； ") for part in merged_parts[:4] if part.strip("。； "))
        return dense.strip("。； ") + "。"

    def _collect_characters(self, scenes: list[Scene], theme: str) -> list[CharacterProfile]:
        names: OrderedDict[str, None] = OrderedDict()
        mention_scores: dict[str, int] = {}
        scene_profile_map: dict[str, dict[str, str]] = {}
        for scene in scenes:
            scene_profile_map.update(scene.character_profiles)
            for name in scene.characters:
                if name and 1 < len(name) <= 8:
                    names.setdefault(name, None)
                    mention_scores[name] = mention_scores.get(name, 0) + 3
            for line in scene.dialogue:
                speaker = line.speaker.strip()
                if speaker and 1 < len(speaker) <= 8:
                    names.setdefault(speaker, None)
                    mention_scores[speaker] = mention_scores.get(speaker, 0) + 2
        profiles: list[CharacterProfile] = []
        ordered_names = sorted(names.keys(), key=lambda item: (-mention_scores.get(item, 0), list(names.keys()).index(item)))
        role_map = self._infer_character_roles(ordered_names)
        for index, name in enumerate(ordered_names, start=1):
            scene_profile = scene_profile_map.get(name, {})
            appearance, costume, temperament, traits, voice_style, gender, age_group, identity, speech_style = self._build_character_profile(
                index,
                name,
                role_map.get(name, "supporting"),
                scene_profile,
                theme,
            )
            profiles.append(
                CharacterProfile(
                    name=name,
                    traits=traits,
                    voice_style=voice_style,
                    gender=gender,
                    age_group=age_group,
                    identity=identity,
                    speech_style=speech_style,
                    appearance=appearance,
                    costume=costume,
                    temperament=temperament,
                )
            )
        return profiles

    def _infer_character_roles(self, ordered_names: list[str]) -> dict[str, str]:
        female_markers = ["晚", "夏", "薇", "晴", "瑶", "宁", "雪", "柔", "雅", "娜", "琳", "颖", "婷", "倩", "姝", "姐", "妈", "桃", "兰", "梅", "莲", "萍", "芳", "秀", "玲", "丽", "燕", "蓉", "珍", "翠"]
        male_markers = ["川", "默", "泽", "辰", "凯", "邦", "晏", "骁", "霆", "宸", "骏", "峰", "叔", "爷", "父", "哥", "军", "刚", "强", "建", "伟", "磊", "勇", "鹏", "涛", "斌", "龙", "武", "国"]
        female_candidates = [name for name in ordered_names if any(marker in name for marker in female_markers)]
        male_candidates = [name for name in ordered_names if any(marker in name for marker in male_markers)]
        role_map: dict[str, str] = {name: "supporting" for name in ordered_names}
        if female_candidates:
            role_map[female_candidates[0]] = "female_lead"
        if male_candidates:
            role_map[male_candidates[0]] = "male_lead"
        if not female_candidates and ordered_names:
            role_map[ordered_names[0]] = "female_lead"
        if not male_candidates:
            for name in ordered_names:
                if role_map.get(name) != "female_lead":
                    role_map[name] = "male_lead"
                    break
        return role_map

    def _apply_character_consistency(self, scenes: list[Scene], characters: list[CharacterProfile], theme: str) -> None:
        appearance_map = {character.name: character for character in characters}
        theme_prompt = theme or "短剧冲突"
        for scene in scenes:
            location_text = f"{scene.location} {scene.time_of_day}".strip()
            for shot in scene.shots:
                active_names = [line.speaker for line in shot.dialogue if line.speaker in appearance_map]
                if not active_names:
                    active_names = [shot.character_focus] if shot.character_focus in appearance_map else [name for name in shot.characters if name in appearance_map][:2]
                active_prompt = "；".join(
                    (
                        f"{name}保持固定形象：身份{appearance_map[name].identity or '未明确'}，"
                        f"性别{appearance_map[name].gender}，年龄层{appearance_map[name].age_group}，"
                        f"外貌{appearance_map[name].appearance}，服装{appearance_map[name].costume}，"
                        f"气质{appearance_map[name].temperament}，说话方式{appearance_map[name].speech_style}"
                    )
                    for name in active_names
                ) or "角色形象保持前后一致"
                dialogue_hint = " ".join(line.text for line in shot.dialogue)
                story_focus = dialogue_hint or shot.narration or shot.visual_description or scene.summary
                dense_video_paragraph = self._build_dense_video_paragraph(
                    scene,
                    shot,
                    active_names,
                    story_focus,
                    appearance_map,
                    active_prompt,
                )
                focal_profile = appearance_map.get(shot.character_focus)
                if focal_profile:
                    shot.character_identity = focal_profile.identity or shot.character_identity
                    shot.speaker_gender = focal_profile.gender or shot.speaker_gender
                    shot.speaker_age_group = focal_profile.age_group or shot.speaker_age_group
                    shot.delivery_style = focal_profile.speech_style or shot.delivery_style
                shot.image_prompt = (
                    f"4:3画幅短剧电影感空镜背景，主题：{theme_prompt}。场景：{location_text}，环境细节：{scene.environment_details}。"
                    f"镜头性质：{shot.camera}，景别：{shot.framing}，视角：{shot.viewpoint}，运镜：{shot.camera_movement}，转场：{shot.transition}。"
                    f"剧情氛围：{story_focus}。"
                    f"要求只生成场景背景与环境布置，不出现人物、不出现人脸、不出现人体、不出现手臂、不出现剪影、"
                    f"不出现倒影中的人物，不出现多人关系。突出空间结构、景深、灯光、道具、前后景层次、构图稳定，"
                    f"作为后续视频生成的场景底图参考。"
                )
                shot.video_prompt = dense_video_paragraph
                if shot.dialogue:
                    shot.tts_text = self._build_tts_text(shot.dialogue, shot.delivery_style)
                else:
                    shot.tts_text = ""

    def _build_dense_video_paragraph(
        self,
        scene: Scene,
        shot: Shot,
        active_names: list[str],
        story_focus: str,
        appearance_map: dict[str, CharacterProfile],
        active_prompt: str,
    ) -> str:
        active_characters = "；".join(name for name in active_names if name) or (shot.character_focus or "主角")
        visual_anchors = "；".join(
            self._build_character_visual_anchor(appearance_map[name], include_voice=False)
            for name in active_names
            if name in appearance_map
        ) or active_prompt
        beat_prompt = self._build_shot_beats(shot, story_focus)
        if shot.dialogue:
            dialogue_text = shot.dialogue[0].text.strip() if shot.dialogue else ""
            sync_requirement = (
                f"【核心动作】{shot.character_focus or active_characters}正在开口说话，"
                f"嘴部清晰地说出「{dialogue_text[:30]}」，嘴唇开合幅度与中文发音节奏一致，下巴自然活动。"
                "仅一人说话，不要多人同时张嘴，"
                "镜头保持稳定或仅轻微推进，避免突兀甩镜、快速变焦和夸张肢体抖动。"
            )
        else:
            sync_requirement = "当前镜头以动作和气氛推进为主，运动平顺自然，避免无意义抖动和突然卡顿。"
        if shot.dialogue:
            state_desc = f"角色正在说话，{shot.expression}，身体与手部动作是{shot.body_action}"
        else:
            state_desc = f"角色状态是{shot.emotion}，表情细节为{shot.expression}，身体与手部动作是{shot.body_action}"
        return (
            f"4:3画幅短剧电影镜头。地点在{scene.location}，时间为{scene.time_of_day}，环境包含{scene.environment_details}。"
            f"当前镜头聚焦{active_characters}，其中核心人物是{shot.character_focus}，身份为{shot.character_identity or '剧情核心人物'}，"
            f"人物连续性锚点：{visual_anchors}。"
            f"{state_desc}。"
            f"剧情推进为：{story_focus}。"
            f"镜头节奏按拍推进：{beat_prompt}。"
            f"镜头采用{shot.camera}，景别为{shot.framing}，视角为{shot.viewpoint}，运镜为{shot.camera_movement}，"
            f"前后景关系和镜头语言为{shot.lens_language}，转场方式是{shot.transition}。"
            f"{sync_requirement}"
            "先交代主体与站位，再交代镜头观察方式，再交代动作如何在时间中展开，最后补充氛围与真实感锚点。"
            "要求人物发型、服装层次、饰品、法器、环境、动作和空间关系连续稳定，"
            "同一角色在前后镜头保持同一套造型设定，画面自然流畅，不要截肢，不要异常裁切，不要让角色凭空闪变。"
        )

    def _build_character_visual_anchor(self, profile: CharacterProfile, include_voice: bool = True) -> str:
        parts = [profile.name]
        if profile.identity:
            parts.append(f"身份{profile.identity}")
        if profile.appearance:
            parts.append(f"外貌{profile.appearance}")
        if profile.costume:
            parts.append(f"服装{profile.costume}")
        if profile.temperament:
            parts.append(f"气质{profile.temperament}")
        if include_voice and profile.speech_style:
            parts.append(f"说话方式{profile.speech_style}")
        return "，".join(part for part in parts if part)

    def _build_shot_beats(self, shot: Shot, story_focus: str) -> str:
        focus = shot.character_focus or "主角"
        if shot.dialogue:
            line_text = shot.dialogue[0].text.strip() if shot.dialogue else shot.tts_text.strip()
            return (
                f"{focus}从视频第一帧起就在开口说话，"
                f"嘴部从头到尾清晰地说出[{line_text[:18]}]，"
                f"说话贯穿整个镜头，说完后短暂停顿收住情绪。"
            )
        return (
            f"第一拍建立场景与人物站位，第二拍推进{story_focus[:24]}，"
            "第三拍用停顿、眼神或环境反应收束悬念。"
        )

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

    def _build_character_profile(self, index: int, name: str, role: str, scene_profile: dict[str, str], theme: str) -> tuple[str, str, str, list[str], str, str, str, str, str]:
        gender = scene_profile.get("gender") or self._infer_gender(name, scene_profile.get("raw", ""))
        age_group = scene_profile.get("age_group") or self._infer_age_group(name, scene_profile.get("raw", ""))
        identity = scene_profile.get("identity") or self._infer_identity(name, scene_profile.get("raw", ""))
        speech_style = scene_profile.get("speech_style") or self._infer_speech_style(name, scene_profile.get("raw", ""))
        is_xianxia = any(keyword in (theme or "") for keyword in ["修仙", "仙侠", "仙门", "宗门", "飞升", "灵气"])
        if role == "female_lead":
            default_appearance = "二十出头的年轻女修，黑发如墨，肤色冷白，眼尾锋利，发间常配玉簪或金饰，眉眼里压着不肯低头的狠意" if is_xianxia else "二十多岁年轻女性，黑色长发，肤色冷白，眼神清醒锋利，五官精致立体"
            default_costume = "赤金或墨色法袍层层叠穿，外覆轻纱或斗篷，衣料带暗纹与流光，腰间悬玉佩、符囊或法器" if is_xianxia else "米色风衣叠穿丝质衬衫，利落高腰长裤，低饱和都市职场配色"
            default_temperament = "冷冽克制，压着滔天情绪，却始终不失锋芒与威压" if is_xianxia else "冷静克制、压抑中带锋芒的都市精英感"
            return (
                scene_profile.get("appearance") or default_appearance,
                scene_profile.get("costume") or default_costume,
                scene_profile.get("temperament") or default_temperament,
                self._merge_traits(["克制", "敏锐", "有压抑情绪"] if not is_xianxia else ["冷冽", "隐忍", "锋利"], scene_profile.get("temperament", "")),
                "female_lead",
                gender,
                age_group,
                identity,
                speech_style,
            )
        if role == "male_lead":
            default_appearance = "二十多岁的年轻男修，墨发高束，眉眼深冷，轮廓凌厉，身形挺拔，目光压着情绪不外露" if is_xianxia else "二十多岁年轻男性，短黑发，眉眼深邃，轮廓利落，身形挺拔"
            default_costume = "玄色或雪色长袍外覆大氅，衣摆与护腕有宗门纹样，佩剑或法器不离身" if is_xianxia else "深色西装或长款大衣，层次简洁，质感高级，商务都市风"
            default_temperament = "克制冷硬，压迫感强，像随时会出剑却又强行收住" if is_xianxia else "冷静强势、压迫感明显、情绪内敛"
            return (
                scene_profile.get("appearance") or default_appearance,
                scene_profile.get("costume") or default_costume,
                scene_profile.get("temperament") or default_temperament,
                self._merge_traits(["冷静", "压迫感强", "情绪内敛"] if not is_xianxia else ["冷硬", "克制", "压迫感强"], scene_profile.get("temperament", "")),
                "male_lead",
                gender,
                age_group,
                identity,
                speech_style,
            )
        default_appearance = "修仙配角，轮廓鲜明，妆发与修为、宗门身份相配" if is_xianxia else "年轻都市配角，外形轮廓清晰，妆发与身份匹配"
        default_costume = "长袍、法袍、斗篷或侍从服饰固定，配色与阵营身份清晰" if is_xianxia else "现代都市穿着，服装设定固定并与剧情身份相符"
        default_temperament = "服务冲突推进，立场鲜明，气息和阵营明确" if is_xianxia else "服务剧情推进，人物关系明确"
        return (
            scene_profile.get("appearance") or default_appearance,
            scene_profile.get("costume") or default_costume,
            scene_profile.get("temperament") or default_temperament,
            self._merge_traits(["推动剧情"] if not is_xianxia else ["立场鲜明", "服务冲突"], scene_profile.get("temperament", "")),
            "supporting",
            gender,
            age_group,
            identity,
            speech_style,
        )

    def _extract_scene_character_profiles(self, label_map: OrderedDict[str, str]) -> dict[str, dict[str, str]]:
        raw = label_map.get("角色造型&情绪", "").strip() or label_map.get("角色造型", "").strip()
        if not raw:
            return {}
        result: dict[str, dict[str, str]] = {}
        segments = [segment.strip() for segment in re.split(r"(?:[；;\n]|。(?=[\u4e00-\u9fa5A-Z]))", raw) if segment.strip()]
        for segment in segments:
            match = re.match(r"^([\u4e00-\u9fa5A-Za-z0-9_·]{2,8})[：:]?(.*)$", segment)
            if not match:
                continue
            name = match.group(1).strip()
            detail = match.group(2).strip(" ，。；")
            result[name] = {
                "raw": detail,
                "gender": self._infer_gender(name, detail),
                "age_group": self._infer_age_group(name, detail),
                "identity": self._infer_identity(name, detail),
                "speech_style": self._infer_speech_style(name, detail),
                "appearance": self._extract_appearance(detail),
                "costume": self._extract_costume(detail),
                "temperament": self._extract_phrase(detail, ["气质", "情绪", "神情", "状态", "克制", "冷静", "强势", "温柔", "狠厉", "慌乱", "羞涩", "紧张", "雀跃"]),
            }
        return result

    def _infer_gender(self, name: str, text: str) -> str:
        combined = f"{name}{text}"
        female_keywords = ["女", "她", "姐姐", "小姐", "夫人", "公主", "母亲", "妈妈", "女孩", "女生", "闺蜜", "新娘", "丫鬟", "侍女", "姑娘", "小娘子", "嫂子"]
        male_keywords = ["男", "他", "哥哥", "少爷", "先生", "父亲", "爸爸", "男孩", "男生", "新郎", "总裁", "老板", "将军", "大人", "师傅", "师父"]
        if any(keyword in combined for keyword in female_keywords):
            return "female"
        if any(keyword in combined for keyword in male_keywords):
            return "male"
        if any(marker in name for marker in ["晚", "夏", "薇", "晴", "瑶", "宁", "雪", "柔", "雅", "娜", "琳", "颖", "婷", "倩", "姝", "桃", "兰", "梅", "莲", "萍", "芳", "秀", "玲", "丽", "燕", "蓉", "珍", "翠"]):
            return "female"
        if any(marker in name for marker in ["川", "默", "泽", "辰", "凯", "邦", "晏", "骁", "霆", "宸", "骏", "峰", "军", "刚", "强", "建", "伟", "磊", "勇", "鹏", "涛", "斌", "龙", "武", "国"]):
            return "male"
        return "unknown"

    def _infer_age_group(self, name: str, text: str) -> str:
        combined = f"{name}{text}"
        if any(keyword in combined for keyword in ["小女孩", "小男孩", "孩子", "孩童", "幼年"]):
            return "child"
        age_match = re.search(r"(\d{1,2})\s*岁", combined)
        if age_match:
            age_num = int(age_match.group(1))
            if age_num < 14:
                return "child"
            if age_num < 25:
                return "young_adult"
            if age_num < 35:
                return "adult"
            if age_num < 60:
                return "middle_aged"
            return "elder"
        if any(keyword in combined for keyword in ["少女", "少年", "高中", "大学生", "实习生", "二十出头", "学徒"]):
            return "young_adult"
        if any(keyword in combined for keyword in ["中年", "经理", "总监", "母亲", "父亲", "三十多", "四十岁", "阿姨", "大婶"]):
            return "middle_aged"
        if any(keyword in combined for keyword in ["老太", "老者", "老人", "奶奶", "爷爷"]):
            return "elder"
        return "adult"

    def _infer_identity(self, name: str, text: str) -> str:
        combined = f"{name}{text}"
        identity_keywords = ["宗主", "掌门", "长老", "真君", "真人", "仙尊", "魔尊", "弟子", "圣女", "道侣", "师尊", "师兄", "师姐", "少主", "护法", "剑修", "医修", "丹修", "器修", "总裁", "医生", "律师", "助理", "保姆", "秘书", "学生", "校花", "校霸", "警察", "记者", "皇后", "公主", "王爷", "太子", "母亲", "父亲", "前任", "未婚妻", "老板"]
        for keyword in identity_keywords:
            if keyword in combined:
                return keyword
        return "剧情关键人物"

    def _infer_speech_style(self, name: str, text: str) -> str:
        combined = f"{name}{text}"
        if any(keyword in combined for keyword in ["冷", "压迫", "强势", "锋利", "狠"]):
            return "冷峻压迫"
        if any(keyword in combined for keyword in ["温柔", "轻声", "安抚", "宠", "柔"]):
            return "温柔克制"
        if any(keyword in combined for keyword in ["哭腔", "哽咽", "崩溃", "慌乱", "颤抖"]):
            return "情绪颤抖"
        if any(keyword in combined for keyword in ["少年", "活泼", "俏皮", "嘴硬"]):
            return "年轻利落"
        return "自然克制"

    def _derive_delivery_style(self, text: str, mood: str, age_group: str) -> str:
        if any(word in text for word in ["你敢", "闭嘴", "住手", "凭什么", "马上"]):
            return "强压爆发"
        if any(word in text for word in ["求你", "不要", "别走", "我害怕"]):
            return "脆弱哽咽"
        if "压抑" in mood or "克制" in mood:
            return "低沉克制"
        if age_group == "young_adult":
            return "年轻利落"
        return "自然克制"

    def _merge_traits(self, base_traits: list[str], extra_text: str) -> list[str]:
        traits = list(base_traits)
        for candidate in [segment.strip() for segment in re.split(r"[，、； ]", extra_text) if segment.strip()]:
            if candidate not in traits and len(candidate) <= 8:
                traits.append(candidate)
        return traits[:6]

    def _extract_appearance(self, text: str) -> str:
        appearance_parts: list[str] = []
        age_match = re.search(r"\d{1,2}岁", text)
        if age_match:
            appearance_parts.append(age_match.group(0))
        for anchor in ["梳", "长发", "短发", "黑发", "白发", "卷发", "麻花辫", "马尾", "发髻", "光头"]:
            phrase = self._extract_phrase(text, [anchor])
            if phrase:
                appearance_parts.append(phrase)
                break
        for anchor in ["肤", "脸", "眼", "眉", "鼻", "唇", "五官", "轮廓", "身形", "身材", "挺拔", "消瘦", "壮实"]:
            phrase = self._extract_phrase(text, [anchor])
            if phrase and phrase not in appearance_parts:
                appearance_parts.append(phrase)
                if len(appearance_parts) >= 4:
                    break
        return "，".join(appearance_parts) if appearance_parts else ""

    def _extract_costume(self, text: str) -> str:
        costume_parts: list[str] = []
        for anchor in ["穿", "身着", "一袭", "戴", "披", "法袍", "道袍", "仙裙", "广袖", "斗篷", "护腕", "发冠", "金冠", "玉簪", "佩剑", "符囊", "西装", "风衣", "校服", "长裙", "盔甲", "工装", "制服", "军便服", "衬衫", "围裙", "解放鞋", "布鞋", "大衣", "旗袍"]:
            phrase = self._extract_phrase(text, [anchor])
            if phrase and phrase not in costume_parts:
                costume_parts.append(phrase)
                if len(costume_parts) >= 3:
                    break
        return "，".join(costume_parts) if costume_parts else ""

    def _extract_first_match(self, text: str, candidates: list[str]) -> str:
        for candidate in candidates:
            if candidate in text:
                return candidate
        return ""

    def _extract_phrase(self, text: str, anchors: list[str]) -> str:
        for anchor in anchors:
            match = re.search(rf"([^，。；]*{re.escape(anchor)}[^，。；]*)", text)
            if match:
                return match.group(1).strip()
        return ""

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

    def _derive_expression(self, text: str, mood: str, has_dialogue: bool = False) -> str:
        if has_dialogue:
            if any(word in text for word in ["质问", "为什么", "凭什么", "棋子", "两清"]):
                return "眉头微锁，嘴部随对白节奏开合，语气逼人，目光直视对方"
            if any(word in text for word in ["哭", "哽咽", "求你", "别走"]):
                return "眼眶泛红，嘴唇微颤，随对白开合说话，声线带哭腔"
            if "压抑" in mood or "克制" in mood:
                return "神情克制但嘴部自然开合说出台词，眼神压着情绪，语速沉稳"
            return "表情自然，嘴部随台词节奏清晰开合，目光交流明确"
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
