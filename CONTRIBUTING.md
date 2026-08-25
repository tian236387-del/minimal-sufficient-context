# Contributing

感谢你帮助改进 Minimal Sufficient Context。项目当前优先让分支上下文工作流更清晰、可靠和可验证，而不是快速增加更多工具。

## 开始之前

1. 先搜索现有 Issue，确认问题没有重复。
2. Bug 请附最小复现步骤、操作系统、Python / Node / Ollama 版本和模型名称。
3. 功能建议请说明它改善了哪一段核心路径：提问、Fork、A/B 对比或 Context 检查。
4. 不要提交对话数据库、模型文件、浏览器 Profile 或真人实验原始数据。

## 本地验证

后端：

```powershell
cd UI-V0.1/backend
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

外部验证工具：

```powershell
python -B -m unittest discover -s First/validation/tests -v
python -B First/validation/run_matrix.py --dry-run
python -B First/validation/human_study/study_server.py --check
```

前端：

```powershell
cd UI-V0.1/frontend
npm ci
npm run build
```

## Pull Request

- 保持改动聚焦，避免无关重构和生成文件。
- 行为变化应更新测试和文档。
- UI 改动请同时检查桌面与移动布局。
- 不要把自动筛查结果描述为真实用户质量结论。
- 提交前确认 CI 中的全部检查通过。

