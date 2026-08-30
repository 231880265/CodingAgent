# hako Web：Session、Run 与历史架构

> 更新：2026-08-29。本文记录本轮已经落地的设计、状态语义和验收证据；P2 明确不在当前范围。

## 1. 一句话模型

`Workspace` 决定工具操作哪个代码目录，`Session` 代表一次连续工程对话，`Run` 代表用户在这次对话里发送的一条工程目标，`Attachment` 只是某个 Run 新增的文本上下文。当前实现采用“一 Session 一 Python Worker”：

```text
Workspace D:/project/demo
└─ Session A
   ├─ Worker A
   ├─ Agent A + Conversation A
   ├─ Run 1：定位问题
   ├─ Run 2：直接修复
   └─ Run 3：补充测试

同一个 Workspace 还可以创建 Session B，
但 Worker B、Agent B、Conversation B 都是全新的。
```

Conversation 的生命周期跟 Session 绑定，不跟单个 Run 或 Workspace 绑定。因此同一 Session 的后续 Run 会看到前文；点击“新会话”后，即使仍选择同一目录，也不会继承旧 Conversation。

## 2. 本轮范围

| 优先级 | 结果 | 当前状态 |
|---|---|---|
| P0 | 一个 Session 支持多个 Run，共享 Worker、Agent、Conversation 和 workspace | 已实现 |
| P1 | 独立“新会话”；Session 上下文隔离；元数据、Run、消息/工具事件、审批与证据写入 SQLite，可只读查看 | 已实现 |
| P2 | 重新打开历史 Session 并恢复旧 Agent Conversation 后继续对话 | 不实现 |

历史页能复盘“发生过什么”，不能复活已经退出的 Python 对象。界面会明确显示“只读历史”，避免把事件重放冒充上下文恢复。

## 3. 两套状态机

```text
SessionStatus
OPENING → OPEN → CLOSING → CLOSED
    └──────┴──────────────→ FAILED

RunStatus
PENDING → RUNNING → COMPLETED
             ├────→ FAILED
             ├────→ WAITING_APPROVAL → RUNNING
             └────→ CANCELLING → CANCELLED
```

状态转换只在 Spring Boot 服务端执行。`COMPLETED / FAILED / CANCELLED` 与 `CLOSED / FAILED` 都是不可逆终态；晚到事件不能把 `CANCELLED` 改回 `COMPLETED`，也不能把 `CLOSED` 改回 `OPEN`。

## 4. 取消与新会话不是一回事

普通“停止本轮”只取消 Run：

```text
Run RUNNING / WAITING_APPROVAL
→ CANCELLING
→ 终止当前 run_command 的进程树
→ CANCELLED

Session 仍为 OPEN
Worker、Agent、Conversation 继续保活
已落盘文件继续保留
```

拒绝某次审批也不是取消：`DENY` 会作为一条 tool observation 返回 Agent，Agent 可以改做只读调查或选择风险更低的方案。

“新会话”才关闭 Session：若当前 Run 活跃，前端先等待它真正进入 `CANCELLED`，再请求 `Session → CLOSING`，等待 Worker 退出和 `CLOSED`，最后清空旧时间线。任何一步超时都不会提前创建新 Session。取消停止后续行为，不提供 Undo；已经写入的文件不会自动回滚。

Windows 下 `run_command` 使用独立进程组。取消时先向整组发送 `CTRL_BREAK`，再以 `taskkill /T /F` 兜底，目标是结束本次编译/测试命令树而不杀承载 Conversation 的 Python Worker。

## 5. 事件身份与迟到事件

统一事件信封：

```json
{
  "sessionId": "required",
  "runId": "optional",
  "eventId": 42,
  "type": "tool_call_finished",
  "payload": {}
}
```

`session_status / worker_exited` 等 Session 生命周期事件没有 `runId`；工具、审批、Run 结果等事件必须同时匹配 `sessionId + runId`。Worker 在整个 Session 内使用单调连续 `sequence`，Web 对外事件使用 `eventId`。服务端先丢弃旧 Session 或旧 Run 的迟到回调，前端再按当前身份过滤一次，形成双层防护。

## 6. 附件与工作区

输入框旁的 `+` 只接收文本、日志和代码附件，最多 5 个，前端把 prompt 与附件总量控制在约 48 KiB；后端还会校验 MIME 类型和 HTTP 上限。附件内容以明确的 `<attachment ...>` 边界加入当前 Run 的 user message，不成为系统指令，也不会改变 Workspace。选择 Workspace 是独立操作，它决定 `read_file / edit_file / run_command` 的真实根目录。

## 7. P1 持久化

默认数据库为 `.hako/web-history.db`，仓库已忽略 `.hako/`。三张表分别保存：

- `sessions`：workspace、Session 状态、Worker 标识、Run 数量和时间；
- `runs`：prompt、附件元数据、状态、Outcome、Verified Finish 摘要；
- `events`：带 sessionId/runId 的完整展示事件，包括 Agent 文本、工具调用、审批与证据。

数据库保存的是可展示事实，不序列化 LLM SDK、Agent Python 对象或 Conversation 内存，因此历史只能查看，不能继续执行。这是有意的 P1/P2 边界。

## 8. 关键调用链

```text
Vue 创建 Session / Run
→ Spring SessionService 校验 workspace、附件和状态
→ JSONL start 或 run 发给同一个 Python Worker
→ Worker 复用同一个 Agent.run(...)
→ Agent 继续使用同一个 Conversation
→ EventBus → Worker JSONL → Spring 状态机/SQLite/SSE
→ Vue 只按结构化事件展示过程和 Verified Finish
```

## 9. 验收结果

- Python：`191 passed, 1 skipped`，覆盖同一 Agent 多 Run 共享历史、附件进入 user context、审批拒绝继续、取消不调用模型和 Windows 命令树快速终止。
- Spring Boot：`15 passed`，真实 fake-worker 子进程覆盖首个 Run、同 Session 第二 Run、Worker 复用、取消后继续 Run、DENY observation、关闭 Session、SQLite 历史和协议故障。
- 前端：`npm run typecheck` 与 `npm run build` 通过，Vite 转换 303 个模块；浏览器走通三 Run 共享时间线、取消后继续、活动 Run 新建会话、CLOSED 历史只读，并在 `1280×720`、`1024×700` 下确认无页面级滚动。输入 `+`、工作区、新会话、停止本轮、只读历史是五个独立语义。

本轮没有 stage、commit 或 push。回退时应按文件逐项审查，不使用 `git reset --hard`，也不要删除用户已有工作区修改。
