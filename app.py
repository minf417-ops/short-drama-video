import os
import threading
import time
import traceback
from uuid import uuid4

from flask import Flask, jsonify, render_template, request, send_file, session
from dotenv import load_dotenv

from agents import DirectorAgent, PlannerAgent, ReviewerAgent
from agents.schemas import EditRequest, PlanOutline, UserRequest
from agents.writer import WriterAgent
from services import ExportService, JobStore, LLMService, VideoPipelineService


load_dotenv(dotenv_path=".env", override=True, encoding="utf-8")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "outputs")

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-me")

llm_service = LLMService()
director_agent = DirectorAgent(llm_service)
planner_agent = PlannerAgent(llm_service)
writer_agent = WriterAgent(llm_service)
reviewer_agent = ReviewerAgent(llm_service)
job_store = JobStore()
export_service = ExportService(OUTPUT_DIR)
video_pipeline_service = VideoPipelineService(os.path.join(OUTPUT_DIR, "video_mvp"))


def _get_session_id() -> str:
    """为当前浏览器会话生成并缓存 session_id，用于把多次请求归到同一用户会话。"""
    if "session_id" not in session:
        session["session_id"] = str(uuid4())
    return session["session_id"]


def _build_request(form: dict) -> UserRequest:
    """把前端 JSON 请求转换成内部统一的 UserRequest，顺便做默认值和类型兜底。"""
    def _safe_int(value: object, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    keywords_text = form.get("keywords", "")
    keywords = [item.strip() for item in keywords_text.split(",") if item.strip()]
    raw_styles = form.get("styles", [])
    if isinstance(raw_styles, str):
        import json as _json
        try:
            parsed = _json.loads(raw_styles)
            if isinstance(parsed, list):
                styles = [str(s).strip() for s in parsed if str(s).strip()]
            else:
                styles = [item.strip() for item in raw_styles.split(",") if item.strip()]
        except (ValueError, TypeError):
            styles = [item.strip() for item in raw_styles.split(",") if item.strip()]
    elif isinstance(raw_styles, list):
        styles = [str(item).strip() for item in raw_styles if str(item).strip()]
    else:
        styles = []
    if not styles:
        fallback_style = form.get("style", "都市悬疑").strip()
        styles = [fallback_style] if fallback_style else ["都市悬疑"]
    return UserRequest(
        theme=form.get("theme", "").strip(),
        keywords=keywords,
        audience=form.get("audience", "泛娱乐用户").strip(),
        styles=styles,
        writing_tone=form.get("writing_tone", "影视化强张力").strip() or "影视化强张力",
        episodes=max(_safe_int(form.get("episodes", 1), 1), 1),
        episode_duration=max(_safe_int(form.get("episode_duration", 60), 60), 15),
        extra_requirements=form.get("extra_requirements", "").strip(),
    )


def _should_generate_video(payload: dict) -> bool:
    """兼容布尔值和字符串开关，决定这次生成是否继续进入视频流水线。"""
    raw_value = payload.get("generate_video", False)
    if isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, str):
        return raw_value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(raw_value)


def _has_parseable_script(result: dict) -> bool:
    script_text = str(result.get("script", "")).strip()
    if not script_text:
        return False
    return any(token in script_text for token in ["场景", "对白", "内景", "外景", "动作描述"]) and len(script_text) >= 60


def _merge_session_result_with_payload(session_result: dict, payload: dict) -> dict:
    merged = dict(session_result or {})
    if payload.get("script"):
        merged["script"] = str(payload.get("script", "")).strip()
    if payload.get("title"):
        merged["title"] = str(payload.get("title", "")).strip()
    if payload.get("outline") is not None:
        merged["outline"] = payload.get("outline")
    if payload.get("request_meta") is not None:
        merged["request_meta"] = payload.get("request_meta")
    return merged


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    info = llm_service.runtime_info()
    return jsonify({"ok": True, "llm_available": info["available"], "llm": info})


def _outline_to_dict(outline: PlanOutline) -> dict:
    """PlanOutline dataclass → 可 JSON 序列化的 dict。"""
    return {
        "title": outline.title,
        "opening_hook": outline.opening_hook,
        "core_conflict": outline.core_conflict,
        "reversals": outline.reversals,
        "ending_hook": outline.ending_hook,
        "three_act_outline": outline.three_act_outline,
    }


