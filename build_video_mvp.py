from __future__ import annotations

import argparse
import json
import os

from services.video_pipeline import VideoPipelineService


DEFAULT_SCRIPT = """场景1：内景 夜 办公室
林晚站在落地窗前，手机屏幕泛着冷光。
林晚：原来你早就知道了。
顾川：我只是在等你自己开口。

场景2：内景 夜 会议室
顾川把合同推到桌上，空气像凝固了一样。
林晚：你把我当成棋子？
顾川：我是在救你。

场景3：外景 雨夜 天台
雨水打湿两人的衣角，城市灯光在脚下模糊。
林晚：从今天开始，我们两清。
"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a script-to-video MVP project structure.")
    parser.add_argument("--title", default="短剧视频MVP", help="项目标题")
    parser.add_argument("--theme", default="都市情感反转", help="项目主题")
    parser.add_argument("--script-file", default="", help="剧本文本文件路径")
    parser.add_argument("--output-dir", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "outputs", "video_mvp"), help="输出目录")
    parser.add_argument("--target-duration", type=float, default=18.0, help="目标视频时长（秒）")
    parser.add_argument("--max-duration", type=float, default=20.0, help="最大视频时长（秒）")
    args = parser.parse_args()

    script_text = DEFAULT_SCRIPT
    if args.script_file:
        with open(args.script_file, "r", encoding="utf-8") as file:
            script_text = file.read()

    service = VideoPipelineService(base_output_dir=args.output_dir)
    result = service.build_project(
        title=args.title,
        theme=args.theme,
        script_text=script_text,
        target_duration_seconds=args.target_duration,
        max_duration_seconds=args.max_duration,
    )
    print(json.dumps({
        "project_dir": result["project_dir"],
        "scene_count": len(result["project"]["scenes"]),
        "timeline_count": len(result["render_plan"]["timeline"]),
        "subtitle_path": result["render_plan"]["subtitle_path"],
        "notes": result["render_plan"]["notes"],
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
