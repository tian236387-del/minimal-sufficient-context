# Minimal Sufficient Context

Minimal Sufficient Context (MSC) 是一个连接本地 Ollama 的分支感知 AI 工作台。它把对话保存为消息树，只将当前分支的祖先路径发送给模型，并提供 Linear / Branch A/B 对比和可检查的 Context Diff。

> 当前版本：**V0.2.1 Public Beta**。这是本地、单用户产品，不应直接作为公网多用户服务部署。

## 为什么做这个产品

普通线性聊天会把所有历史继续塞给模型。用户探索多个方案后，其他分支中的旧假设可能污染当前回答。MSC 让用户从任意消息 Fork，并明确展示哪些消息被纳入、排除或因 Token 预算被裁剪。

## 核心能力

- 命名分支、活动分支持久化、分支删除和 Markdown / JSON 导出
- Ollama 流式输出，完整成功后才原子写入 SQLite
- 每个对话独立的 Token 预算和连续历史裁剪
- 同一问题的 Linear / Branch 一键 A/B 对比
- Context Inspector、Context Diff 和实际 Provider Token
- 带来源引用的 Summary、冲突预览、可回滚 Merge 和 DAG 审计
- 数据库迁移、升级前备份、外键与事务完整性检查

## 快速开始

需要 Python 3.12+、Node.js 22+、Ollama 和 Git。

```powershell
ollama pull qwen3:4b
```

启动后端：

```powershell
cd UI-V0.1/backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
```

在另一个终端启动前端：

```powershell
cd UI-V0.1/frontend
npm ci
npm run dev -- --host 127.0.0.1
```

打开 `http://127.0.0.1:5173`。Windows 用户也可以在 `UI-V0.1/` 中使用 `run_backend.bat` 和 `run_frontend.bat`。

macOS / Linux 的后端 Python 路径为 `./.venv/bin/python`。完整使用说明见 [产品文档](UI-V0.1/README.md)。

## 推荐体验

1. 在 Main 分支连续提问两轮。
2. 从关键历史消息 Fork，并在新分支探索另一种方案。
3. 用同一个新问题运行 A/B 对比。
4. 在 Context Inspector 中检查 sibling 排除、预算裁剪和 Token 差异。

## 仓库结构

```text
UI-V0.1/   V0.2.1 产品代码、迁移、测试和启动脚本
First/     最小实验、跨模型验证矩阵和真人实验协议
.github/   CI、依赖更新和社区协作配置
```

`UI-V0.1` 是为兼容早期启动脚本保留的目录名，目录中的实际产品版本是 V0.2.1。

## 质量状态

- 后端 API、数据库、Provider、Context Compiler、Summary / Merge：25 个测试
- 外部验证 runner 与真人实验协议：18 个测试
- CI：后端测试、Benchmark dry-run、外部验证检查和前端生产构建
- 自动矩阵：Qwen、Llama、Gemma，4k / 8k / 16k / 32k，多 Seed 和重复实验

自动实验是工程回归证据，不等于产品已经证明对所有真实任务普遍有效。真人实验尚未完成，因此项目不会宣称确定性的质量提升。详情见 [外部验证报告](First/validation/EXTERNAL_VALIDATION_REPORT.md)。

## 隐私与安全

- 对话默认只保存在本地 SQLite，不上传云端。
- 本地数据库、备份、浏览器 Profile 和真人实验原始数据已被 Git 忽略。
- API 默认只绑定 `127.0.0.1`；当前版本没有公网认证，不要直接暴露到互联网。
- 发布前请阅读 [安全策略](SECURITY.md)。

## 参与贡献

欢迎提交复现报告、可用性反馈和聚焦核心工作流的改进。开始前请阅读 [贡献指南](CONTRIBUTING.md)。

## 许可证

本项目采用 [Apache License 2.0](LICENSE)。
