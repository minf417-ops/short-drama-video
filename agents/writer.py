import re
import time

from .schemas import UserRequest, PlanOutline, ScriptDraft, EditRequest
from services.llm_service import LLMService


class WriterAgent:
    """根据 Planner 大纲生成正式剧本。

    当前 Writer 的目标不只是写出“能看”的文本，还要尽量输出更适合后续视频解析的
    场景结构，方便下游拆成 Scene / Shot。
    """
    def __init__(self, llm: LLMService) -> None:
        self.llm = llm
        self._logger = None

    def run(self, request: UserRequest, outline: PlanOutline, revision_feedback: str = "", logger=None) -> ScriptDraft:
        """优先走在线生成，并按质量情况依次尝试补尾、定向重写和 fallback。

        这里的核心目标是提升长文本剧本的可交付性，而不是只做一次简单生成。
        logger: 可选的日志回调函数，用于报告生成进度。
        """
        self._logger = logger
        if request.episodes > 1:
            return self._run_multi_episode(request, outline, revision_feedback)
        system_prompt = "你是短剧编剧Agent，擅长写可直接拆解为视频分镜的结构化短剧剧本。每个场景包含：场景号、内外景、时间、地点、角色造型&情绪、动作描述、对白、分镜提示。写法参照电影分场景剧本：角色描写聚焦外貌辨识度与当前情绪；动作描写聚焦可拍摄的具体行为与空间关系；对白简洁有力、有人物辨识度；分镜提示一句话概括景别、运镜与情绪焦点。严格围绕用户主题与风格写作，不得偷换题材。仅输出剧本正文，禁止Markdown、代码块或解释。"
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
1. 全剧严格 {scene_count} 个场景，适配 {request.episode_duration} 秒短剧节奏，不多不少。
2. 每个场景使用以下固定结构（字段名不要改）：
场景号：（数字）
内外景：（内景/外景）
时间：（白天/夜晚/黄昏等）
地点：（具体地点名）
角色造型&情绪：（写出人物外貌辨识特征、服装风格与当前情绪状态，首次出场需交代身份关系。用分号隔开不同角色。不要写空泛的"衣着华丽"，要写具体。）
动作描述：（写可拍摄的具体行为：走位、手部动作、物件互动、视线方向、空间距离变化。环境光线与道具细节自然融入动作中。不写抽象心理活动。）
对白：（格式"角色名（动作/语气）：台词"。单句简短有力，体现人物身份差异。）
分镜提示：（一句话概括：景别 + 运镜方向 + 焦点人物 + 情绪转折点。）
3. 围绕"{request.theme}"展开核心冲突，至少自然融入2个关键词到剧情中。严禁偷换主题。
4. 风格锁定：
{style_rules}
5. 场景推进蓝图：
{scene_blueprint}
6. 仅输出剧本正文，不要解释，不要Markdown。**每个场景必须写完整，严禁中途截断。**
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
        if len(content.strip()) < 900:
            issues.append("篇幅过短")
        if content.count("角色造型&情绪：") < target_scene_count:
            issues.append("角色描写密度不足")
        if content.count("分镜提示：") < target_scene_count:
            issues.append("分镜描写密度不足")
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

    def _scenes_per_episode(self, episode_duration: int) -> int:
        if episode_duration <= 45:
            return 3
        elif episode_duration <= 90:
            return 4
        return 5

    def _target_scene_count(self, episode_duration: int, episodes: int) -> int:
        return self._scenes_per_episode(episode_duration) * max(episodes, 1)

    def _generation_max_tokens(self, scene_count: int) -> int:
        if scene_count <= 3:
            return 4000
        if scene_count <= 4:
            return 5500
        if scene_count <= 6:
            return 7500
        if scene_count <= 9:
            return 10000
        if scene_count <= 12:
            return 13000
        return 16000

    def _rewrite_max_tokens(self, scene_count: int) -> int:
        if scene_count <= 3:
            return 3500
        if scene_count <= 4:
            return 5000
        if scene_count <= 6:
            return 7000
        if scene_count <= 9:
            return 9000
        if scene_count <= 12:
            return 12000
        return 15000

    def _tail_append_max_tokens(self, scene_count: int, existing_scene_count: int) -> int:
        missing = max(scene_count - existing_scene_count, 1)
        if missing <= 1:
            return 1500
        if missing <= 2:
            return 2500
        if missing <= 4:
            return 4000
        if missing <= 6:
            return 6000
        return 8000

    def _build_style_rules(self, styles: list[str], writing_tone: str) -> str:
        rules: list[str] = []
        normalized = set(styles)
        if "都市悬疑" in normalized or "悬疑推理" in normalized:
            rules.append("- 都市悬疑：信息揭示要层层递进，证据链推动剧情，不要写成无脑情爱纠缠。")
            rules.append("- 都市悬疑：每个关键场景至少给出一个可见线索、一个误导点或一个身份疑点。")
        if "复仇" in normalized:
            rules.append("- 复仇：主角目标必须明确，行动要针对伤害来源，不要空喊口号。")
            rules.append("- 复仇：必须出现打脸、反制、逼供、揭穿、夺回利益或身份翻盘中的至少两类爽点。")
        if "穿书" in normalized:
            rules.append("- 穿书：必须写清主角知道原书剧情、原身处境、死亡/失败节点，并主动改写命运。")
            rules.append("- 穿书：至少出现一次“剧情偏离原书预期”的明确桥段。")
        if "重生" in normalized:
            rules.append("- 重生：要体现前世记忆如何转化为本轮先手布局，不能只停留在设定说明。")
        if "甜宠" in normalized:
            rules.append("- 甜宠：糖点要服务人物关系推进，不能冲淡主线矛盾。")
        if "古风逆袭" in normalized:
            rules.append("- 古风逆袭：要有尊卑秩序、公开羞辱/打脸、身份翻案或名分逆转。")
        if "年代" in normalized:
            rules.append("- 年代：道具、服饰、生活空间、话语习惯要有年代感，不能写成现代都市口吻。")
        if "修仙" in normalized or "仙侠" in normalized:
            rules.append("- 修仙/仙侠：人物服化道、法器、宗门建筑、灵气流动、符纹光效必须具体，不要只写空泛仙气。")
        if "霸总" in normalized:
            rules.append("- 霸总：只可作为人物气场补充，不能盖过主线主题。")
        if "甜宠" not in normalized:
            rules.append("- 未选择甜宠时，禁止出现甜宠腔或撒糖式对白主导剧情。")
        if "权谋" in normalized or "商战" in normalized:
            rules.append("- 权谋/商战：冲突要通过利益交换、布局、博弈来呈现。")
        if "犯罪" in normalized:
            rules.append("- 犯罪：剧情要围绕犯罪事件展开，需有明确的作案动机、侦查推进和嫌疑链条。")
        if "穿越" in normalized:
            rules.append("- 穿越：主角必须利用穿越身份/知识形成信息差优势，展示具体的先知行动。")
        if "校园" in normalized:
            rules.append("- 校园：场景限定在校园及周边，人物关系围绕同学、师生展开，情节贴合学生生活。")
        if "家庭伦理" in normalized:
            rules.append("- 家庭伦理：冲突围绕家庭成员之间的利益、情感、代际矛盾展开，细节要有生活质感。")
        if "体育竞技" in normalized:
            rules.append("- 体育竞技：必须有明确的赛事目标、训练困境和比赛高光时刻，动作描写要具体可视化。")
        if "医疗" in normalized:
            rules.append("- 医疗：涉及诊断、手术、伦理抉择等专业场景，需有紧迫感和生死赌注。")
        if "科幻" in normalized or "赛博朋克" in normalized:
            rules.append("- 科幻/赛博朋克：世界观设定要有具体科技细节，场景需有未来感的视觉元素（霓虹、义体、AI界面等）。")
        if "末世" in normalized:
            rules.append("- 末世：环境必须体现末世特征（废墟、资源匮乏、危险生物），生存压力要贯穿始终。")
        if "无限流" in normalized:
            rules.append("- 无限流：副本/关卡规则必须明确，主角需在限定条件下用智力或能力通关。")
        if "军旅" in normalized:
            rules.append("- 军旅：军事纪律、战友情谊、任务压力要具体，体现军人特质和集体荣誉。")
        if "虐恋" in normalized:
            rules.append("- 虐恋：误会、牺牲、被迫分离的情节要有充分铺垫，虐点要服务于情感深度。")
        if "治愈" in normalized:
            rules.append("- 治愈：温暖细节和人物成长要自然渐进，避免强行煽情。")
        if "暗黑" in normalized:
            rules.append("- 暗黑：道德灰色地带和人性阴暗面要通过具体事件展现，不是单纯的恶意堆砌。")
        if "热血" in normalized:
            rules.append("- 热血：主角面对逆境时的信念和爆发要有层层递进，战斗/对抗场面要燃。")
        if "黑色幽默" in normalized:
            rules.append("- 黑色幽默：荒诞与讽刺并存，笑点背后要有深层社会或人性洞察。")
        if "轻喜剧" in normalized:
            rules.append("- 轻喜剧：节奏轻快，误会、反差、夸张手法制造笑点，但不失主线推进力。")
        if "宫斗" in normalized:
            rules.append("- 宫斗：等级制度、后宫势力、明争暗斗必须具体，体现智谋博弈和地位争夺。")
        if "谍战" in normalized:
            rules.append("- 谍战：潜伏、情报交换、身份危机要有悬念节奏，信任与背叛交织推进。")
        rules.append("- 类型元素必须外显：身份压制、关系拉扯、资源争夺、公开对峙、情绪爆点都要落到具体场景里。")
        rules.append("- 每个场景都要有明确功能：设局、试探、反击、揭露、翻盘、留钩，不能只有氛围没有事件。")
        rules.append("- 默认采用高细节可视化写法：人物、服装、表情、环境、运镜都要具体到可直接给导演和视频模型使用。")
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
            if index == 0:
                scene_goal = "开场即给出身份压制或生死危机，并抛出可见钩子"
            elif index == scene_count - 1:
                scene_goal = "完成阶段性翻盘或反杀，并留下更大钩子"
            else:
                scene_goal = "推进对抗、制造误判，再给出一次关系或利益反转"
            lines.append(f"- 场景{index + 1}：推进“{act_summary}”，完成反转“{reversal_summary}”，场景功能是“{scene_goal}”。必须出现具体事件动作、人物关系拉扯和一个可视化爽点。")
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
            ("内景", "夜", "宗门大殿偏厅"),
            ("外景", "夜", "万骨崖底禁地"),
            ("内景", "夜", "祭天台外廊"),
            ("内景", "夜", "祭阵中心"),
            ("外景", "夜", "坍塌后的宗门废墟"),
        ]
        emotion_sets = [
            ("女主，二十出头，黑发半绾，婚制法袍外层赤金云纹薄纱已被鲜血浸透，唇色苍白却强撑冷意，眼底从震惊迅速凝成杀意", "男主，同龄青年，玄色礼袍束金冠，表面克制端正，实则眼神游移、指节发紧"),
            ("女主发髻散乱，额角带血，破损法袍沾着尘土与骨灰，呼吸发颤却目光死死咬住前方", "残灵或对手以低沉、危险、诱导式姿态出现，气息逼仄"),
            ("女主换上暗色斗篷遮住伤痕，脸色冷白，动作收束但杀气压不住", "对手衣冠整肃却防线松动，嘴硬，神色一寸寸失控"),
            ("女主站进光阵中央，衣摆与黑焰同时翻卷，悲怒压到极致", "幕后者法袍鼓荡，面容狰狞，眼底有被逼急的惧色"),
            ("女主立于废墟高处，发丝被夜风吹散，衣袍残破却脊背笔直，情绪从剧痛转成冷硬誓言", "幸存对手或旁观者狼狈失神，不敢直视她"),
        ]
        action_templates = [
            "主角先以极短停顿稳住身体，再一步步逼近冲突中心，衣摆拖过地面留下血痕或灵焰痕迹，用手、眼神和站位压迫对手，在动线变化里把真相一点点撕开。",
            "主角在恶劣环境中完成重生或觉醒，先是蜷缩、挣扎、抬头，再借法器、骨骸、阵纹或灵气完成反转，动作层层递进，视觉上要有明显的能量变化。",
            "主角带着隐藏伤势回到核心场域，故意示弱试探，让对手先出招，再借对方动作反制，过程中要写清距离拉近、视线碰撞和物件落点。",
            "主角主动踏入最危险的位置，以身体承受代价换取阵法失控或真相曝光，要求把风、火、光、血、衣料、碎石等动态细节写出来。",
            "主角在废墟或残局中完成最后宣判，不急着离开，而是停顿、转身、抬眼或垂眸，让余波和空间寂静形成结尾钩子。",
        ]
        dialogue_pairs = [
            ("主角：我站在这里，不是等你们给我一个名分，是等你们把欠我的命债亲口认出来。", "对手：你现在这副样子，还拿什么跟我算账？"),
            ("主角：这万骨崖吞不掉我，你们也一样。", "对手：你若接受力量，就再也回不了头。"),
            ("主角：我今日回来，不是求活路，是来给你们断活路。", "对手：你别逼我把最后一点情面也撕碎。"),
            ("主角：你最该怕的，不是我活着回来，是我把真相带回来了。", "对手：就算你知道了，又能改变什么！"),
            ("主角：青霄宗的债，我会一层天一层天地讨。", "对手：你杀了我，也只是刚刚开始。"),
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
角色造型&情绪：{emotions[0]}；{emotions[1]}。要求把发型、衣料层次、颜色、饰物、伤痕、身份压迫感和此刻情绪都写实写满，保证观众一眼能看清人物关系与危险程度。
动作描述：围绕“{request.theme}”的核心矛盾推进。{actions} 同时自然埋入关键词：{keyword_text}。环境里的风声、光线、尘土、血迹、法器、阵纹或建筑细节要参与动作，不要让人物悬空表演。
对白：
苏晚：{dialogues[0].replace("苏晚：", "").strip()}
{"顾承泽" if index in {0, 2, 4} else "林薇薇"}：{dialogues[1].split("：", 1)[1].strip() if "：" in dialogues[1] else dialogues[1]}
分镜提示：先用环境建立镜头交代空间、光线与危险源，再切到人物对位关系，最后用近景或特写逼近情绪爆点。景别、视角、运镜、转场、焦点人物、表情、微动作、前后景构图都要具体写清。突出{outline.core_conflict}，完成“{act.get('summary', beat)}”的推进，并以“{beat}”形成节奏反转。"""
            scenes.append(scene)
        return "\n\n".join(scenes)

    # ===================== 多集生成 =====================

    def _log(self, message: str) -> None:
        """通过可选的 logger 回调输出进度日志。"""
        if self._logger:
            self._logger(message)

    def _run_multi_episode(self, request: UserRequest, outline: PlanOutline, revision_feedback: str = "") -> ScriptDraft:
        """并行生成多集剧本，大幅缩短耗时。"""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        episodes = max(request.episodes, 1)
        scenes_per_ep = self._scenes_per_episode(request.episode_duration)
        max_workers = min(4, episodes)
        self._log(f"Writer：多集并行模式，共{episodes}集×{scenes_per_ep}场景，{max_workers}路并发")
        style_text = "、".join(request.styles)
        style_rules = self._build_style_rules(request.styles, request.writing_tone)
        system_prompt = (
            "你是短剧编剧Agent，擅长按集生成可直接拆解为视频分镜的结构化短剧剧本。"
            "每个场景包含：场景号、内外景、时间、地点、角色造型&情绪、动作描述、对白、分镜提示。"
            "角色描写聚焦外貌辨识度与当前情绪；动作描写聚焦可拍摄的具体行为；"
            "对白简洁有力；分镜提示一句话概括景别、运镜与情绪焦点。"
            "严格围绕用户主题写作。仅输出本集剧本正文，禁止Markdown或解释。"
        )
        episode_plan = self._build_episode_plan(episodes, outline)
        api_mode = "api_multi_episode"
        error_reason = ""
        gen_start = time.time()

        def _generate_one(ep_num: int) -> str:
            act_info = self._episode_act_info(ep_num, episodes, outline)
            ep_reversal = self._episode_reversal(ep_num, episodes, outline.reversals)
            is_first = ep_num == 1
            is_last = ep_num == episodes
            ep_prompt = f"""请为第{ep_num}集（共{episodes}集）生成剧本，本集包含{scenes_per_ep}个场景。

总体信息：
标题：{outline.title}
核心冲突：{outline.core_conflict}
本集所处幕次：{act_info}
本集关键反转：{ep_reversal}
{"开场钩子：" + outline.opening_hook if is_first else ""}
{"本集为大结局，需用结尾钩子收束：" + outline.ending_hook if is_last else "本集结尾需留悬念，引导观众追看下一集。"}

全剧各集规划（请严格只写第{ep_num}集，但了解前后文以保持连贯）：
{episode_plan}

用户需求：
主题：{request.theme}
关键词：{', '.join(request.keywords)}
风格：{style_text}
文字风格：{request.writing_tone}
单集时长：{request.episode_duration}秒

输出要求：
1. 本集严格{scenes_per_ep}个场景，场景号从1到{scenes_per_ep}。
2. 每个场景使用固定结构（字段名不要改）：
场景号：（数字）
内外景：（内景/外景）
时间：（白天/夜晚/黄昏等）
地点：（具体地点名）
角色造型&情绪：（人物外貌辨识特征、服装与当前情绪，首次出场交代身份）
动作描述：（可拍摄的具体行为：走位、手部动作、物件互动、视线方向）
对白：（格式"角色名（动作/语气）：台词"，单句简短有力）
分镜提示：（一句话：景别+运镜+焦点人物+情绪转折点）
3. 围绕"{request.theme}"推进本集冲突，本集剧情不要与其他集重复。
4. 风格锁定：
{style_rules}
5. 仅输出本集剧本正文，每个场景必须写完整，严禁中途截断。"""
            self._log(f"Writer：正在生成第{ep_num}/{episodes}集...")
            _ep_start = time.time()
            try:
                ep_content = self._normalize_api_script(
                    self.llm.complete(
                        system_prompt, ep_prompt,
                        temperature=0.3,
                        max_tokens=self._generation_max_tokens(scenes_per_ep),
                    )
                )
                ep_content = self._post_process_script(ep_content, scenes_per_ep)
                self._log(f"Writer：第{ep_num}集完成 | {time.time() - _ep_start:.1f}s")
                return ep_content
            except Exception as exc:
                self._log(f"Writer：第{ep_num}集API失败，兜底 | {exc}")
                return self._build_fallback_episode(
                    ep_num, scenes_per_ep, request, outline, act_info, ep_reversal
                )

        results: dict[int, str] = {}
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {executor.submit(_generate_one, ep): ep for ep in range(1, episodes + 1)}
            for future in as_completed(future_map):
                ep_num = future_map[future]
                try:
                    results[ep_num] = future.result()
                except Exception as exc:
                    error_reason = f"第{ep_num}集异常：{exc}"
                    api_mode = "api_multi_episode_partial_fallback"
                    act_info = self._episode_act_info(ep_num, episodes, outline)
                    ep_reversal = self._episode_reversal(ep_num, episodes, outline.reversals)
                    results[ep_num] = self._build_fallback_episode(
                        ep_num, scenes_per_ep, request, outline, act_info, ep_reversal
                    )

        gen_elapsed = time.time() - gen_start
        self._log(f"Writer：全部{episodes}集正文生成完成 | 总耗时 {gen_elapsed:.1f}s")

        self._log("Writer：正在为所有集生成集名...")
        titles = self._batch_generate_episode_titles(results, episodes, request)
        all_episodes = [(ep, titles.get(ep, f"第{ep}集"), results[ep]) for ep in range(1, episodes + 1)]

        full_script = self._combine_episodes(all_episodes)
        return ScriptDraft(
            title=outline.title,
            content=full_script,
            metadata={
                "styles": style_text,
                "writing_tone": request.writing_tone,
                "audience": request.audience,
                "generation_mode": api_mode,
                "episodes": str(episodes),
                "scenes_per_episode": str(scenes_per_ep),
                "revision_feedback": revision_feedback,
                "error_reason": error_reason,
            },
        )

    def _episode_act_info(self, ep_num: int, total_eps: int, outline: PlanOutline) -> str:
        """根据集数位置确定当前集所处的幕次。"""
        acts = outline.three_act_outline if isinstance(outline.three_act_outline, list) else []
        act1_end = max(total_eps // 4, 1)
        act3_start = total_eps - max(total_eps // 4, 1) + 1
        if ep_num <= act1_end:
            act = acts[0] if acts else {"act": "第一幕", "summary": "建立关系与危机"}
        elif ep_num >= act3_start:
            act = acts[2] if len(acts) > 2 else {"act": "第三幕", "summary": "高潮与结局"}
        else:
            act = acts[1] if len(acts) > 1 else {"act": "第二幕", "summary": "冲突升级与反转"}
        act_name = act.get("act", "第二幕") if isinstance(act, dict) else "第二幕"
        summary = act.get("summary", "冲突推进") if isinstance(act, dict) else str(act)
        return f"{act_name}：{summary}"

    def _episode_reversal(self, ep_num: int, total_eps: int, reversals) -> str:
        """为当前集分配反转/情节点。"""
        rev_list = reversals if isinstance(reversals, list) else ([reversals] if reversals else [])
        if not rev_list:
            return "推进冲突并制造一次反转"
        idx = int((ep_num - 1) * len(rev_list) / max(total_eps, 1))
        idx = min(idx, len(rev_list) - 1)
        rev = rev_list[idx]
        if isinstance(rev, dict):
            return rev.get("summary", rev.get("content", "局势反转"))
        return str(rev)

    def _build_episode_plan(self, episodes: int, outline: PlanOutline) -> str:
        """预生成各集剧情规划，替代 prev_summary 实现无依赖并行。"""
        lines: list[str] = []
        for ep in range(1, episodes + 1):
            act_info = self._episode_act_info(ep, episodes, outline)
            reversal = self._episode_reversal(ep, episodes, outline.reversals)
            extra = ""
            if ep == 1:
                extra = f"（开场钩子：{outline.opening_hook[:40]}）"
            elif ep == episodes:
                extra = f"（结局收束：{outline.ending_hook[:40]}）"
            lines.append(f"第{ep}集 [{act_info}] 反转：{reversal[:30]}{extra}")
        return "\n".join(lines)

    def _batch_generate_episode_titles(self, results: dict, episodes: int, request: UserRequest) -> dict:
        """一次API调用为所有集批量生成集名，失败则走启发式提取。"""
        summaries: list[str] = []
        for ep in range(1, episodes + 1):
            content = results.get(ep, "")
            brief = content[:150].replace('\n', ' ')
            summaries.append(f"第{ep}集：{brief}")
        all_summaries = "\n".join(summaries)
        try:
            data = self.llm.complete_json(
                system_prompt="你是短剧集名专家。根据每集摘要批量生成集名。只输出JSON。",
                user_prompt=f"""主题：{request.theme}
请为以下{episodes}集分别起一个集名：

{all_summaries}

要求：
1. 每个集名3-10个字，可以是台词片段、悬念短句或情绪关键词
2. 要勾起读者好奇心，暗示本集核心冲突或转折
3. 不要加标点和书名号
4. 只输出JSON：{{"titles": ["集名1", "集名2", ...]}}""",
                required_fields=["titles"],
                temperature=0.6,
                max_tokens=min(episodes * 30 + 100, 2000),
            )
            title_list = data.get("titles", [])
            if len(title_list) >= episodes:
                titles = {}
                for i in range(episodes):
                    t = str(title_list[i]).strip().strip("《》\"'""''")
                    titles[i + 1] = t if t else f"第{i + 1}集"
                self._log(f"Writer：集名批量生成完成（API）")
                return titles
        except Exception:
            pass
        self._log("Writer：集名API失败，使用启发式提取")
        return self._heuristic_episode_titles(results, episodes)

    def _heuristic_episode_titles(self, results: dict, episodes: int) -> dict:
        """从剧本台词/动作中提取集名，零API调用。"""
        titles: dict[int, str] = {}
        for ep in range(1, episodes + 1):
            content = results.get(ep, "")
            title = self._extract_title_from_content(content, ep)
            titles[ep] = title
        return titles

    def _extract_title_from_content(self, content: str, ep_num: int) -> str:
        """从单集内容中提取一个有吸引力的短语作为集名。"""
        _META_STARTS = ('场景号', '内外景', '时间', '地点', '角色造型', '动作描述', '分镜提示', '对白')
        for line in content.split('\n'):
            stripped = line.strip()
            if '：' not in stripped or stripped.startswith(_META_STARTS):
                continue
            parts = stripped.split('：', 1)
            if len(parts) == 2:
                text = parts[1].strip().rstrip('。！？，、；…—')
                if 3 <= len(text) <= 12:
                    return text
        return f"第{ep_num}集"

    def _combine_episodes(self, all_episodes: list) -> str:
        """将所有集合并为完整剧本，添加集标题分隔。"""
        parts: list[str] = []
        for ep_num, ep_title, ep_content in all_episodes:
            header = f"第{ep_num}集：{ep_title}"
            parts.append(f"{header}\n\n{ep_content}")
        return ("\n\n" + "=" * 40 + "\n\n").join(parts)

    def _build_fallback_episode(self, ep_num: int, scenes_per_ep: int, request: UserRequest,
                                outline: PlanOutline, act_info: str, ep_reversal: str) -> str:
        """单集兜底模板。"""
        keyword_text = "、".join(request.keywords[:3]) if request.keywords else request.theme[:8]
        scenes: list[str] = []
        for i in range(1, scenes_per_ep + 1):
            scenes.append(f"""场景号：{i}
内外景：内景
时间：夜晚
地点：主场景
角色造型&情绪：主角，当前情绪紧绷，面对第{ep_num}集核心冲突。
动作描述：围绕"{request.theme}"推进，{act_info}，完成反转"{ep_reversal}"。关键词：{keyword_text}。
对白：
主角（紧迫）：这一次，我不会再退。
分镜提示：中景推近景，聚焦主角情绪爆发点。""")
        return "\n\n".join(scenes)

    # ===================== 多轮对话：场景级编辑 =====================

    def edit_scene(self, edit_req: EditRequest) -> ScriptDraft:
        """根据用户指令，只修改剧本中指定场景，保留其余场景不变。"""
        scene_text = self._extract_scene(edit_req.original_script, edit_req.scene_number)
        if not scene_text:
            return ScriptDraft(
                title=edit_req.title,
                content=edit_req.original_script,
                metadata={"generation_mode": "edit_no_scene_found", "error_reason": f"未找到场景{edit_req.scene_number}"},
            )
        history_text = ""
        if edit_req.conversation_history:
            history_text = "\n".join(
                f"{'用户' if msg.get('role') == 'user' else 'AI'}：{msg.get('content', '')}"
                for msg in edit_req.conversation_history[-6:]
            )

        style_text = ""
        writing_tone = ""
        if edit_req.request_meta:
            style_text = "、".join(edit_req.request_meta.get("styles", []))
            writing_tone = edit_req.request_meta.get("writing_tone", "")

        system_prompt = "你是短剧编剧修改Agent。用户会指定要修改剧本中的某个场景，你只需要输出修改后的该场景完整内容，保持场景结构（场景号、内外景、时间、地点、角色造型&情绪、动作描述、对白、分镜提示）不变。不要输出其他场景，不要解释，不要Markdown。"

        user_prompt = f"""请修改以下场景（场景{edit_req.scene_number}），按照用户指令调整内容。

用户修改指令：{edit_req.instruction}

当前场景内容：
{scene_text}

{f'对话上下文：{chr(10)}{history_text}' if history_text else ''}
{f'风格要求：{style_text} / {writing_tone}' if style_text else ''}

输出要求：
1. 只输出修改后的场景{edit_req.scene_number}完整内容。
2. 必须保持场景结构字段完整：场景号、内外景、时间、地点、角色造型&情绪、动作描述、对白、分镜提示。
3. 根据用户指令重点修改对应部分，其余部分保持不变或适当微调以保持连贯。
4. 不要输出其他场景，不要解释。"""

        try:
            edited_scene = self._normalize_api_script(
                self.llm.complete(system_prompt, user_prompt, temperature=0.25, max_tokens=1200)
            )
            new_script = self._replace_scene(edit_req.original_script, edit_req.scene_number, edited_scene)
            return ScriptDraft(
                title=edit_req.title,
                content=new_script,
                metadata={"generation_mode": "scene_edit", "edited_scene": str(edit_req.scene_number)},
            )
        except Exception as exc:
            return ScriptDraft(
                title=edit_req.title,
                content=edit_req.original_script,
                metadata={"generation_mode": "scene_edit_failed", "error_reason": str(exc)},
            )

    def _extract_scene(self, script: str, scene_number: int) -> str:
        """从完整剧本中提取指定场景号的文本块。"""
        pattern = re.compile(
            rf"(?:^|\n)\s*(?:【?场景\s*{scene_number}】?|场景号[:：]?\s*{scene_number})(?:\s|[:：]|$)"
        )
        match = pattern.search(script)
        if not match:
            return ""
        start = match.start()
        next_pattern = re.compile(
            rf"(?:^|\n)\s*(?:【?场景\s*{scene_number + 1}】?|场景号[:：]?\s*{scene_number + 1})(?:\s|[:：]|$)"
        )
        next_match = next_pattern.search(script)
        end = next_match.start() if next_match else len(script)
        return script[start:end].strip()

    def _replace_scene(self, script: str, scene_number: int, new_scene: str) -> str:
        """将剧本中指定场景替换为新内容。"""
        pattern = re.compile(
            rf"(?:^|\n)\s*(?:【?场景\s*{scene_number}】?|场景号[:：]?\s*{scene_number})(?:\s|[:：]|$)"
        )
        match = pattern.search(script)
        if not match:
            return script
        start = match.start()
        next_pattern = re.compile(
            rf"(?:^|\n)\s*(?:【?场景\s*{scene_number + 1}】?|场景号[:：]?\s*{scene_number + 1})(?:\s|[:：]|$)"
        )
        next_match = next_pattern.search(script)
        end = next_match.start() if next_match else len(script)
        prefix = script[:start].rstrip()
        suffix = script[end:]
        if prefix:
            return f"{prefix}\n\n{new_scene.strip()}\n\n{suffix.strip()}".strip()
        return f"{new_scene.strip()}\n\n{suffix.strip()}".strip()
