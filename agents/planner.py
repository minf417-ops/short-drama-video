from .schemas import UserRequest, PlanOutline
from services.llm_service import LLMService


class PlannerAgent:
    def __init__(self, llm: LLMService) -> None:
        self.llm = llm

    def run(self, request: UserRequest) -> PlanOutline:
        system_prompt = "你是短剧策划Agent。请严格围绕三幕式结构、前3秒强钩子、每15秒一个小反转、结尾留钩子来生成短剧大纲。你必须严格服务于用户指定主题、关键词、风格与文字风格，不得偷换题材，不得擅自改成别的热门母题。不要输出分析过程、解释或Markdown，只输出JSON。"
        style_text = "、".join(request.styles)
        style_rules = self._build_style_rules(request.styles, request.writing_tone)
        user_prompt = f"""
请为以下需求生成结构化短剧策划方案，并输出JSON：
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

JSON字段：
- title
- opening_hook
- core_conflict
- reversals
- ending_hook
- three_act_outline
"""
        try:
            raw = self.llm.complete(
                system_prompt,
                user_prompt,
                temperature=0.25,
                max_tokens=480,
            )
            data = self.llm.parse_json_text(raw)
        except Exception as exc:
            return self._build_fallback_outline(request, str(exc))
        reversals = self._normalize_reversals(data.get("reversals", []))
        three_act_outline = self._normalize_three_act_outline(data.get("three_act_outline", []))
        return PlanOutline(
            title=data.get("title", request.theme[:20] or "短剧标题"),
            opening_hook=data.get("opening_hook", "开场抛出强悬念"),
            core_conflict=data.get("core_conflict", "主角陷入高压冲突"),
            reversals=reversals,
            ending_hook=data.get("ending_hook", "下一集危机升级"),
            three_act_outline=three_act_outline,
        )

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

    def _build_style_rules(self, styles: list[str], writing_tone: str) -> str:
        rules: list[str] = []
        normalized = set(styles)
        if "都市悬疑" in normalized or "悬疑推理" in normalized:
            rules.append("- 都市悬疑/悬疑推理：必须依靠证据、误导、身份真相推进，不要写成纯恋爱线。")
        if "复仇" in normalized:
            rules.append("- 复仇：主角目标必须明确，反击动作要具体可执行。")
        if "霸总" in normalized:
            rules.append("- 霸总：仅允许作为角色气场，不能压过用户主题主线。")
        if "甜宠" not in normalized:
            rules.append("- 未选择甜宠时，禁止撒糖式对白主导剧情。")
        if "权谋" in normalized or "商战" in normalized:
            rules.append("- 权谋/商战：冲突要体现利益交换、布局与反制。")
        rules.append(f"- 文字风格必须稳定保持“{writing_tone}”。")
        return "\n".join(rules)
