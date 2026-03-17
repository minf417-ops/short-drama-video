# 多Agent短剧剧本生成系统规格说明

## 1. 目标
构建一个可网页交互的多 Agent 短剧剧本生成系统。用户可以输入主题、关键词、目标受众、剧本风格、集数与时长偏好，并通过网页发起一键生成。系统通过多 Agent 协作完成短剧剧本创作、审校、格式化与导出。

## 2. 多 Agent 架构

### 2.1 Agent 角色
- Planner Agent
  - 负责大纲生成
  - 负责三幕式结构拆分
  - 负责冲突点、反转点、Hook 设计
- Writer Agent
  - 负责场景、对白、动作、镜头提示的草稿编写
- Reviewer Agent
  - 负责逻辑检查、风格统一、格式校验、违规内容过滤
- Director Agent
  - 负责任务分发、上下文管理、流程编排、结果汇总

### 2.2 通信方式
- 采用 LangGraph 状态图进行层级式编排
- Director 维护共享状态与上下文
- Planner 输出结构化大纲给 Writer
- Writer 输出初稿给 Reviewer
- Reviewer 给出修订建议或通过结果
- 当 Reviewer 未通过时，状态图回到 Writer 进行一次修订
- Director 在结束节点汇总最终结果

### 2.3 外部 API 接入
- 各 Agent 通过统一 LLM 服务层接入外部 API
- 默认支持 OpenAI 兼容接口
- 通过环境变量配置 `ARK_BASE_URL`、`ARK_API_KEY`、`ARK_MODEL`
- 支持无 API 时使用本地 mock 回退

## 3. 专业短剧标准
- 使用三幕式结构：开端、发展、高潮/结局
- 前 3 秒必须吸睛
- 每 15 秒至少一个小反转
- 结尾必须留 Hook
- 格式要求：
  - 场景号
  - 内外景
  - 时间
  - 地点
  - 角色名
  - 动作描述
  - 简洁对白

## 4. 网页端需求
- 输入项：
  - 主题
  - 关键词
  - 目标受众
  - 剧本风格
  - 集数
  - 单集时长
  - 补充要求
- 功能：
  - 一键生成
  - 实时展示生成过程
  - 展示最终剧本
  - 导出 PDF
  - 导出 Word
  - 重置本轮会话

## 5. Windsurf 开发实践
- 使用 spec-driven development
- 后端、前端、Agent、导出服务模块解耦
- 保持可替换的 Agent 与 LLM 服务接口
- 使用 LangGraph 作为 Agent 编排层

## 6. 目录结构
- `app.py` Flask 入口
- `agents/` 多 Agent 实现
- `services/` LLM、导出、任务状态服务
- `templates/` 页面模板
- `static/` 前端资源
- `outputs/` 导出文件
- `DirectorAgent` 基于 LangGraph 调度 Planner / Writer / Reviewer
