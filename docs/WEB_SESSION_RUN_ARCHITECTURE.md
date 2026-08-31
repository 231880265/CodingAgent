# hako Web：Session、Run 与历史架构

> 更新：2026-08-30。本文记录多 Run、挂起 Session 与语义 Conversation 恢复的已落地设计。

## 1. 一句话模型

`Workspace` 决定工具操作哪个代码目录，`Session` 代表一次连续工程对话，`Run` 代表用户在这次对话里发送的一条工程目标，`Attachment` 只是某个 Run 新增的文本上下文。当前实现采用“一 Session 一 Python Worker”：

```text
Workspace D:/project/demo
└─ Session A
   ├─ Worker A
   ├─ Agent A + Conversation A
   ├─ Run 1：定位问题
   ├─ Run 2：直接修复
   └─ Run N：继续补充测试或修正方向

同一个 Workspace 还可以创建 Session B，
但 Worker B、Agent B、Conversation B 都是全新的。
```

Conversation 的语义生命周期跟 Session 绑定，不跟单个 Run 或 Workspace 绑定。同一 OPEN Session 的后续 Run 直接复用内存 Conversation；SUSPENDED Session 则由新 Worker 从 SQLite 重建用户输入与最终回答。新建独立 Session 即使仍选择同一目录，也不会继承旧 Conversation。

## 2. 本轮范围

| 优先级 | 结果 | 当前状态 |
|---|---|---|
| P0 | 一个 Session 支持多个 Run，共享 Worker、Agent、Conversation 和 workspace | 已实现 |
| P1 | 独立“新会话”；Session 上下文隔离；元数据、Run、消息/工具事件、审批与证据写入 SQLite | 已实现 |
| P2 | 重新打开历史 Session，以新 Worker 重建语义 Conversation 后继续对话 | 已实现 |

恢复不复活已经退出的 Python 对象，也不重放旧工具结果。它只恢复成对的 `user prompt + assistant finalText`；仓库内容、命令输出和验证事实由新 Agent 重新读取。这样既保留“上一轮为什么这样做”，又不把旧文件快照伪装成当前事实。

## 3. 两套状态机

```text
SessionStatus
OPENING → OPEN → SUSPENDING → SUSPENDED → OPENING
    │       └────→ CLOSING → CLOSED
    └─────────────────────→ FAILED

RunStatus
PENDING → RUNNING → COMPLETED
             ├────→ FAILED
             ├────→ WAITING_APPROVAL → RUNNING
             └────→ CANCELLING → CANCELLED
```

状态转换只在 Spring Boot 服务端执行。`SUSPENDED` 没有存活 Worker，但可以恢复；`CLOSED / FAILED` 是终态。晚到事件不能把 `CANCELLED` 改回 `COMPLETED`，旧 workerId 的消息也不能进入恢复后的 Session。

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

“新会话”与切换历史会话会挂起当前 Session：若当前 Run 活跃，前端先等待它真正进入 `CANCELLED`，再请求 `Session → SUSPENDING`，等待 Worker 退出和 `SUSPENDED`，最后进入新会话启动页或目标历史时间线。任何一步超时都不会提前切换。取消和挂起都不提供 Undo；已经写入的文件不会自动回滚。

用户点击历史条目时只读取 SQLite 并恢复页面，不立刻占用 Worker。第一次发送后续目标时才调用 `/resume`，使用同一个 sessionId、新 workerId 和新 runId 启动 Worker。显式 `/close` 仍用于永久归档；旧版“新会话”产生的 CLOSED 数据只做一次迁移，转为 SUSPENDED。

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

## 7. 持久化与语义恢复

默认数据库为 `.hako/web-history.db`，仓库已忽略 `.hako/`。三张表分别保存：

- `sessions`：workspace、Session 状态、Worker 标识、Run 数量和时间；
- `runs`：prompt、附件元数据、用于恢复的 user message、状态、Outcome、Verified Finish 摘要；
- `events`：带 sessionId/runId 的完整展示事件，包括 Agent 文本、工具调用、审批与证据。

数据库仍不序列化 LLM SDK、Python 对象或工具历史。恢复快照按 Run 提取完整 user message（prompt 与附件文本）和最终 assistant answer，严格校验为成对交替消息后交给新 Worker。工具调用、文件读取、stdout/stderr 和 token 计数只留在审计时间线，不进入新 Conversation。数据库因而包含本地敏感内容，必须保持在被 Git 忽略的 `.hako/` 中。

## 8. 关键调用链

```text
Vue 创建 Session / Run，或选择历史后发送 follow-up
→ Spring SessionService 校验 workspace、附件和状态
→ OPEN：JSONL run 发给同一 Worker
→ SUSPENDED：SQLite 提取语义对，新 Worker 收到 start + conversation
→ Worker 复用或重建 Agent Conversation，再执行 Agent.run(...)
→ EventBus → Worker JSONL → Spring 状态机/SQLite/SSE
→ Vue 只按结构化事件展示过程和 Verified Finish
```

## 9. 验收结果

- Python 覆盖同一 Agent 多 Run、语义 Conversation 恢复校验、附件、审批拒绝、取消和 Windows 命令树终止。
- Spring Boot 真实 fake-worker 子进程覆盖 OPEN 内复用 workerId，以及 SUSPENDED 恢复时 sessionId 不变、workerId 更新、上一轮语义仍可用。
- 前端类型检查、组件测试与生产构建覆盖常驻左侧会话列表、无内部状态标签、历史时间线进入和继续输入。

本轮没有 stage、commit 或 push。回退时应按文件逐项审查，不使用 `git reset --hard`，也不要删除用户已有工作区修改。
