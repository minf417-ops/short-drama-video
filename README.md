# AI 短剧创作工坊

多 Agent 协作的短剧剧本生成与视频化系统。Planner / Writer / Reviewer / Director 四大 AI Agent 从创意到剧本到视频，一站式自动化生产。

## 快速启动

1. **安装依赖**

```bash
pip install -r requirements.txt
```

2. **配置环境变量**

在项目根目录创建 `.env`，填写：

| 变量名 | 说明 | 必填 |
|--------|------|------|
| `ARK_API_KEY` | 豆包/火山方舟 API Key | 是 |
| `ARK_BASE_URL` | OpenAI 兼容网关地址 | 是 |
| `ARK_MODEL` | 模型 ID | 是 |
| `STRICT_LLM_API` | 建议 `1`，强制仅使用外部 API | 否 |
| `ARK_TIMEOUT_SECONDS` | 单次模型请求超时（秒） | 否 |
| `ARK_RETRY_ON_TIMEOUT` | 超时自动重试次数 | 否 |
| `ARK_MAX_TOKENS` | 最大输出 Token 数 | 否 |
| `FLASK_SECRET_KEY` | Flask 会话密钥 | 否 |
| `VOLC_VIDEO_REQ_KEY` | 火山即梦视频 req_key | 视频功能 |
| `VOLC_ACCESS_KEY_ID` | 火山 Access Key ID | 视频功能 |
| `VOLC_SECRET_ACCESS_KEY` | 火山 Secret Access Key | 视频功能 |
| `VOLC_TTS_APP_ID` | 火山 TTS App ID | 视频功能 |
| `VOLC_TTS_ACCESS_KEY` | 火山 TTS Access Key | 视频功能 |
| `FFMPEG_BINARY` | FFmpeg 路径 | 视频功能 |

3. **启动项目**

```bash
python app.py
```

4. **浏览器访问**

- `http://127.0.0.1:5000`

## 核心功能

- **多 Agent 协作**：Planner 规划大纲 → Writer 编写剧本 → Reviewer 审校质量 → Director 统一调度
- **现代化 Web UI**：暗色主题、步骤指示器、标签选择器、自定义下拉框、进度条、Agent 状态指示、Tab 切换
- **剧本导出**：支持 PDF / Word 导出
- **视频生成**：基于剧本自动生成视频（即梦 AI 文生视频 → TTS → FFmpeg 合成 → 字幕烧录）
- **字幕系统**：SRT 字幕自动生成，支持自定义样式（字体、描边、阴影、位置）
- **异步任务**：后台线程执行，前端实时轮询进度与日志

## 严格 API 模式

- 默认启用 `STRICT_LLM_API=1`
- 若 API 不可用，`/api/generate` 返回 503
- `GET /api/health` 查看 LLM 状态

## 视频生成流水线

在 Web 界面勾选「同时生成视频」后，系统会在剧本生成完成后自动进入视频流水线：

1. **剧本解析**：结构化拆分为 Scene / Shot / Dialogue
2. **音频生成**：TTS 语音合成（火山 TTS）
3. **视频片段**：即梦 AI 基于镜头提示词生成单镜头视频，再与音频重新合成
4. **时间线构建**：音画对齐，自动裁剪到目标时长
5. **字幕渲染**：SRT 生成 + FFmpeg 字幕叠加（微软雅黑、描边阴影）
6. **最终合成**：FFmpeg concat 拼接为完整视频

输出在 `outputs/video_mvp/` 目录。

## 目录结构

```bash
app.py                          Flask 入口与 API 路由
agents/
  director.py                   LangGraph 状态图调度器
  planner.py                    大纲策划 Agent
  writer.py                     剧本编写 Agent
  reviewer.py                   审校 Agent
  schemas.py                    数据结构定义
services/
  llm_service.py                LLM 统一调用层
  export_service.py             PDF/Word 导出
  job_store.py                  异步任务管理
  video_pipeline/
    pipeline.py                 视频流水线编排
    parser.py                   剧本结构化解析
    providers.py                图像/TTS/视频 Provider
    models.py                   视频数据模型
templates/index.html            前端页面
static/styles.css               样式表（暗色主题）
static/app.js                   前端交互逻辑
```
