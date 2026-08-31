# hako Web 后端完成记录

> 更新：2026-08-30。Spring Boot 控制面支持 Session 内多 Run，以及 SUSPENDED Session 的新 Worker 语义恢复。

## 本轮完成

- 新增独立 `SessionStatus` 与 `RunStatus`，所有转换显式校验，终态不可逆。
- `SessionService` 负责创建/查询 Session、创建后续 Run、审批、取消 Run、关闭 Session、SSE 和历史；旧 `TaskService/TaskState/TaskStatus` 已移除。
- Python Worker 在整个 Session 内只构造一次 Agent，后续 `run` 消息再次调用同一个 `Agent.run()`，Conversation 因此连续。
- `DENY` 改为工具 observation；Agent 可以继续调查。普通 cancel 只触发 `cancel_run`，Worker 在返回 CANCELLED 后继续等待下一 Run。
- `run_command` 支持协作式取消；Windows 独立进程组使用 `CTRL_BREAK` 与 `taskkill /T /F` 兜底，避免 pytest/java/npm 子进程遗留。
- Session/Run 事件身份拆分：sessionId 必填、runId 按事件层级可选；旧 Session 与旧 Run 的迟到消息在状态服务入口丢弃。
- 新增 SQLite `sessions/runs/events` 三表，保存 Session 元数据、每个 Run 的 prompt/附件元数据/Outcome/Summary，以及 Agent 文本、工具、审批和证据事件。
- SQLite 额外保存每个完成 Run 的完整 user message；恢复时只重建 user/assistant 语义对，不反序列化 Python 对象或旧工具观察。
- 正常关闭 Worker 只记录一次 `worker_exited`，避免进程回调与 terminate 回调制造重复证据。

## 验证覆盖

Spring Boot 共 `18 passed`。`SessionServiceProcessIntegrationTest` 使用真实 Python fake-worker 子进程验证：首个 Run 两次审批与 Verified Finish；第二 Run 复用 workerId 且取得新 runId；取消后 Session 仍 OPEN 并可继续；挂起后旧 Worker 退出，恢复时 sessionId 不变、workerId 更新、runCount 递增且结果引用上一轮上下文；DENY 不关闭 Session；高风险决策限制；非法 JSON 使 Run/Session 失败。

`ApiContractTest` 验证 `/api/v1/sessions` 契约、严格 JSON、请求体上限与统一错误体。Python 测试另外覆盖同 Agent 多 Run、附件进入 user context、拒绝 observation、取消前置检查与命令树终止。

## 关键边界

- Cancel 停止后续行为但不回滚文件；Session close 也不还原 Workspace。
- 取消超时不会假装 Worker 可继续：后端把 Run 固化为 CANCELLED，同时使 Session FAILED 并回收 Worker。
- 当前服务只维护一个实时 Worker；历史 Session 可以很多。切换前先挂起当前 Session，恢复时仍只启动一个 Worker。
- `.hako/web-history.db` 会保存 prompt、附件文本、最终回答和展示事件，属于本地敏感数据；`.hako/` 已忽略。
- Fake Worker 证明协议和状态机，不证明真实 DeepSeek 成功率。

接口见 [WEB_CONSOLE_API.md](./WEB_CONSOLE_API.md)，完整决策见 [WEB_SESSION_RUN_ARCHITECTURE.md](./WEB_SESSION_RUN_ARCHITECTURE.md)。本轮没有 stage、commit 或 push。

## SUSPENDED 与 Conversation 重建（2026-08-30）

新增 `OPEN → SUSPENDING → SUSPENDED → OPENING` 路径。挂起先确认没有活跃 Run，再终止 Worker 并固化状态；恢复保留 sessionId 和事件游标，生成新 workerId、新 runId，并把 SQLite 中每个已完成 Run 的 `conversation_user + finalText` 放入首条 Worker `start`。Python `Conversation.restore_semantic()` 只接受不超过 200 条、严格成对的 user/assistant 消息；任何 tool call、文件快照或 stdout/stderr 都不会恢复。服务重启时数据库中失去 Worker 的瞬时 OPEN 状态会自动降为 SUSPENDED；旧版由“新会话”生成的 CLOSED 记录做一次性迁移，之后显式 close 仍保持不可恢复终态。详细记录见 [WEB_SESSION_RESUME_COMPLETION.md](./WEB_SESSION_RESUME_COMPLETION.md)。

## 启动就绪门禁补充（2026-08-29）

`start-web.ps1` 不再同时放出两个服务地址：脚本先轮询后端 `/api/v1/health` 并确认状态为 `UP`，之后才启动前端并等待首页返回成功。新增 8080/5173 端口占用预检、服务提前退出检测以及后端 180 秒/前端 30 秒就绪超时；失败仍通过原有 `finally` 回收本次启动的进程树。PowerShell 5.1 语法、`-CheckOnly` 预检和 Fake 模式完整冒烟均通过；实测约 29 秒后后端与前端依次 ready，两个端点均返回 HTTP 200，测试进程已清理。
