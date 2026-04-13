# 多Agent短剧剧本与视频生成平台
 
 多 Agent 协作的短剧剧本生成与视频化系统。Planner / Writer / Reviewer / Director 四大 AI Agent 从创意到剧本到视频，一站式自动化生产。
 
 支持多集短剧生成、分集标题生成、集-场景层级展示、Word / PDF 导出，以及剧本驱动的视频流水线。
 
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
 - **多集剧本生成**：按“集 → 场景”生成正文，支持单集/多集模式，按单集时长自动估算场景数
 - **短剧化标题能力**：自动生成更适合短剧平台点击传播的总标题与分集标题
 - **分集结构展示**：前端按“第 N 集 / 场景 N.M”展示剧本，便于阅读与逐场修改
 - **现代化 Web UI**：暗色主题、步骤指示器、标签选择器、自定义下拉框、进度条、Agent 状态指示、Tab 切换
 - **实时生成日志**：前端轮询任务日志，展示大纲、正文、分集生成进度
 - **剧本导出**：支持 PDF / Word 导出
 - **导出层级格式**：Word / PDF 按“集标题 → 场景内容”格式输出，适合审阅与交付
 - **视频生成**：基于剧本自动生成视频（即梦 AI 文生视频 → TTS → FFmpeg 合成 → 字幕烧录）
 - **字幕系统**：SRT 字幕自动生成，支持自定义样式（字体、描边、阴影、位置）
 - **异步任务**：后台线程执行，前端实时轮询进度与日志
 
## 剧本生成说明
 
- **输入维度**：主题、关键词、风格、文风、集数、单集时长、额外要求
- **多集模式**：Writer 会按集生成正文，并为每集生成单独集名
- **场景数量**：根据单集时长自动推算单集目标场景数
- **完整性检查**：生成后会统计集数与场景数，供前端展示“完整性”状态
- **场景编辑**：前端支持按场景选择进行局部修改，多集模式下使用 `集号.场景号` 形式展示，如 `4.1`
 
## 导出与前端展示
 
- **网页展示**：剧本正文按“第 N 集”分段显示，每集内再按场景卡片展示
- **场景编号**：多集场景使用 `1.1`、`1.2`、`2.1` 这类编号，便于定位
- **Word/PDF 导出**：导出文件按“第 N 集：集名”作为一级内容块，场景内容逐段排版
- **导出前提**：建议在完整性检查通过后再导出，以减少结构缺失风险
 
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
 5. **字幕渲染**：SRT 生成 + FFmpeg 字幕叠加
 6. **最终合成**：FFmpeg concat 拼接为完整视频
 
 
 `docs/video_demo.mp4` 为项目当前视频生成能力的演示样例，可用于快速预览生成效果。
 
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
 
 ## 当前工作流概览
 
 1. 用户在前端填写短剧需求并生成大纲
 2. 审阅并确认大纲后，进入正文生成阶段
 3. Writer 生成单集或多集剧本，前端实时显示日志
 4. Reviewer 对剧本进行审校和必要润色
 5. 用户可继续做整稿或逐场景修改
 6. 确认剧本后导出 Word / PDF，或进入视频生成流水线