def _dict_to_outline(d: dict) -> PlanOutline:
    """dict → PlanOutline dataclass。"""
    return PlanOutline(
        title=d.get("title", ""),
        opening_hook=d.get("opening_hook", ""),
        core_conflict=d.get("core_conflict", ""),
        reversals=d.get("reversals", []),
        ending_hook=d.get("ending_hook", ""),
        three_act_outline=d.get("three_act_outline", []),
    )


def _request_to_dict(req: UserRequest) -> dict:
    """UserRequest dataclass → dict。"""
    return {
        "theme": req.theme,
        "keywords": req.keywords,
        "audience": req.audience,
        "styles": req.styles,
        "writing_tone": req.writing_tone,
        "episodes": req.episodes,
        "episode_duration": req.episode_duration,
        "extra_requirements": req.extra_requirements,
    }


def _dict_to_request(d: dict) -> UserRequest:
    """dict → UserRequest dataclass。"""
    return UserRequest(
        theme=d.get("theme", ""),
        keywords=d.get("keywords", []),
        audience=d.get("audience", ""),
        styles=d.get("styles", []),
        writing_tone=d.get("writing_tone", ""),
        episodes=d.get("episodes", 1),
        episode_duration=d.get("episode_duration", 60),
        extra_requirements=d.get("extra_requirements", ""),
    )


# ============================================================
#  新工作流：大纲生成 → 用户审阅/修改 → 确认 → 正文生成 → 审校 → 用户审阅/修改 → 导出
# ============================================================

