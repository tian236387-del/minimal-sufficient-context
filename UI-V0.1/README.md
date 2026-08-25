# Minimal Sufficient Context V0.2.1

一个连接本地 Ollama 的 Branch-aware Chat Workbench，用同一棵消息树验证“只给模型最小充分上下文”是否比线性聊天更准确、更省 Token。

> 目录名 `UI-V0.1` 为兼容原有启动脚本而保留，当前产品与前后端版本均为 **V0.2.1**，不需要寻找另一个 V2 文件夹。

## V0.2.1 使用路径

这一版优先优化首次使用体验，而不是增加新的复杂操作：

- 首次打开可以直接选择示例问题创建对话，也可以新建空白对话。
- 顶栏显示 Ollama 连接状态；服务未连接时可直接重试。
- `Context` 和 `A/B` 是主路径，Summary、Merge、DAG 收在“更多”工具中。
- 完成几轮消息后会出现一次可关闭的 Fork 提示，帮助用户发现分支价值。
- Context Inspector 会显示纳入、排除和预算裁剪的消息数量；点击消息可回到原文。
- 分支摘要作为低权限证据数据注入，不再提升为 `system` 指令。

建议先完成一次“提问 -> Fork -> A/B 对比”的闭环，再使用高级工具。

## V0.2 产品能力

- 命名分支：从任意消息创建分支，支持重命名与名称唯一性校验
- 活动状态持久化：记住每个 Conversation 的活动分支与选中消息
- 分支删除：删除分支独有子树，Main 分支受保护
- 分支导出：一键导出 Markdown 或结构化 JSON
- Token 预算：每个 Conversation 独立设置，自动裁剪最旧历史并展示裁剪消息
- 流式回答：通过 SSE 实时显示 Ollama 输出，完整结束后才原子写入数据库
- Linear / Branch A/B：同一个问题同时运行两种上下文策略，对比回答与 Token
- Context Diff：展示共同消息、Linear 独有、Branch 独有及两侧被预算裁剪的消息
- Context Inspector：检查实际 Included Path、Excluded Siblings 和估算 Token
- 响应式工作台：桌面三栏布局，窄屏使用可开合导航与 Inspector

## 快速打开

### 1. 准备 Ollama

```powershell
ollama list
```

默认模型是 `qwen3:4b`。缺少时运行：

```powershell
ollama pull qwen3:4b
```

### 2. 启动后端

双击：

```text
run_backend.bat
```

或在 PowerShell 运行：

```powershell
cd UI-V0.1/backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

健康检查：

```text
http://127.0.0.1:8000/api/health
```

V0.2 应显示 `schema_version: 7`。首次从旧数据库升级时，会先备份到 `backend/backups/`，再执行迁移与历史分支回填。

### 3. 启动前端

双击：

```text
run_frontend.bat
```

或在另一个 PowerShell 运行：

```powershell
cd UI-V0.1/frontend
npm ci
npm run dev -- --host 127.0.0.1
```

打开产品：

```text
http://127.0.0.1:5173
```

## 推荐体验路径

1. 在 Main 连续发送两轮消息。
2. 点击任意历史消息旁的 `Fork`，输入分支名称。
3. 在新分支继续聊天，刷新页面确认活动分支与节点仍然保留。
4. 调小右上角 `Budget`，查看右侧 `Budget Truncated`。
5. 在输入框写一个问题，点击 `A/B 对比`。
6. 在右侧比较 Linear 与 Branch 回答，并检查 `Context Diff`。
7. 在左侧分支操作中导出 Markdown / JSON，或删除非 Main 分支。

## 两种 Context 策略

`Branch` 只包含当前选中消息的祖先链：

```text
root -> ... -> active message -> new user message
```

`Linear` 按消息写入顺序包含该 Conversation 的全部历史，因此会暴露 sibling branch 污染。两种策略使用同一 system prompt、问题、模型与 Token 预算。

Token 数使用透明的本地近似算法做预算预检：ASCII 约 4 字符一个 Token，非 ASCII 约 1 字符一个 Token，并计入消息结构开销。Ollama 返回后，界面同时展示 Provider 的实际 `prompt_tokens`。

## 数据与事务保证

- SQLite 每个连接强制 `PRAGMA foreign_keys = ON`
- migrations 带版本、checksum、备份和完整性检查
- Conversation 删除级联删除 messages 与 branches
- trigger 阻止跨 Conversation 的 parent、branch pointer 和 active state
- 普通聊天与流式聊天均在模型成功后原子写入 user + assistant
- Provider 失败、流中断或用户停止生成时，不保存半条 exchange
- A/B 对比只读，不写入消息历史

## 测试

双击：

```text
run_tests.bat
```

或运行：

```powershell
cd UI-V0.1/backend
.\.venv\Scripts\python.exe -B -m unittest discover -s tests -v
```

前端生产构建：

```powershell
cd UI-V0.1/frontend
npm run build
```

CI 位于 `.github/workflows/ci.yml`，会执行后端测试、Benchmark V2.2 dry-run 和前端构建。

## 数据库备份

双击 `backup_database.bat`，或运行：

```powershell
cd UI-V0.1/backend
.\.venv\Scripts\python.exe -B -m app.backup --destination ".\backups\msc-chat.sqlite3"
```

常用环境变量：

```text
MSC_DB_PATH
MSC_MIGRATIONS_PATH
MSC_BACKUP_PATH
MSC_BACKUP_BEFORE_MIGRATE
MSC_CORS_ORIGINS
OLLAMA_BASE_URL
OLLAMA_MODEL
MSC_PROVIDER_TIMEOUT_SECONDS
VITE_API_BASE
```

## Summary / Merge / DAG (V0.2)

The current backend schema is version 7. New capability is intentionally explicit:

1. Open the `Summary` inspector and create a summary for the active branch.
2. Each summary includes `[m:<id>]` citations and clickable original-message sources.
3. Open `Merge`, choose target and source branches, and run `Preview conflicts`.
4. Resolve every detected conflict, then create the reversible derived merge branch.
5. Use `Rollback` to restore the active state captured before the merge.
6. Open `DAG` to inspect message, branch, summary, merge, and rollback history.

Merge previews do not write to the database. Merge execution requires the preview token;
changed branch heads invalidate it. Raw messages are never rewritten by Summary or Merge.
If a branch deletion removes any cited evidence from a summary, that summary is marked `orphaned`
and is no longer treated as citable.

Useful endpoints:

```text
GET  /api/conversations/{id}/summaries
POST /api/branches/{branch_id}/summaries
POST /api/conversations/{id}/merges/preview
POST /api/conversations/{id}/merges
POST /api/merges/{merge_id}/rollback
GET  /api/conversations/{id}/dag
```

## 后续边界

Summary 与 Merge 已作为本地、可引用、可回滚、可审计的 V0.2 能力实现。
登录、云同步、RAG、消息编辑和多父节点图重写仍不在本版本范围内。
