import re

from .schemas import UserRequest, PlanOutline, ScriptDraft
from services.llm_service import LLMService


class WriterAgent:
    """根据 Planner 大纲生成正式剧本。

    当前 Writer 的目标不只是写出“能看”的文本，还要尽量输出更适合后续视频解析的
    场景结构，方便下游拆成 Scene / Shot。
    """
    def __init__(self, llm: LLMService) -> None:
        self.llm = llm

    def run(self, request: UserRequest, outline: PlanOutline, revision_feedback: str = "") -> ScriptDraft:
        """优先走在线生成，并按质量情况依次尝试补尾、定向重写和 fallback。

        这里的核心目标是提升长文本剧本的可交付性，而不是只做一次简单生成。
        """
        system_prompt = "你是短剧编剧Agent。请生成专业短剧剧本，必须包含场景号、内外景、时间、地点、角色造型、人物情绪、动作描述、对白、分镜提示。分镜提示必须可直接用于视频解析，包含景别、运镜、转场、焦点人物、表情和微动作。对白简洁有力，节奏快。你必须严格围绕用户指定主题与风格写作，不得套用别的题材，不得偷换成霸总、甜宠或无关复仇模板。禁止输出Markdown标题、分隔线、代码块或额外说明，只输出剧本文本。"
        reversals_text = "; ".join(
            [
                (
                    item.get("summary")
                    or item.get("content")
                    or item.get("title")
                    or str(item)
                )
                if isinstance(item, dict)
                else str(item)
                for item in outline.reversals
            ]
        )
        style_text = "、".join(request.styles)
        scene_count = self._target_scene_count(request.episode_duration, request.episodes)
        style_rules = self._build_style_rules(request.styles, request.writing_tone)
        scene_blueprint = self._build_scene_blueprint(outline, scene_count)
        existing_script = self._extract_existing_script(revision_feedback)
        if existing_script:
            # 如果审校反馈里已经带回当前剧本，优先尝试“局部修尾”，
            # 避免整稿重写导致前文重复或风格漂移。
            repaired = self._repair_tail_only(existing_script, request, outline, scene_count, style_text)
            repaired = self._post_process_script(repaired, scene_count)
            quality_issues = self._validate_content(repaired, request)
            if self._is_usable_api_draft(repaired):
                return ScriptDraft(
                    title=outline.title,
                    content=repaired,
                    metadata={
                        "styles": "、".join(request.styles),
                        "writing_tone": request.writing_tone,
                        "audience": request.audience,
                        "generation_mode": "api_tail_repair",
                        "revision_feedback": revision_feedback,
                        "error_reason": "" if not quality_issues else f"尾部定向修补后仍有问题：{'; '.join(quality_issues)}",
                    },
                )
        user_prompt = f"""
请基于以下策划大纲生成完整短剧剧本：
标题：{outline.title}
开场钩子：{outline.opening_hook}
核心冲突：{outline.core_conflict}
反转点：{reversals_text}
结尾钩子：{outline.ending_hook}
三幕结构：{outline.three_act_outline}

用户需求：
主题：{request.theme}
关键词：{', '.join(request.keywords)}
目标受众：{request.audience}
剧本风格（融合）：{style_text}
文字风格：{request.writing_tone}
集数：{request.episodes}
单集时长：{request.episode_duration}秒
补充要求：{request.extra_requirements}
审校修订重点：{revision_feedback or '无，直接输出最佳版本'}

输出要求：
1. 全剧严格控制在 {scene_count} 个场景内，不能少于也不能多于 {scene_count} 个场景，适配 {request.episode_duration} 秒短剧节奏，避免冗长铺垫。
2. 每个场景必须严格使用以下结构输出，字段名不要改：
场景号：
内外景：
时间：
地点：
角色造型&情绪：
动作描述：
对白：
分镜提示：
3. `角色造型&情绪` 必须包含人物外貌、服装、年龄感、性别线索、身份关系、当下情绪，首次出场要交代身份、动机和说话气质。
4. `动作描述` 必须包含明确行动、互动关系、站位变化或物件动作，不能只写抽象心理。
5. `对白` 保持网感和可演性，单句简洁有冲击力，避免大段解释；关键人物的台词要体现年龄感、身份差异和语气特征，例如冷峻压迫、温柔克制、脆弱哽咽、年轻利落。
6. `分镜提示` 必须直接写出：景别、镜头类型、视角、运镜、转场、焦点人物、表情、微动作、画面重点，并明确镜头如何推进剧情或放大情绪。
7. 必须把主题核心冲突自然写进剧情推进中，不能只借标题带过。
8. 至少自然融入2个关键词原词到动作、对白或场景目标中。
9. 总字数尽量控制在 900-1500 字，保证每个场景写完整，不要在对白、动作或分镜提示中途截断。
10. 仅输出剧本正文，不要解释。
11. 严禁偷换主题：必须明确写出“{request.theme}”对应的人物关系、身份反转和核心矛盾。
12. 风格锁定要求：
{style_rules}
13. 场景推进蓝图：
{scene_blueprint}
14. 输出时不要写“镜头1/镜头2”这种简略标签，统一把镜头语言写进 `分镜提示` 字段。
15. 每个场景至少给出一个可直接用于视频生成的明确视角推进，例如“先环境观察，再切人物对位，最后近景逼近情绪爆点”。
16. 若存在多人对手戏，必须写清楚谁是焦点人物、谁在前景、谁在后景、镜头跟随谁移动。
"""
        try:
            api_mode = "api"
            error_reason = ""
            content = self._normalize_api_script(
                self.llm.complete(
                    system_prompt,
                    user_prompt,
                    temperature=0.3,
                    max_tokens=self._generation_max_tokens(scene_count),
                )
            )
            content = self._post_process_script(content, scene_count)
            quality_issues = self._validate_content(content, request)
            needs_targeted_repair = any(
                issue in quality_issues
                for issue in ["内容疑似截断", "结尾场景不完整", "场景数量超出", "场景数量不足"]
            )
            if needs_targeted_repair:
                # 长文本最常见的问题集中在尾部，因此优先走定向补尾。
                content = self._repair_tail_only(content, request, outline, scene_count, style_text)
                content = self._post_process_script(content, scene_count)
                quality_issues = self._validate_content(content, request)
            if quality_issues:
                soft_issues = {"内容疑似截断", "结尾场景不完整", "场景数量超出"}
                remaining_hard_issues = [issue for issue in quality_issues if issue not in soft_issues]
                if remaining_hard_issues:
                    # 只有还存在主题覆盖、结构缺失等硬问题时，才触发一次更强的定向重写。
                    rewrite_prompt = f"""
你输出的剧本基本可用，但仍需定向修补以下问题：{'; '.join(remaining_hard_issues)}
请基于当前剧本直接优化，不要偷换题材，不要删掉已有有效内容，重点修补：
1. 明确补强主题“{request.theme}”对应的核心矛盾。
2. 自然加入关键词 {', '.join(request.keywords[:4]) if request.keywords else '无'} 中至少2个原词。
3. 保持短剧节奏和场景、镜头、对白结构。
4. 风格必须稳定保持“{style_text} / {request.writing_tone}”。
5. 必须处理这些修订重点：{revision_feedback or '无'}。
6. 保持总场景数严格为 {scene_count} 个，不要新增额外场景。

当前剧本：
{content}
"""
                    content = self._normalize_api_script(
                        self.llm.complete(
                            system_prompt,
                            rewrite_prompt,
                            temperature=0.22,
                            max_tokens=self._rewrite_max_tokens(scene_count),
                        )
                    )
                    content = self._post_process_script(content, scene_count)
                    quality_issues = self._validate_content(content, request)
                if quality_issues:
                    if self._is_usable_api_draft(content):
                        api_mode = "api_salvaged"
                        error_reason = f"在线生成已保留，存在轻微问题：{'; '.join(quality_issues)}"
                    else:
                        raise RuntimeError(f"Writer输出质量不达标：{'; '.join(quality_issues)}")
        except Exception as exc:
            api_mode = "fallback"
            error_reason = str(exc)
            content = self._build_fallback_script(request, outline, scene_count)
            if revision_feedback.strip():
                content = self._apply_revision_feedback(content, revision_feedback)
        return ScriptDraft(
            title=outline.title,
            content=content,
            metadata={
                "styles": "、".join(request.styles),
                "writing_tone": request.writing_tone,
                "audience": request.audience,
                "generation_mode": api_mode,
                "revision_feedback": revision_feedback,
                "error_reason": error_reason,
            },
        )

    def _validate_content(self, content: str, request: UserRequest) -> list[str]:
        issues: list[str] = []
        target_scene_count = self._target_scene_count(request.episode_duration, request.episodes)
        if "场景" not in content:
            issues.append("缺少场景结构")
        if "分镜提示" not in content:
            issues.append("缺少分镜信息")
        if "对白" not in content:
            issues.append("缺少对白结构")
        if "动作描述" not in content and "动作：" not in content:
            issues.append("缺少动作描写")
        if "角色造型" not in content and "角色造型&情绪" not in content:
            issues.append("缺少角色状态描写")
        if len(content.strip()) < 500:
            issues.append("篇幅过短")
        normalized_content = self._normalize_text(content)
        if not self._theme_is_covered(request.theme, normalized_content):
            issues.append("主题植入不足")
        keyword_hits = [word for word in request.keywords[:4] if self._keyword_is_covered(word, normalized_content)]
        if request.keywords and len(keyword_hits) < min(2, len(request.keywords[:4])):
            issues.append("关键词未有效覆盖")
        scene_markers = self._scene_marker_count(content)
        if scene_markers < target_scene_count:
            issues.append("场景数量不足")
        if scene_markers > target_scene_count + 1:
            issues.append("场景数量超出")
        if self._looks_truncated(content):
            issues.append("内容疑似截断")
        if not self._has_complete_final_scene(content, target_scene_count):
            issues.append("结尾场景不完整")
        duplicate_dialogue = "林晚：今天这场订婚，我要的不只是答案，我还要把欠我的都讨回来。"
        if content.count(duplicate_dialogue) > 1:
            issues.append("对白模板重复")
        return issues

    def _is_usable_api_draft(self, content: str) -> bool:
        if len(content.strip()) < 420:
            return False
        required_markers = ["场景", "分镜提示", "对白"]
        return all(marker in content for marker in required_markers)

    def _scene_marker_count(self, content: str) -> int:
        scene_numbers = set()
        for match in re.finditer(r"(?:^|\n)\s*【?场景\s*(\d+)", content):
            scene_numbers.add(match.group(1))
        for match in re.finditer(r"(?:^|\n)\s*场景号[:：]?\s*(\d+)", content):
            scene_numbers.add(match.group(1))
        return len(scene_numbers)

    def _trim_to_scene_count(self, content: str, scene_count: int) -> str:
        if not content.strip():
            return content
        starts = []
        for match in re.finditer(r"(?:^|\n)\s*(?:【?场景\s*\d+|场景号[:：]?\s*\d+)", content):
            starts.append(match.start())
        if len(starts) <= scene_count:
            return content.strip()
        cut_index = starts[scene_count]
        return content[:cut_index].rstrip()

    def _post_process_script(self, content: str, scene_count: int) -> str:
        cleaned = self._normalize_scene_headers(content)
        cleaned = self._trim_duplicate_scene_blocks(cleaned)
        cleaned = self._trim_to_scene_count(cleaned, scene_count)
        return cleaned.strip()

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

    def _merge_continuation(self, base: str, continuation: str) -> str:
        base = base.rstrip()
        continuation = continuation.strip()
        if not continuation:
            return base
        if continuation in base:
            return base
        for overlap in range(min(len(base), len(continuation), 120), 20, -1):
            if base[-overlap:] == continuation[:overlap]:
                return f"{base}{continuation[overlap:]}"
        return f"{base}\n{continuation}"

    def _repair_tail_only(self, content: str, request: UserRequest, outline: PlanOutline, scene_count: int, style_text: str) -> str:
        cleaned = content.strip()
        existing_scene_count = self._scene_marker_count(cleaned)
        if existing_scene_count < scene_count:
            missing_from = max(existing_scene_count + 1, 1)
            append_prompt = f"""
请在不改动已有前文的前提下，从场景{missing_from}开始续写并补齐到场景{scene_count}。
要求：
1. 保留现有前文原样，不要重写场景1到场景{missing_from - 1}。
2. 只补写缺失场景，场景号必须连续到 {scene_count}。
3. 每个新增场景必须包含：场景号、内外景、时间、地点、角色造型&情绪、动作描述、对白、分镜提示。
4. 必须承接现有冲突，并用结尾钩子“{outline.ending_hook}”收束最后一场。
5. 保持题材、人物、风格一致：{style_text} / {request.writing_tone}。
6. 只输出缺失的尾段内容，也就是从场景{missing_from}开始的新场景，不要重复已有前文，不要解释，不要Markdown。

当前剧本：
{cleaned}
"""
            appended_tail = self._normalize_api_script(
                self.llm.complete(
                    system_prompt="你是短剧编剧补全Agent，只补写缺失场景，不能重写已有内容。",
                    user_prompt=append_prompt,
                    temperature=0.18,
                    max_tokens=self._tail_append_max_tokens(scene_count, existing_scene_count),
                )
            )
            merged = self._merge_missing_scenes(cleaned, appended_tail, missing_from)
            if self._scene_marker_count(merged) > existing_scene_count:
                return merged
        tail_prompt = f"""
请只修补下面剧本的尾部，不要重写前文，不要新增超过 {scene_count} 个场景。
你的目标：
1. 如果最后一场未完成，只补齐最后一场缺失的动作描述、对白、分镜提示。
2. 如果场景标题重复或格式混乱，只整理尾部重复部分。
3. 严格保持总场景数为 {scene_count} 个。
4. 保持题材、人物、风格一致：{style_text} / {request.writing_tone}。
5. 只输出修补后的完整剧本，不要解释，不要Markdown。

当前剧本：
{cleaned}

结尾钩子：
{outline.ending_hook}
"""
        repaired = self._normalize_api_script(self.llm.complete(system_prompt="你是短剧编剧修补Agent，只做尾部修补与结构整理。", user_prompt=tail_prompt, temperature=0.18, max_tokens=700))
        if repaired and len(repaired.strip()) >= max(500, len(cleaned) * 0.6):
            return repaired
        return cleaned

    def _merge_missing_scenes(self, base: str, appended_tail: str, missing_from: int) -> str:
        if not appended_tail.strip():
            return base
        normalized_tail = appended_tail.strip()
        pattern = re.compile(rf"(?:^|\n)\s*(?:【?场景\s*{missing_from}(?:】)?|场景号[:：]?\s*{missing_from})(?:\s|[:：]|$)")
        match = pattern.search(normalized_tail)
        if match:
            normalized_tail = normalized_tail[match.start():].lstrip()
        return self._merge_continuation(base, normalized_tail)

    def _normalize_scene_headers(self, content: str) -> str:
        cleaned = re.sub(r"场景号\s*(\d+)", r"场景号：\1", content)
        cleaned = re.sub(r"场景\s+号[:：]?\s*(\d+)", r"场景号：\1", cleaned)
        cleaned = re.sub(r"^场景\s*(\d+)\s*$", r"场景\1", cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r"^【场景\s*(\d+)】", r"场景\1", cleaned, flags=re.MULTILINE)
        return cleaned

    def _trim_duplicate_scene_blocks(self, content: str) -> str:
        pattern = re.compile(r"(?m)^(场景\d+)\s*$")
        matches = list(pattern.finditer(content))
        if not matches:
            return content
        blocks: list[tuple[str, str]] = []
        for index, match in enumerate(matches):
            start = match.start()
            end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
            header = match.group(1)
            block = content[start:end].strip()
            blocks.append((header, block))
        deduped: dict[str, str] = {}
        ordered_headers: list[str] = []
        for header, block in blocks:
            if header not in deduped or len(block) > len(deduped[header]):
                deduped[header] = block
            if header not in ordered_headers:
                ordered_headers.append(header)
        return "\n\n".join(deduped[header] for header in ordered_headers).strip()

    def _normalize_api_script(self, content: str) -> str:
        cleaned = content.strip()
        cleaned = re.sub(r"^```[\w-]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        cleaned = re.sub(r"^#+\s*", "", cleaned, flags=re.MULTILINE)
        cleaned = cleaned.replace("---\n", "").replace("\n---", "\n")
        cleaned = re.sub(r"\*\*(.+?)\*\*", r"\1", cleaned)
        return cleaned.strip()

    def _normalize_text(self, text: str) -> str:
        return re.sub(r"[\W_]+", "", text).lower()

    def _theme_is_covered(self, theme: str, normalized_content: str) -> bool:
        normalized_theme = self._normalize_text(theme)
        if not normalized_theme:
            return True
        if normalized_theme in normalized_content:
            return True
        if len(normalized_theme) <= 4:
            return normalized_theme in normalized_content
        segments = [normalized_theme[index:index + 4] for index in range(0, len(normalized_theme) - 3)]
        hit_count = sum(1 for segment in segments if segment in normalized_content)
        return hit_count >= 1

    def _keyword_is_covered(self, keyword: str, normalized_content: str) -> bool:
        normalized_keyword = self._normalize_text(keyword)
        if not normalized_keyword:
            return False
        return normalized_keyword in normalized_content

    def _target_scene_count(self, episode_duration: int, episodes: int) -> int:
        if episode_duration <= 45:
            per_episode = 3
        elif episode_duration <= 90:
            per_episode = 4
        else:
            per_episode = 5
        return per_episode * max(episodes, 1)

    def _generation_max_tokens(self, scene_count: int) -> int:
        if scene_count <= 3:
            return 1400
        if scene_count <= 6:
            return 2000
        if scene_count <= 9:
            return 2600
        return 3200

    def _rewrite_max_tokens(self, scene_count: int) -> int:
        if scene_count <= 3:
            return 1200
        if scene_count <= 6:
            return 1600
        if scene_count <= 9:
            return 2000
        return 2400

    def _tail_append_max_tokens(self, scene_count: int, existing_scene_count: int) -> int:
        missing = max(scene_count - existing_scene_count, 1)
        if missing <= 1:
            return 700
        if missing <= 2:
            return 1000
        if missing <= 4:
            return 1400
        return 1800

    def _build_style_rules(self, styles: list[str], writing_tone: str) -> str:
        rules: list[str] = []
        normalized = set(styles)
        if "都市悬疑" in normalized or "悬疑推理" in normalized:
            rules.append("- 都市悬疑：信息揭示要层层递进，证据链推动剧情，不要写成无脑情爱纠缠。")
        if "复仇" in normalized:
            rules.append("- 复仇：主角目标必须明确，行动要针对伤害来源，不要空喊口号。")
        if "霸总" in normalized:
            rules.append("- 霸总：只可作为人物气场补充，不能盖过主线主题。")
        if "甜宠" not in normalized:
            rules.append("- 未选择甜宠时，禁止出现甜宠腔或撒糖式对白主导剧情。")
        if "权谋" in normalized or "商战" in normalized:
            rules.append("- 权谋/商战：冲突要通过利益交换、布局、博弈来呈现。")
        rules.append(f"- 文字风格：整体语言必须符合“{writing_tone}”，不能口吻漂移。")
        return "\n".join(rules)

    def _build_scene_blueprint(self, outline: PlanOutline, scene_count: int) -> str:
        acts = outline.three_act_outline if isinstance(outline.three_act_outline, list) else [outline.three_act_outline]
        reversals = outline.reversals if isinstance(outline.reversals, list) else [outline.reversals]
        lines: list[str] = []
        for index in range(scene_count):
            act = acts[index] if index < len(acts) else {"summary": "冲突升级"}
            reversal = reversals[index] if index < len(reversals) else "局势骤变"
            act_summary = act.get("summary", "冲突升级") if isinstance(act, dict) else str(act)
            reversal_summary = reversal.get("summary", "局势骤变") if isinstance(reversal, dict) else str(reversal)
            lines.append(f"- 场景{index + 1}：推进“{act_summary}”，并完成反转“{reversal_summary}”。")
        return "\n".join(lines)

    def _apply_revision_feedback(self, content: str, feedback: str) -> str:
        if not feedback.strip():
            return content
        return f"{content}\n\n分镜提示补强：{feedback.strip()}"

    def _extract_existing_script(self, revision_feedback: str) -> str:
        marker = "当前剧本："
        if marker not in revision_feedback:
            return ""
        return revision_feedback.split(marker, 1)[1].strip()

    def _build_fallback_script(self, request: UserRequest, outline: PlanOutline, scene_count: int) -> str:
        keyword_text = "、".join(request.keywords[:4]) if request.keywords else request.theme[:8]
        reversal_lines = []
        reversals = outline.reversals if isinstance(outline.reversals, list) else [outline.reversals]
        for item in reversals[:scene_count]:
            if isinstance(item, dict):
                reversal_lines.append(item.get("summary") or item.get("content") or item.get("title") or "局势骤变")
            else:
                reversal_lines.append(str(item))
        while len(reversal_lines) < scene_count:
            reversal_lines.append("危机升级，真相逼近")
        raw_acts = outline.three_act_outline if isinstance(outline.three_act_outline, list) else [outline.three_act_outline]
        acts = []
        for index, item in enumerate(raw_acts[:scene_count], start=1):
            if isinstance(item, dict):
                acts.append(
                    {
                        "act": str(item.get("act", f"第{index}幕")).strip() or f"第{index}幕",
                        "summary": str(item.get("summary", item.get("content", reversal_lines[index - 1]))).strip() or reversal_lines[index - 1],
                    }
                )
            else:
                acts.append({"act": f"第{index}幕", "summary": str(item).strip() or reversal_lines[index - 1]})
        while len(acts) < scene_count:
            acts.append({"act": f"第{len(acts) + 1}幕", "summary": reversal_lines[len(acts)]})
        locations = [
            ("内景", "夜", "订婚宴会厅侧门"),
            ("内景", "夜", "休息室"),
            ("外景", "夜", "酒店地下车库"),
            ("内景", "夜", "老宅书房"),
            ("外景", "夜", "天台边缘"),
        ]
        emotion_sets = [
            ("女主苏晚，礼服未乱但眼神结冰，强压怒意", "男主顾承泽，西装笔挺却神色闪躲"),
            ("苏晚摘下耳饰握紧证据，语气克制", "闺蜜林薇薇强装镇定，手心冒汗"),
            ("苏晚步步逼近，杀气外露", "顾承泽嘴硬，试图抢夺手机"),
            ("苏晚冷静设局，开始反制", "林薇薇情绪崩溃，防线松动"),
            ("苏晚彻底掌控局面，只等收网", "顾承泽狼狈失控，底牌尽失"),
        ]
        action_templates = [
            "苏晚听见密谋后没有立刻闯入，而是先打开手机同步备份，把关键录音和现场画面同时上传到云端。",
            "苏晚推门打断二人伪装，用一句试探性提问逼出破绽，迫使对方主动暴露真正目标。",
            "顾承泽试图以婚约和家族压力反制，苏晚当场甩出早已准备好的证据链，反客为主。",
            "林薇薇为了自保开始甩锅，苏晚顺势追问细节，让两人的口径当场对不上。",
            "苏晚完成最后反杀，把全部证据交给赶到的警方或董事会代表，并留下下一集钩子。",
        ]
        dialogue_pairs = [
            ("苏晚：原来我站在这里等的是订婚誓词，你们等的却是我的家产。", "顾承泽：你少在这儿装清高，没有我，你守不住这一切。"),
            ("苏晚：你们以为把我哄进礼堂，我就会乖乖签字？", "林薇薇：晚晚，你先冷静，事情不是你听到的那样。"),
            ("苏晚：从今天起，这场婚约不是喜事，是你们的证物。", "顾承泽：你真要把事情闹大，对苏家也没好处。"),
            ("苏晚：你们最蠢的地方，是把我当成只会相信感情的人。", "林薇薇：都是他逼我的，我只是帮他传了几次消息！"),
            ("苏晚：该结束了，今晚之后，你们一个都跑不掉。", "顾承泽：你赢这一局，不代表你真能活着拿回全部真相。"),
        ]
        scenes: list[str] = []
        for index in range(scene_count):
            act = acts[index]
            beat = reversal_lines[index]
            location = locations[index] if index < len(locations) else locations[-1]
            emotions = emotion_sets[index] if index < len(emotion_sets) else emotion_sets[-1]
            actions = action_templates[index] if index < len(action_templates) else action_templates[-1]
            dialogues = dialogue_pairs[index] if index < len(dialogue_pairs) else dialogue_pairs[-1]
            scene = f"""【场景{index + 1}】
场景号：{index + 1}
内外景：{location[0]}
时间：{location[1]}
地点：{location[2]}
角色造型&情绪：{emotions[0]}；{emotions[1]}。服装保持前后一致，人物年龄感和身份信息明确。
动作描述：围绕“{request.theme}”的核心矛盾推进。{actions} 同时自然埋入关键词：{keyword_text}。
对白：
苏晚：{dialogues[0].replace("苏晚：", "").strip()}
{"顾承泽" if index in {0, 2, 4} else "林薇薇"}：{dialogues[1].split("：", 1)[1].strip() if "：" in dialogues[1] else dialogues[1]}
分镜提示：景别中景，运镜缓推或跟拍，转场硬切，焦点人物跟随当前冲突中心，表情和微动作清晰可见。突出{outline.core_conflict}，完成“{act.get('summary', beat)}”的推进，并以“{beat}”形成节奏反转。"""
            scenes.append(scene)
        return "\n\n".join(scenes)
