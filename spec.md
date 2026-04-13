# AI 短剧创作工坊 — 规格说明

## 1. 目标
构建一个可网页交互的多 Agent 短剧剧本生成与视频化系统。用户输入主题、关键词、目标受众、剧本风格、集数与时长偏好，系统通过多 Agent 协作完成短剧剧本创作、审校、格式化、导出，并可选自动生成视频。

## 2. 多 Agent 架构

### 2.1 Agent 角色
- **Planner Agent**：大纲生成、三幕式结构拆分、冲突点/反转点/Hook 设计
- **Writer Agent**：场景、对白、动作、分镜提示编写；支持单集/多集剧本生成、定向补尾和重写
- **Reviewer Agent**：本地快速规则筛查 + 远程模型审校；逻辑、格式、节奏、风格检查
- **Director Agent**：LangGraph 状态图调度、修订次数管理、完整性检查与最终输出决策

### 2.2 通信方式
- LangGraph 状态图编排：`planner → writer → reviewer → (rewrite | finalize)`
- Director 维护共享 `GraphState`（request / outline / draft / review / logs）
- 最多 1 次自动修订，超限则输出当前最优稿
- 多集模式下 Writer 支持并发生成各集正文，并在汇总后统一生成分集标题

### 2.3 外部 API 接入
- 统一 LLM 服务层，支持 OpenAI 兼容接口（豆包/火山方舟）
- 环境变量配置：`ARK_BASE_URL`、`ARK_API_KEY`、`ARK_MODEL`
- 严格模式下无 API 不降级，直接返回 503

## 3. 专业短剧标准
- 三幕式结构：开端 → 发展 → 高潮/结局
- 前 3 秒强钩子，每 15 秒一个小反转，结尾留 Hook
- 场景格式：场景号、内外景、时间、地点、角色造型&情绪、动作描述、对白、分镜提示
- 分镜提示包含：景别、运镜、转场、焦点人物、表情、微动作
- 多集模式下每集必须有独立集标题，前端展示按“第 N 集 → 场景 N.M”组织
- 总标题应更贴近短剧平台风格，短、强、具悬念或冲突感

## 4. 网页端

### 4.1 输入项
- 主题、关键词、目标受众
- 剧本风格（多选标签）、文字风格（下拉选择）
- 集数、单集时长、补充要求
- 是否同时生成视频（开关）

### 4.2 UI 特性
- 暗色玻璃态主题 + 动态背景粒子
- 三步骤指示器（配置 → 生成 → 结果）
- 标签选择器（多风格融合）
- 自定义下拉框（文字风格）
- 进度条 + Agent 状态芯片（实时反馈）
- Tab 切换（剧本 / 大纲 / 导出）
- 彩色日志（按 Agent 着色）
- 多集剧本按“第 N 集”分段卡片化展示
- 多集场景号显示为 `集号.场景号`，如 `4.1`

### 4.3 功能
- 一键生成、实时日志、进度可视化
- 大纲确认后生成正文，正文阶段展示分集生成进度
- 剧本完整性检查（目标集数 / 实际集数 / 目标场景数 / 实际场景数）
- 导出 PDF / Word
- 视频生成（可选）
- 剧本整体修改
- 逐场景局部修改
- 重新生成

### 4.4 剧本展示与编辑约束
- 单集模式按场景顺序展示
- 多集模式必须先展示集标题，再展示该集内场景，禁止将集标题附着到上一场结尾
- 场景编辑面板与正文展示使用同一套场景解析规则，避免编号错乱或重复
- 完整性信息在结果面板展示，作为导出前参考

## 5. 视频生成流水线

### 5.1 架构
- 独立 `services/video_pipeline` 模块
- 核心分层：script → storyboard → assets → timeline → render plan → video
- 可替换 Provider 抽象：`BaseImageProvider`、`BaseTTSProvider`、`BaseVideoProvider`

### 5.2 已实现 Provider
- **VolcTTSProvider**：火山 TTS，支持多角色声音选择、WAV 合并、流式 JSON 解析
- **JimengVideoProvider**：火山引擎即梦 AI 文生视频，支持异步提交、轮询结果、视频下载与音画重合成
- **FFmpegVideoProvider**：本地兜底视频方案，用于无即梦接口时的基础视频拼接
- **Placeholder 系列**：占位 Provider 用于无 API 环境

### 5.3 字幕系统
- 自动生成 SRT 字幕文件
- FFmpeg force_style 渲染：微软雅黑、加粗、描边、阴影、底部居中
- 对白文本去除说话人前缀，保持画面干净

### 5.4 音画同步
- TTS 音频时长驱动视频片段时长
- 总时长裁剪到目标范围，防止超长
- `-shortest` 参数确保音视频对齐

## 6. 剧本生成与导出规则
- 根据单集时长自动推算目标场景数
- 多集模式下同时统计目标集数、实际集数、目标总场景数、实际总场景数
- 导出内容保留“第 N 集：集名”层级结构
- Word / PDF 导出应与网页端分集结构保持一致

## 7. 技术栈
- **后端**：Flask + LangGraph + OpenAI SDK
- **前端**：原生 HTML/CSS/JS（暗色主题、CSS 动画）
- **LLM**：豆包/火山方舟（OpenAI 兼容）
- **视频**：火山引擎即梦 AI + 火山 TTS + FFmpeg
- **导出**：python-docx（Word）+ ReportLab（PDF）

## 8. 目录结构
- `app.py` — Flask 入口与 API 路由
- `agents/` — 多 Agent 实现（director / planner / writer / reviewer / schemas）
- `services/` — LLM、导出、任务管理、视频流水线
- `templates/` — HTML 页面模板
- `static/` — CSS 样式 + JS 交互逻辑