@app.route("/api/generate_outline", methods=["POST"])
def generate_outline():
    """步骤1：仅生成大纲，返回给用户审阅。"""
    payload = request.get_json() or {}
    user_request = _build_request(payload)
    if not user_request.theme:
        return jsonify({"error": "主题不能为空"}), 400
    if llm_service.strict_api and not llm_service.is_available():
        return jsonify({"error": "模型服务不可用，请检查 ARK_API_KEY / ARK_BASE_URL / ARK_MODEL 配置后重试"}), 503

    session_id = _get_session_id()
    job_id = job_store.create_job({"session_id": session_id, "type": "outline", "theme": user_request.theme})
    job_store.set_status(job_id, "running")

    def task() -> None:
        try:
            runtime = llm_service.runtime_info()
            mode_label = "严格API模式" if runtime["strict_api"] else "标准模式"
            job_store.add_log(job_id, f"LLM模式：{mode_label} | 模型：{runtime['model']}")
            job_store.add_log(job_id, "Planner：生成大纲、冲突点与反转设计")
            outline = planner_agent.run(user_request, logger=lambda msg: job_store.add_log(job_id, msg))
            is_fallback = "开场3秒内抛出与" in outline.opening_hook
            if is_fallback:
                job_store.add_log(job_id, "Planner：API生成大纲失败，已切换为结构化兜底大纲")
            outline_dict = _outline_to_dict(outline)
            job_store.add_log(job_id, f"Planner：大纲生成完成，请审阅并修改")
            session_data = {
                "outline": outline_dict,
                "user_request": _request_to_dict(user_request),
                "is_fallback": is_fallback,
            }
            job_store.save_session_outline(session_id, session_data)
            job_store.set_result(job_id, {
                "outline": outline_dict,
                "is_fallback": is_fallback,
                "user_request": _request_to_dict(user_request),
            })
        except Exception as exc:
            job_store.set_error(job_id, f"{exc}\n{traceback.format_exc()}")

    threading.Thread(target=task, daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/api/edit_outline", methods=["POST"])
def edit_outline():
    """用户对大纲提出修改意见，LLM 修改后返回更新后的大纲。"""
    payload = request.get_json() or {}
    instruction = payload.get("instruction", "").strip()
    if not instruction:
        return jsonify({"error": "修改指令不能为空"}), 400

    session_id = _get_session_id()
    session_data = job_store.get_session_outline(session_id)
    if not session_data or not session_data.get("outline"):
        return jsonify({"error": "当前会话无已生成的大纲，请先生成大纲"}), 400

    outline_dict = session_data["outline"]
    user_request = _dict_to_request(session_data.get("user_request", {}))

    job_store.add_outline_chat(session_id, "user", instruction)

    try:
        updated_outline = planner_agent.edit_outline(outline_dict, instruction, user_request)
        job_store.update_session_outline(session_id, updated_outline)
        job_store.add_outline_chat(session_id, "assistant", "大纲已根据您的意见修改")
        return jsonify({"ok": True, "outline": updated_outline})
    except Exception as exc:
        return jsonify({"error": f"大纲修改失败：{exc}"}), 500


@app.route("/api/confirm_outline", methods=["POST"])
def confirm_outline():
    """用户确认大纲，触发 Writer + Reviewer 生成正文。"""
    session_id = _get_session_id()
    session_data = job_store.get_session_outline(session_id)
    if not session_data or not session_data.get("outline"):
        return jsonify({"error": "当前会话无已确认的大纲，请先生成并审阅大纲"}), 400

    outline_dict = session_data["outline"]
    user_request = _dict_to_request(session_data.get("user_request", {}))
    outline = _dict_to_outline(outline_dict)

    job_id = job_store.create_job({"session_id": session_id, "type": "script", "theme": user_request.theme})
    job_store.set_status(job_id, "running")

    def task() -> None:
        try:
            job_store.add_log(job_id, "Writer：开始编写场景、动作与对白")
            started_at = time.time()
            draft = writer_agent.run(user_request, outline, logger=lambda msg: job_store.add_log(job_id, msg))
            mode = draft.metadata.get("generation_mode", "unknown")
            elapsed = time.time() - started_at
            if mode == "fallback":
                job_store.add_log(job_id, f"Writer：API生成失败，已切换为兜底剧本生成 | 耗时 {elapsed:.1f}s")
            else:
                job_store.add_log(job_id, f"Writer：已通过API生成初稿 | 耗时 {elapsed:.1f}s")

            job_store.add_log(job_id, "Reviewer：检查逻辑、格式、节奏与风格统一性")
            started_at = time.time()
            review = reviewer_agent.run(user_request, draft)
            review_mode = review.metadata.get("review_mode", "unknown")
            elapsed = time.time() - started_at
            if review_mode == "fallback":
                job_store.add_log(job_id, f"Reviewer：API审校失败，已保留当前稿件 | 耗时 {elapsed:.1f}s")
            else:
                job_store.add_log(job_id, f"Reviewer：审校完成 | 耗时 {elapsed:.1f}s")

            final_script = draft.content
            if review.approved and review.polished_script and len(review.polished_script.strip()) >= len(draft.content.strip()) * 0.8:
                final_script = review.polished_script

            job_store.add_log(job_id, "Planner：根据剧本内容生成具体化标题")
            title = planner_agent.generate_title(final_script, outline_dict, user_request)
            job_store.add_log(job_id, f"剧本标题：{title}")

            result = {
                "title": title,
                "outline": outline_dict,
                "request_meta": {
                    "styles": user_request.styles,
                    "writing_tone": user_request.writing_tone,
                    "episodes": user_request.episodes,
                    "episode_duration": user_request.episode_duration,
                },
                "script": final_script,
                "review": {
                    "approved": review.approved,
                    "feedback": review.feedback,
                    "mode": review_mode,
                },
                "generation": {
                    "writer_mode": mode,
                },
                "script_status": director_agent._script_completeness(user_request, final_script),
            }
            job_store.set_result(job_id, result)
            job_store.save_session_script(session_id, result)
            job_store.add_log(job_id, "正文生成完成，请审阅")
        except Exception as exc:
            job_store.set_error(job_id, f"{exc}\n{traceback.format_exc()}")

    threading.Thread(target=task, daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/api/review_script", methods=["POST"])
def review_script():
    """用户对正文提出修改意见，Writer 修订后返回。"""
    payload = request.get_json() or {}
    instruction = payload.get("instruction", "").strip()
    if not instruction:
        return jsonify({"error": "修改指令不能为空"}), 400

    session_id = _get_session_id()
    session_result = job_store.get_session_script(session_id) if job_store.has_session(session_id) else {}
    session_result = _merge_session_result_with_payload(session_result, payload)
    if not _has_parseable_script(session_result):
        return jsonify({"error": "当前会话无已生成的剧本，请先生成剧本"}), 400

    original_script = session_result.get("script", "")
    title = session_result.get("title", "")
    outline = session_result.get("outline")
    request_meta = session_result.get("request_meta", {})

    job_store.add_conversation_message(session_id, "user", f"剧本修改：{instruction}")

    system_prompt = "你是短剧编剧修改Agent。用户会对已有剧本提出整体修改意见，你需要根据用户指令修改剧本并输出完整的修改后剧本。保持场景结构完整，不要输出Markdown、代码块或解释，只输出剧本正文。"
    style_text = "、".join(request_meta.get("styles", []))
    writing_tone = request_meta.get("writing_tone", "")

    user_prompt = f"""请根据用户意见修改以下剧本：

用户修改意见：{instruction}

当前剧本：
{original_script}

风格要求：{style_text} / {writing_tone}

输出要求：
1. 根据用户意见修改对应部分，其余部分保持不变。
2. 保持场景结构完整（场景号、内外景、时间、地点、角色造型&情绪、动作描述、对白、分镜提示）。
3. 风格和节奏不变。
4. 只输出修改后的完整剧本正文。"""

    try:
        revised = llm_service.complete(system_prompt, user_prompt, temperature=0.25, max_tokens=8000)
        revised = revised.strip()
        if len(revised) < len(original_script) * 0.5:
            revised = original_script

        job_store.update_session_script(session_id, revised)
        job_store.add_conversation_message(session_id, "assistant", "剧本已根据您的意见修改")

        updated_result = job_store.get_session_script(session_id)
        exports = None
        if revised != original_script:
            try:
                docx_path = export_service.export_docx(title, revised)
                pdf_path = export_service.export_pdf(title, revised)
                exports = {"docx": os.path.basename(docx_path), "pdf": os.path.basename(pdf_path)}
            except Exception:
                pass

        return jsonify({
            "ok": True,
            "script": revised,
            "title": title,
            "exports": exports,
        })
    except Exception as exc:
        return jsonify({"error": f"剧本修改失败：{exc}"}), 500


@app.route("/api/confirm_script", methods=["POST"])
def confirm_script():
    """用户确认最终剧本，导出文档。"""
    session_id = _get_session_id()
    if not job_store.has_session(session_id):
        return jsonify({"error": "当前会话无已生成的剧本"}), 400

    session_result = job_store.get_session_script(session_id)
    if not _has_parseable_script(session_result):
        return jsonify({"error": "当前剧本不可解析，请先确保剧本完整"}), 400

    title = session_result.get("title", "短剧")
    script = session_result.get("script", "")

    request_meta = session_result.get("request_meta", {})
    user_request = _dict_to_request(request_meta) if request_meta else UserRequest(theme="", keywords=[])

    try:
        docx_path = export_service.export_docx(title, script)
        pdf_path = export_service.export_pdf(title, script)
        return jsonify({
            "ok": True,
            "title": title,
            "script": script,
            "script_status": director_agent._script_completeness(user_request, script),
            "exports": {
                "docx": os.path.basename(docx_path),
                "pdf": os.path.basename(pdf_path),
            },
        })
    except Exception as exc:
        return jsonify({"error": f"导出失败：{exc}"}), 500


@app.route("/api/generate", methods=["POST"])
def generate():
    """项目主入口：
    1. 接收用户自定义输入
    2. 构建结构化请求并校验
    3. 异步触发 Director 多 Agent 工作流
    4. 在剧本完整时导出文档，并可选继续生成视频
    """
    payload = request.get_json() or {}
    user_request = _build_request(payload)
    if not user_request.theme:
        return jsonify({"error": "主题不能为空"}), 400
    if llm_service.strict_api and not llm_service.is_available():
        return jsonify({"error": "模型服务不可用，请检查 ARK_API_KEY / ARK_BASE_URL / ARK_MODEL 配置后重试"}), 503

    session_id = _get_session_id()
    job_id = job_store.create_job({"session_id": session_id, "theme": user_request.theme})
    job_store.set_status(job_id, "running")

    def task() -> None:
        """后台线程真正执行长链路任务，避免接口请求长时间阻塞。

        这里把“剧本生成”和“视频生成”都挂到同一个 job 上，前端只需要轮询一个 job_id
        就能看到日志、错误和最终产物。
        """
        try:
            runtime = llm_service.runtime_info()
            mode_label = "严格API模式" if runtime["strict_api"] else "标准模式"
            job_store.add_log(job_id, f"LLM模式：{mode_label} | 模型：{runtime['model']} | 超时：{runtime['timeout_seconds']}秒 | 最大输出Token：{runtime.get('max_completion_tokens', 'unknown')}")
            result = director_agent.run(user_request, lambda message: job_store.add_log(job_id, message))
            result["llm"] = llm_service.runtime_info()
            script_status = result.get("script_status", {})
            should_generate_video = _should_generate_video(payload)
            allow_best_effort_video = should_generate_video and _has_parseable_script(result)
            is_complete = script_status.get("is_complete", False)
            warnings: list[str] = []
            if is_complete:
                docx_path = export_service.export_docx(result["title"], result["script"])
                pdf_path = export_service.export_pdf(result["title"], result["script"])
                result["exports"] = {
                    "docx": os.path.basename(docx_path),
                    "pdf": os.path.basename(pdf_path),
                }
            else:
                result["exports"] = None
                if allow_best_effort_video:
                    warning = "剧本未完全通过审校，已切换为 best-effort 视频生成模式"
                    warnings.append(warning)
                    job_store.add_log(job_id, f"VideoPipeline：{warning}")
                else:
                    job_store.set_result(job_id, result)
                    if _has_parseable_script(result):
                        job_store.save_session_script(session_id, result)
                    job_store.set_error(job_id, "生成结果不完整，请重试或缩短单次生成范围")
                    return
            if should_generate_video:
                # 视频能力建立在“最终剧本已经通过基础完整性校验”之上，
                # 避免把结构不完整的文本继续放大成更难排查的视频问题。
                job_store.add_log(job_id, "VideoPipeline：开始基于最终剧本生成视频" if is_complete else "VideoPipeline：开始基于可解析剧本执行 best-effort 视频生成")
                video_result = video_pipeline_service.build_project(
                    title=result["title"],
                    theme=user_request.theme,
                    script_text=result["script"],
                    target_duration_seconds=min(max(user_request.episode_duration, 15), 18),
                    max_duration_seconds=min(max(user_request.episode_duration, 18), 20),
                )
                render_plan = video_result["render_plan"]
                result["video"] = {
                    "project_dir": video_result["project_dir"],
                    "output_video_path": render_plan["output_video_path"],
                    "subtitle_path": render_plan["subtitle_path"],
                    "timeline_count": len(render_plan["timeline"]),
                    "notes": render_plan["notes"],
                }
                job_store.add_log(job_id, f"VideoPipeline：完成，输出视频 {render_plan['output_video_path']}")
            if warnings:
                result["warnings"] = warnings
            job_store.set_result(job_id, result)
            job_store.save_session_script(session_id, result)
        except Exception as exc:
            job_store.set_error(job_id, f"{exc}\n{traceback.format_exc()}")

    threading.Thread(target=task, daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/api/jobs/<job_id>")
def get_job(job_id: str):
    job = job_store.get_job(job_id)
    if not job:
        return jsonify({"error": "任务不存在"}), 404
    return jsonify(job)


@app.route("/api/edit_scene", methods=["POST"])
def edit_scene():
    """多轮对话接口：用户针对某个场景发起修改。

    前端传入 scene_number + instruction，后端从会话中取出当前剧本，
    调用 WriterAgent.edit_scene 只修改目标场景，返回更新后的完整剧本。
    """
    payload = request.get_json() or {}
    scene_number = payload.get("scene_number")
    instruction = payload.get("instruction", "").strip()
    if not scene_number or not instruction:
        return jsonify({"error": "scene_number 和 instruction 不能为空"}), 400

    session_id = _get_session_id()
    session_result = job_store.get_session_script(session_id) if job_store.has_session(session_id) else {}
    session_result = _merge_session_result_with_payload(session_result, payload)
    if not _has_parseable_script(session_result):
        return jsonify({"error": "当前会话无已生成的剧本，请先生成剧本"}), 400

    original_script = session_result.get("script", "")
    title = session_result.get("title", "")
    outline = session_result.get("outline")
    request_meta = session_result.get("request_meta")
    conversation_history = job_store.get_conversation_history(session_id)

    if not job_store.has_session(session_id):
        job_store.save_session_script(session_id, session_result)

    job_store.add_conversation_message(session_id, "user", f"修改场景{scene_number}：{instruction}")

    raw_scene_num = int(str(scene_number).split('.')[-1]) if '.' in str(scene_number) else int(scene_number)
    edit_req = EditRequest(
        scene_number=raw_scene_num,
        instruction=instruction,
        original_script=original_script,
        title=title,
        outline=outline,
        request_meta=request_meta,
        conversation_history=conversation_history,
    )

    try:
        draft = writer_agent.edit_scene(edit_req)
        job_store.update_session_script(session_id, draft.content)
        job_store.add_conversation_message(session_id, "assistant", f"已修改场景{scene_number}")

        updated_result = job_store.get_session_script(session_id)
        if draft.content != original_script:
            docx_path = export_service.export_docx(title, draft.content)
            pdf_path = export_service.export_pdf(title, draft.content)
            updated_result["exports"] = {
                "docx": os.path.basename(docx_path),
                "pdf": os.path.basename(pdf_path),
            }

        return jsonify({
            "ok": True,
            "script": draft.content,
            "title": draft.title,
            "metadata": draft.metadata,
            "exports": updated_result.get("exports"),
        })
    except Exception as exc:
        return jsonify({"error": f"场景编辑失败：{exc}"}), 500


@app.route("/api/generate_video", methods=["POST"])
def generate_video():
    """独立视频生成接口：用户在剧本确认后，可选择单独触发视频生成。"""
    payload = request.get_json() or {}
    session_id = _get_session_id()
    session_result = job_store.get_session_script(session_id) if job_store.has_session(session_id) else {}
    session_result = _merge_session_result_with_payload(session_result, payload)
    if not _has_parseable_script(session_result):
        return jsonify({"error": "当前会话无已生成的剧本，请先生成剧本"}), 400

    if not job_store.has_session(session_id):
        job_store.save_session_script(session_id, session_result)

    script_text = session_result.get("script", "")
    title = session_result.get("title", "")
    request_meta = session_result.get("request_meta", {})

    if not _has_parseable_script(session_result):
        return jsonify({"error": "当前剧本不可解析，请先确保剧本完整"}), 400

    job_id = job_store.create_job({"session_id": session_id, "type": "video_only", "title": title})
    job_store.set_status(job_id, "running")

    episode_duration = request_meta.get("episode_duration", 60)

    def video_task() -> None:
        try:
            job_store.add_log(job_id, "VideoPipeline：开始基于当前剧本生成视频")
            video_result = video_pipeline_service.build_project(
                title=title,
                theme=request_meta.get("theme", title),
                script_text=script_text,
                target_duration_seconds=min(max(episode_duration, 15), 18),
                max_duration_seconds=min(max(episode_duration, 18), 20),
            )
            render_plan = video_result["render_plan"]
            result = {
                "title": title,
                "video": {
                    "project_dir": video_result["project_dir"],
                    "output_video_path": render_plan["output_video_path"],
                    "subtitle_path": render_plan["subtitle_path"],
                    "timeline_count": len(render_plan["timeline"]),
                    "notes": render_plan["notes"],
                },
            }
            job_store.add_log(job_id, f"VideoPipeline：完成，输出视频 {render_plan['output_video_path']}")
            job_store.set_result(job_id, result)
        except Exception as exc:
            job_store.set_error(job_id, f"{exc}\n{traceback.format_exc()}")

    threading.Thread(target=video_task, daemon=True).start()
    return jsonify({"job_id": job_id})


@app.route("/exports/<filename>")
def download_export(filename: str):
    path = os.path.join(OUTPUT_DIR, filename)
    if not os.path.exists(path):
        return jsonify({"error": "文件不存在"}), 404
    return send_file(path, as_attachment=True)


if __name__ == "__main__":
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)
