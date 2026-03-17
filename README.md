# 多Agent短剧剧本生成系统

## 启动

1. 安装依赖

```bash
pip install -r requirements.txt
```

2. 配置环境变量

在项目根目录创建 `.env`，至少填写：

- `ARK_API_KEY`：豆包/火山方舟 API Key
- `ARK_BASE_URL`：OpenAI 兼容网关地址
- `ARK_MODEL`：模型 ID
- `STRICT_LLM_API`：建议 `1`，强制仅使用外部 API
- `ARK_TIMEOUT_SECONDS`：单次模型请求超时秒数
- `ARK_RETRY_ON_TIMEOUT`：超时自动重试次数
- `FLASK_SECRET_KEY`

3. 启动项目

```bash
py app.py
```

4. 浏览器访问

- `http://127.0.0.1:5000`
- `http://<你的局域网IP>:5000`

## 严格API模式说明

- 默认启用 `STRICT_LLM_API=1`
- 启用后，系统不会使用本地 mock 生成剧本
- 若 API 不可用，`/api/generate` 会直接返回 503，避免“看似成功但输出测试剧本”
- 可通过 `GET /api/health` 查看：
  - `llm_available`
  - `llm.model`
  - `llm.base_url`
  - `llm.last_call_mode`

## 功能

- 多 Agent 协作生成短剧剧本
- 支持用户自定义输入
- 实时展示生成过程
- 支持导出 PDF / Word
- 支持严格外部 API 调用与可观测任务日志
- 支持剧本转视频 MVP 工程骨架输出

## 视频 MVP 落地

运行下面的命令可以直接生成一套可替换 Provider 的视频工程骨架：

```bash
py build_video_mvp.py
```

输出目录默认在 `outputs/video_mvp/短剧视频mvp/`，其中包含：

- `project.json`
- `storyboard/*.json`
- `assets/images/*.json`
- `assets/audio/*.wav`
- `assets/video/*.json`
- `output/subtitles.srt`
- `output/render_plan.json`

当前版本中：

- 图片资产使用占位 JSON
- TTS 使用静音 `wav` 占位
- 视频资产使用占位 JSON
- `render_plan.json` 中已预留后续 `ffmpeg` 合成命令
