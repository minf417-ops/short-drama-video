import os
import threading
import traceback
from uuid import uuid4

from flask import Flask, jsonify, render_template, request, send_file, session
from dotenv import load_dotenv

from agents import DirectorAgent
from agents.schemas import UserRequest
from services import ExportService, JobStore, LLMService, VideoPipelineService


load_dotenv(dotenv_path=".env", override=True, encoding="utf-8")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_DIR = os.path.join(BASE_DIR, "outputs")

app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-me")

llm_service = LLMService()
director_agent = DirectorAgent(llm_service)
job_store = JobStore()
export_service = ExportService(OUTPUT_DIR)
video_pipeline_service = VideoPipelineService(os.path.join(OUTPUT_DIR, "video_mvp"))


def _get_session_id() -> str:
    if "session_id" not in session:
        session["session_id"] = str(uuid4())
    return session["session_id"]


def _build_request(form: dict) -> UserRequest:
    def _safe_int(value: object, default: int) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    keywords_text = form.get("keywords", "")
    keywords = [item.strip() for item in keywords_text.split(",") if item.strip()]
    raw_styles = form.get("styles", [])
    if isinstance(raw_styles, str):
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
    raw_value = payload.get("generate_video", False)
    if isinstance(raw_value, bool):
        return raw_value
    if isinstance(raw_value, str):
        return raw_value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(raw_value)


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/health")
def health():
    info = llm_service.runtime_info()
    return jsonify({"ok": True, "llm_available": info["available"], "llm": info})


@app.route("/api/generate", methods=["POST"])
def generate():
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
        try:
            runtime = llm_service.runtime_info()
            mode_label = "严格API模式" if runtime["strict_api"] else "标准模式"
            job_store.add_log(job_id, f"LLM模式：{mode_label} | 模型：{runtime['model']} | 超时：{runtime['timeout_seconds']}秒 | 最大输出Token：{runtime.get('max_completion_tokens', 'unknown')}")
            result = director_agent.run(user_request, lambda message: job_store.add_log(job_id, message))
            result["llm"] = llm_service.runtime_info()
            script_status = result.get("script_status", {})
            if not script_status.get("is_complete", False):
                result["exports"] = None
                job_store.set_result(job_id, result)
                job_store.set_error(job_id, "生成结果不完整，请重试或缩短单次生成范围")
                return
            docx_path = export_service.export_docx(result["title"], result["script"])
            pdf_path = export_service.export_pdf(result["title"], result["script"])
            result["exports"] = {
                "docx": os.path.basename(docx_path),
                "pdf": os.path.basename(pdf_path),
            }
            if _should_generate_video(payload):
                job_store.add_log(job_id, "VideoPipeline：开始基于最终剧本生成视频")
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
            job_store.set_result(job_id, result)
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


@app.route("/exports/<filename>")
def download_export(filename: str):
    path = os.path.join(OUTPUT_DIR, filename)
    if not os.path.exists(path):
        return jsonify({"error": "文件不存在"}), 404
    return send_file(path, as_attachment=True)


if __name__ == "__main__":
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    app.run(host="0.0.0.0", port=5000, debug=True, use_reloader=False)
