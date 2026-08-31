# hako Web API v1

> 更新：2026-08-30。权威实现为 `SessionController`、`ApiModels`、`SessionService` 与 `web/worker/protocol.py`。

## 1. 公共约定

- 前缀：`/api/v1`；JSON 与事件 `schemaVersion` 均为 `1.0`。
- `sessionId / runId / approvalId` 均由服务端生成 UUID。
- Workspace 必须是后端允许根目录内已经存在的绝对目录。
- HTTP 请求体上限 64 KiB；prompt 最长 20,000 字符；每个 Run 最多 5 个文本附件，单附件内容最长 40,000 字符。
- 错误统一返回 `{"schemaVersion":"1.0","error":{"code":"...","message":"...","requestId":"..."}}`。

## 2. REST 接口

| 方法 | 路径 | 含义 |
|---|---|---|
| GET | `/health` | 后端和 Worker 入口健康检查 |
| POST | `/sessions` | 创建 Session，并创建它的首个 Run |
| GET | `/sessions` | 列出 SQLite 中的 Session 历史摘要 |
| GET | `/sessions/{sessionId}` | 查询当前内存 Session 资源 |
| GET | `/sessions/{sessionId}/history` | 查询持久化 Run、事件和展示历史 |
| POST | `/sessions/{sessionId}/runs` | 在 OPEN Session 中创建后续 Run |
| POST | `/sessions/{sessionId}/resume` | 为 SUSPENDED Session 启动新 Worker、恢复语义 Conversation 并创建 Run |
| GET | `/sessions/{sessionId}/events` | 订阅 Session SSE；支持 `Last-Event-ID` |
| POST | `/sessions/{sessionId}/runs/{runId}/approvals/{approvalId}` | 处理当前审批 |
| POST | `/sessions/{sessionId}/runs/{runId}/cancel` | 协作式取消当前 Run，Session 保活 |
| GET | `/sessions/{sessionId}/runs/{runId}/summary` | 获取终态 Run 与 Verified Finish 摘要 |
| POST | `/sessions/{sessionId}/close` | 当前无活跃 Run 时关闭 Worker 与 Session |
| POST | `/sessions/{sessionId}/suspend` | 当前无活跃 Run 时回收 Worker，但保留可恢复 Session |

### 创建 Session

```json
POST /api/v1/sessions
{
  "workspace": "D:\\project\\demo",
  "prompt": "结合 error.log 定位失败并修复",
  "attachments": [
    {"name":"error.log","mediaType":"text/plain","content":"..."}
  ],
  "options": {"maxSteps": 40}
}
```

响应的核心结构：

```json
{
  "schemaVersion": "1.0",
  "sessionId": "...",
  "status": "OPENING",
  "workspace": "D:\\project\\demo",
  "runCount": 1,
  "canContinue": false,
  "worker": {"workerId":"...","pid":null,"alive":false,"status":"NOT_STARTED"},
  "currentRun": {
    "runId": "...",
    "status": "PENDING",
    "prompt": "结合 error.log 定位失败并修复",
    "attachments": [{"name":"error.log","mediaType":"text/plain","bytes":3}]
  }
}
```

### 创建后续 Run

```json
POST /api/v1/sessions/{sessionId}/runs
{
  "prompt": "沿用刚才的结论，再补一个回归测试",
  "attachments": [],
  "options": {"maxSteps": 30}
}
```

只有 Session 为 `OPEN`、前一 Run 已到终态且 Worker 仍存活时才接受。响应仍为 SessionResource；`sessionId / workerId` 不变，`runId / runCount` 更新。

### 审批

```json
POST /api/v1/sessions/{sessionId}/runs/{runId}/approvals/{approvalId}
{"decision":"ALLOW_ONCE"}
```

decision 为 `ALLOW_ONCE / ALLOW_SESSION / DENY`。高风险操作不允许 `ALLOW_SESSION`。HTTP `202 ACCEPTED` 仅表示决定已经发送；`approval_resolved` 才表示 Worker 已接收。`DENY` 是 Agent observation，不等于取消 Run。

### 挂起与恢复 Session

```json
POST /api/v1/sessions/{sessionId}/suspend
{"status":"SUSPENDING"}
```

终态以 `session_status=SUSPENDED` 为准。此时 Worker 已退出、文件不回滚、历史仍在 SQLite。恢复不会立刻发生在“查看历史”动作上；用户真正发送后续目标时调用：

```json
POST /api/v1/sessions/{sessionId}/resume
{
  "prompt": "沿用刚才的根因，再补边界测试",
  "attachments": [],
  "options": {"maxSteps": 30}
}
```

响应保留 sessionId，但生成新 workerId 与 runId。新 Worker 收到过去已完成 Run 的 user/assistant 语义对；旧工具观察不会恢复。只有 SUSPENDED Session 接受该接口。

### 取消 Run 与关闭 Session

```json
POST /api/v1/sessions/{sessionId}/runs/{runId}/cancel
{"status":"CANCELLING","message":"..."}
```

最终以 `run_status=CANCELLED` 为准。文件副作用不回滚，Worker 和 Conversation 保留。关闭 Session 前若仍有活动 Run，接口返回冲突；永久归档顺序是 cancel → 等待 CANCELLED → close → 等待 CLOSED。普通 UI 切换使用 suspend，不使用 close。

### 历史

`GET /sessions` 返回摘要数组；`GET /sessions/{id}/history` 返回 `runs + events`。页面读取历史不会启动 Worker；`POST /resume` 才从持久化语义消息建立新 Worker。

```text
DELETE /api/v1/sessions/{sessionId}
```

删除成功返回 `204 No Content`，会永久删除该 Session 的持久化 Run 与事件记录，但不会删除工作区文件，也不会回滚已经落盘的修改。存在活动 Run 时返回 `409 RUN_CONFLICT`；删除当前空闲 Session 时会先关闭其 Worker，再清理记录。

## 3. SSE 信封

```json
{
  "schemaVersion": "1.0",
  "eventId": 12,
  "sessionId": "...",
  "runId": "...",
  "type": "tool_call_finished",
  "source": "HAKO",
  "occurredAt": "2026-08-29T06:00:00Z",
  "payload": {}
}
```

Session 事件（如 `session_status / worker_exited`）省略 `runId`；Run 事件必须带 `sessionId + runId`。主要类型包括 `run_status`、`assistant_text`、`tool_call_started/finished`、`approval_required/resolved`、`verification_required`、`run_result`、`run_cancelled` 和 `worker_error`。事件按 Session 的 `eventId` 单调排列；缓存缺口使用 `stream_gap` 提示。

## 4. Worker JSONL v1

Spring → Worker：首条为 `start(sessionId, runId, workspace, prompt, attachments, maxSteps, conversation)`；后续为 `run(...)`、`approval_response(...)` 或 `cancel_run(...)`。`conversation` 为空或由完整的 user/assistant 交替消息组成。Worker → Spring：先发 Session 级 `ready`，之后所有 Run 消息携带 `sessionId + runId + sequence`。

同一 Worker 内只构造一次 Agent。每次 `run` 调用同一个 `Agent.run()`；恢复后的首个 `start` 则先把语义历史注入空 Conversation，再执行本次目标。Web `eventId` 跨 Worker 延续，Worker 自身 `sequence` 从新进程重新计数，两者不能混用。
