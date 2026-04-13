from .schemas import UserRequest, PlanOutline
from services.llm_service import LLMService


class PlannerAgent:
    def __init__(self, llm: LLMService) -> None:
        self.llm = llm

    def run(self, request: UserRequest, logger=None) -> PlanOutline:
        system_prompt = "你是短剧策划Agent。你的任务是生成可直接交给编剧Agent使用的短剧结构化大纲。请严格围绕三幕式结构、前3秒强钩子、每15秒一个小反转、结尾留钩子来输出。你必须严格服务于用户指定主题、关键词、风格与文字风格，不得偷换题材，不得擅自改成别的热门母题。只输出一个合法JSON对象，不要解释，不要Markdown，不要代码块。字段必须完整、可解析。"
        style_text = "、".join(request.styles)
        style_rules = self._build_style_rules(request.styles, request.writing_tone)
        user_prompt = f"""
请为以下需求生成结构化短剧策划方案，并输出严格JSON：
主题：{request.theme}
关键词：{', '.join(request.keywords)}
目标受众：{request.audience}
剧本风格（可融合）：{style_text}
文字风格：{request.writing_tone}
集数：{request.episodes}
单集时长：{request.episode_duration}秒
补充要求：{request.extra_requirements}

硬性要求：
1. 标题、冲突、反转、结尾必须直接围绕“{request.theme}”展开，不能借壳写成别的故事。
2. 风格必须体现“{style_text}”，文字风格必须体现“{request.writing_tone}”。
3. 若主题包含人物关系或身份反转，必须把该关系和反转写进 core_conflict、reversals、three_act_outline。
4. 至少把前4个关键词中的2个真实写入 opening_hook / core_conflict / reversals / ending_hook。
5. 方案要短平快，适配短剧，不要写成大世界观长篇。
6. 风格锁定要求：
{style_rules}
7. `reversals` 必须是数组，至少 3 项；每项可以是字符串，或包含 `summary` 字段的对象。
8. `three_act_outline` 必须是数组，固定 3 项；每项必须包含 `act` 和 `summary`。
9. `title` 必须是2-8字的短剧爆款剧名，像抖音/快手短剧APP上让人忍不住点开的标题。公式参考：身份反转（"闪婚后老公是总裁"）、悬念钩子（"别打开那扇门"）、情绪炸裂（"滚"、"你不配"）、关系冲突（"前妻的逆袭"）、打脸爽文（"被退婚后我无敌了"）。禁止平淡叙述型标题。用书名号包裹。

JSON格式示例（仅示例结构，不要照抄内容）：
{{
  "title": "《剧名》",
  "opening_hook": "开场3秒内的强钩子",
  "core_conflict": "主角要解决的核心矛盾",
  "reversals": [
    {{"summary": "第一次反转"}},
    {{"summary": "第二次反转"}},
    {{"summary": "第三次反转"}}
  ],
  "ending_hook": "结尾钩子",
  "three_act_outline": [
    {{"act": "第一幕", "summary": "建立关系与危机"}},
    {{"act": "第二幕", "summary": "冲突升级与反转"}},
    {{"act": "第三幕", "summary": "阶段性翻盘并抛新钩子"}}
  ]
}}
"""
        try:
            data = self.llm.complete_json(
                system_prompt,
                user_prompt,
                required_fields=["title", "opening_hook", "core_conflict", "reversals", "ending_hook", "three_act_outline"],
                temperature=0.15,
                max_tokens=900,
            )
        except Exception as exc:
            if logger:
                logger(f"Planner：API调用异常 → {exc}")
            return self._build_fallback_outline(request, str(exc))
        reversals = self._normalize_reversals(data.get("reversals", []))
        three_act_outline = self._normalize_three_act_outline(data.get("three_act_outline", []))
        return PlanOutline(
            title=self._normalize_title(data.get("title", request.theme[:20] or "短剧标题")),
            opening_hook=data.get("opening_hook", "开场抛出强悬念"),
            core_conflict=data.get("core_conflict", "主角陷入高压冲突"),
            reversals=reversals,
            ending_hook=data.get("ending_hook", "下一集危机升级"),
            three_act_outline=three_act_outline,
        )

    def _normalize_title(self, value: str) -> str:
        title = str(value or "").strip()
        if not title:
            return "短剧标题"
        title = title.replace("《《", "《").replace("》》", "》")
        if not title.startswith("《"):
            title = f"《{title.strip('《》')}》"
        return title

    def _normalize_reversals(self, value):
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            return [value]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    def _normalize_three_act_outline(self, value):
        if isinstance(value, list):
            normalized = []
            for index, item in enumerate(value, start=1):
                if isinstance(item, dict):
                    normalized.append(
                        {
                            "act": str(item.get("act", f"第{index}幕")).strip() or f"第{index}幕",
                            "summary": str(item.get("summary", item.get("content", ""))).strip() or "情节推进",
                        }
                    )
                elif isinstance(item, str) and item.strip():
                    normalized.append({"act": f"第{index}幕", "summary": item.strip()})
            return normalized
        if isinstance(value, dict):
            return [
                {
                    "act": str(value.get("act", "第一幕")).strip() or "第一幕",
                    "summary": str(value.get("summary", value.get("content", "情节推进"))).strip() or "情节推进",
                }
            ]
        if isinstance(value, str) and value.strip():
            return [{"act": "第一幕", "summary": value.strip()}]
        return []

    def _build_fallback_outline(self, request: UserRequest, error_message: str) -> PlanOutline:
        keywords = request.keywords[:4]
        keyword_text = "、".join(keywords) if keywords else request.theme[:10] or "真相"
        style_text = "、".join(request.styles) if request.styles else "都市悬疑"
        title_seed = request.theme[:12] or "危局"
        opening_hook = f"第1集开场3秒内抛出与“{request.theme}”直接相关的异常证据，现场所有人当场失控。"
        core_conflict = f"主角围绕“{request.theme}”展开连续{request.episodes}集行动，在{style_text}氛围下追查真相并承受反噬，每集都必须推动主线。"
        reversals = []
        for index in range(max(request.episodes, 1)):
            episode_no = index + 1
            reversals.append(f"第{episode_no}集反转：围绕“{keyword_text}”释放新的证据或背刺，局势持续升级。")
        reversals.append("终局反转：主角主动设局反制，结尾抛出下一轮更大危机。")
        three_act_outline = [
            {"act": "第一幕", "summary": f"前半程用强钩子引爆“{request.theme}”，迅速交代{keyword_text}与主角目标，并搭建连续剧推进问题。"},
            {"act": "第二幕", "summary": f"中段围绕{style_text}持续升级冲突，按集推进误导、证据与人物立场翻转，不能把多集压成单集。"},
            {"act": "第三幕", "summary": f"尾段完成关键身份或立场反转，形成第{request.episodes}集结尾钩子或总钩子。"},
        ]
        return PlanOutline(
            title=f"《{title_seed}》",
            opening_hook=opening_hook,
            core_conflict=core_conflict,
            reversals=reversals,
            ending_hook=f"第{request.episodes}集结尾揭开阶段性真相，但新的危险正借“{keyword_text}”逼近。",
            three_act_outline=three_act_outline,
        )

    def edit_outline(self, outline_dict: dict, instruction: str, request: UserRequest) -> dict:
        """根据用户指令修改大纲，返回更新后的大纲字典。"""
        system_prompt = "你是短剧策划Agent。用户会对已有大纲提出修改意见，你需要根据用户指令修改大纲并输出完整的修改后JSON。只输出一个合法JSON对象，不要解释，不要Markdown，不要代码块。"
        user_prompt = f"""当前大纲：
{self._outline_to_text(outline_dict)}

用户修改指令：{instruction}

原始需求：
主题：{request.theme}
关键词：{', '.join(request.keywords)}
风格：{'、'.join(request.styles)}
文字风格：{request.writing_tone}

请根据用户指令修改大纲，输出完整的修改后JSON，格式与原大纲一致。字段必须包含：title, opening_hook, core_conflict, reversals, ending_hook, three_act_outline。"""
        try:
            data = self.llm.complete_json(
                system_prompt,
                user_prompt,
                required_fields=["title", "opening_hook", "core_conflict", "reversals", "ending_hook", "three_act_outline"],
                temperature=0.2,
                max_tokens=900,
            )
            data["title"] = self._normalize_title(data.get("title", outline_dict.get("title", "")))
            data["reversals"] = self._normalize_reversals(data.get("reversals", []))
            data["three_act_outline"] = self._normalize_three_act_outline(data.get("three_act_outline", []))
            return data
        except Exception:
            return outline_dict

    def generate_title(self, script_content: str, outline_dict: dict, request: UserRequest) -> str:
        """根据剧本正文内容生成具体化的标题。"""
        system_prompt = "你是短剧爆款标题专家，专门为竖屏短剧（抖音/快手/短剧APP）起刷屏级标题。只输出JSON。"
        summary = script_content[:600] if len(script_content) > 600 else script_content
        user_prompt = f"""根据剧本内容起一个短剧标题：

剧本摘要：
{summary}

核心冲突：{outline_dict.get('core_conflict', '')}
主题：{request.theme}

标题规则（严格遵守）：
1. 2-8个字，越短越好，用书名号包裹
2. 必须像短剧APP的爆款标题，让人忍不住点开
3. 爆款公式参考（任选一种）：
   - 身份反转：《闪婚后老公是总裁》《退婚后她封神了》
   - 悬念钩子：《别打开那扇门》《她消失的第7天》
   - 情绪炸裂：《滚》《你不配》《凭什么》
   - 关系冲突：《前妻的逆袭》《替嫁毒妃》
   - 打脸爽文：《被退婚后我无敌了》《被全家赶出后》
4. 禁止平淡叙述型标题（如"都市爱情故事""命运的转折"）
5. 从剧本中找到最抓人的人物关系或反转点来命名
6. 只输出JSON：{{"title": "《标题》"}}"""
        try:
            data = self.llm.complete_json(
                system_prompt,
                user_prompt,
                required_fields=["title"],
                temperature=0.5,
                max_tokens=100,
            )
            return self._normalize_title(data.get("title", outline_dict.get("title", "短剧")))
        except Exception:
            return self._normalize_title(outline_dict.get("title", f"《{request.theme[:10]}》"))

    def _outline_to_text(self, outline_dict: dict) -> str:
        """把大纲字典转成可读文本，供 LLM 参考。"""
        lines = [
            f"标题：{outline_dict.get('title', '')}",
            f"开场钩子：{outline_dict.get('opening_hook', '')}",
            f"核心冲突：{outline_dict.get('core_conflict', '')}",
            f"结尾钩子：{outline_dict.get('ending_hook', '')}",
        ]
        reversals = outline_dict.get("reversals", [])
        for i, r in enumerate(reversals, 1):
            if isinstance(r, dict):
                lines.append(f"反转{i}：{r.get('summary', str(r))}")
            else:
                lines.append(f"反转{i}：{r}")
        acts = outline_dict.get("three_act_outline", [])
        for a in acts:
            if isinstance(a, dict):
                lines.append(f"{a.get('act', '幕')}：{a.get('summary', '')}")
            else:
                lines.append(str(a))
        return "\n".join(lines)

    def _build_style_rules(self, styles: list[str], writing_tone: str) -> str:
        rules: list[str] = []
        normalized = set(styles)
        if "都市悬疑" in normalized or "悬疑推理" in normalized:
            rules.append("- 都市悬疑/悬疑推理：必须依靠证据、误导、身份真相推进，不要写成纯恋爱线。")
        if "穿书" in normalized:
            rules.append("- 穿书：必须明确原书身份、既定命运、改命目标与剧情偏移点，不能只口头提一句穿书。")
        if "重生" in normalized:
            rules.append("- 重生：必须体现前世记忆、已知危险与抢先布局的优势。")
        if "年代" in normalized:
            rules.append("- 年代：场景与人物关系要带有年代生活质感、社会规则与现实资源约束。")
        if "复仇" in normalized:
            rules.append("- 复仇：主角目标必须明确，反击动作要具体可执行。")
        if "古风逆袭" in normalized:
            rules.append("- 古风逆袭：必须有身份压制、礼法秩序、公开打脸与阶段性翻盘。")
        if "霸总" in normalized:
            rules.append("- 霸总：仅允许作为角色气场，不能压过用户主题主线。")
        if "甜宠" not in normalized:
            rules.append("- 未选择甜宠时，禁止撒糖式对白主导剧情。")
        if "权谋" in normalized or "商战" in normalized:
            rules.append("- 权谋/商战：冲突要体现利益交换、布局与反制。")
        if "犯罪" in normalized:
            rules.append("- 犯罪：围绕犯罪事件展开，需有作案动机、侦查推进和嫌疑链条。")
        if "穿越" in normalized:
            rules.append("- 穿越：主角必须利用穿越身份/知识形成信息差优势。")
        if "校园" in normalized:
            rules.append("- 校园：场景限定校园及周边，人物关系围绕同学、师生展开。")
        if "家庭伦理" in normalized:
            rules.append("- 家庭伦理：冲突围绕家庭成员之间的利益、情感、代际矛盾。")
        if "科幻" in normalized or "赛博朋克" in normalized:
            rules.append("- 科幻/赛博朋克：世界观需有具体科技细节和未来感视觉元素。")
        if "末世" in normalized:
            rules.append("- 末世：环境必须体现末世特征，生存压力贯穿始终。")
        if "无限流" in normalized:
            rules.append("- 无限流：副本/关卡规则必须明确，限定条件下通关。")
        if "修仙" in normalized or "仙侠" in normalized:
            rules.append("- 修仙/仙侠：宗门体系、法器灵气、修炼境界必须具体。")
        if "宫斗" in normalized:
            rules.append("- 宫斗：等级制度、后宫势力、明争暗斗要具体。")
        if "谍战" in normalized:
            rules.append("- 谍战：潜伏、情报交换、身份危机要有悬念节奏。")
        if "虐恋" in normalized:
            rules.append("- 虐恋：误会、牺牲、被迫分离要有充分铺垫。")
        if "治愈" in normalized:
            rules.append("- 治愈：温暖细节和人物成长要自然渐进。")
        if "暗黑" in normalized:
            rules.append("- 暗黑：道德灰色地带和人性阴暗面通过具体事件展现。")
        if "热血" in normalized:
            rules.append("- 热血：主角面对逆境的信念和爆发要层层递进。")
        rules.append(f"- 文字风格必须稳定保持“{writing_tone}”。")
        return "\n".join(rules)
