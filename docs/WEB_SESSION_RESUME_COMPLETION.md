# Web 历史会话恢复完成记录

> 日期：2026-08-30。实现范围是单用户、本地少量会话；没有引入多 Session 并发。

## 用户看到的变化

- 历史会话固定显示在 Web 左侧，不再使用遮挡页面的抽屉，也不显示 `OPEN / CLOSED / SUSPENDED` 等内部枚举。
- 点击一条历史会话会恢复原时间线，底部可以直接继续输入。此时不会立刻启动 Python 进程；只有用户真正发送后续目标时才恢复 Worker。
- 点击“新会话”会先取消仍在执行的 Run，再把当前 Session 挂起。已写入 Workspace 的文件不会回滚，旧会话以后仍可继续。
- 旧版由“新会话”产生的 `CLOSED` 本地记录会做一次性迁移，转为可恢复的 `SUSPENDED`；以后显式调用 close API 产生的 `CLOSED` 仍是终态。

## 实现原理

```text
Session A / Run 1 完成
        ↓ 点击新会话或切换历史
Run（若活跃）→ CANCELLING → CANCELLED
Session A → SUSPENDING → SUSPENDED
旧 Worker 退出；SQLite 保留 Session、Run、事件和语义对话
        ↓ 用户在 Session A 再次发送
POST /sessions/A/resume
        ↓
同一个 sessionId + 新 workerId + 新 runId
        ↓
新 Worker 重建 system prompt
        ↓
恢复 [user 输入, assistant 最终回答] 成对消息
        ↓
追加本次 user 目标并继续 Agent.run()
```

恢复的是“语义 Conversation”，不是 Python 进程快照。每个已完成 Run 持久化原始 prompt、附件文本和最终回答；恢复时只接受完整的 `user → assistant` 交替消息。旧 `tool_calls`、`read_file` 内容、stdout/stderr、审批和 token 状态不进入新模型上下文，因为这些是过去时刻的仓库观察，恢复后继续使用可能造成陈旧读取。工具事件仍保留在 Web 时间线供审计，新 Agent 必须重新读取当前 Workspace 后再修改。

## 状态与边界

```text
Session: OPENING → OPEN → SUSPENDING → SUSPENDED → OPENING
                         └────────────→ CLOSING → CLOSED

Run:     PENDING → RUNNING → COMPLETED / FAILED
                     └────→ CANCELLING → CANCELLED
```

`SUSPENDED` 表示 Conversation 可从 SQLite 语义重建、但没有存活 Worker；`CLOSED` 表示显式归档终态。当前服务同一时间只保留一个实时 Worker，历史会话数量可以更多。语义恢复上限为 200 条消息，适合本地少量演示会话；没有实现无限历史压缩、模型隐藏状态恢复或多 Worker 并发。

## 代码落点

- `SessionStatus / SessionState / SessionService`：挂起、恢复、新 Worker 和事件游标。
- `SessionHistoryRepository / RunState`：SQLite schema 迁移、user/assistant 语义对保存与恢复快照。
- `hako/history.py`：严格重建成对语义消息并排除工具观察。
- `web/worker/main.py`：在首个新 Run 前把历史注入新 Agent Conversation。
- `useSessionController.ts / SessionSidebar.vue`：懒恢复、左侧会话切换和继续输入。
- `fake_worker.py`：确定性证明新 Worker 收到了几轮历史，而不是只恢复了 UI。

## 验证

确定性集成测试断言：挂起后旧 Worker 已退出；恢复时 sessionId 不变、workerId 改变、runCount 递增；新 Run 的结果能引用上一轮上下文。当前统一回归口径为 Python `203 passed, 1 skipped`、Spring Boot `18 passed`、前端 `34 passed`，生产构建转换 324 个模块。浏览器使用真实 REST/SSE 与确定性 Worker，从左侧打开历史会话并完成两次审批后，该会话由 1 轮增至 2 轮，旧时间线仍在、控制台无错误。Python 还断言乱序、空内容、悬空 user 消息都会拒绝，且恢复结果没有 tool 消息；前端断言侧栏不显示生命周期枚举或旧版灰色解释文案。

本轮未执行 Git stage、commit 或 push。
