# hako Web 后端完成记录

> 更新：2026-08-29。Spring Boot 控制面已从“一个 task 等于一个 Worker”改为“一 Session 一 Worker，Session 内多个 Run”。

## 本轮完成

- 新增独立 `SessionStatus` 与 `RunStatus`，所有转换显式校验，终态不可逆。
- `SessionService` 负责创建/查询 Session、创建后续 Run、审批、取消 Run、关闭 Session、SSE 和历史；旧 `TaskService/TaskState/TaskStatus` 已移除。
- Python Worker 在整个 Session 内只构造一次 Agent，后续 `run` 消息再次调用同一个 `Agent.run()`，Conversation 因此连续。
- `DENY` 改为工具 observation；Agent 可以继续调查。普通 cancel 只触发 `cancel_run`，Worker 在返回 CANCELLED 后继续等待下一 Run。
- `run_command` 支持协作式取消；Windows 独立进程组使用 `CTRL_BREAK` 与 `taskkill /T /F` 兜底，避免 pytest/java/npm 子进程遗留。
- Session/Run 事件身份拆分：sessionId 必填、runId 按事件层级可选；旧 Session 与旧 Run 的迟到消息在状态服务入口丢弃。
- 新增 SQLite `sessions/runs/events` 三表，保存 Session 元数据、每个 Run 的 prompt/附件元数据/Outcome/Summary，以及 Agent 文本、工具、审批和证据事件。
- 历史 API 只读，不反序列化 Agent 或 Conversation；P2 明确不实现。
- 正常关闭 Worker 只记录一次 `worker_exited`，避免进程回调与 terminate 回调制造重复证据。

## 验证覆盖

Spring Boot 共 `15 passed`。`SessionServiceProcessIntegrationTest` 使用真实 Python fake-worker 子进程验证：首个 Run 两次审批与 Verified Finish；第二 Run 复用 workerId 且取得新 runId；取消后 Session 仍 OPEN 并可继续；DENY 不关闭 Session；高风险决策限制；关闭后 SQLite 历史可查；非法 JSON 使 Run/Session 失败。

`ApiContractTest` 验证 `/api/v1/sessions` 契约、严格 JSON、请求体上限与统一错误体。Python 测试另外覆盖同 Agent 多 Run、附件进入 user context、拒绝 observation、取消前置检查与命令树终止。

## 关键边界

- Cancel 停止后续行为但不回滚文件；Session close 也不还原 Workspace。
- 取消超时不会假装 Worker 可继续：后端把 Run 固化为 CANCELLED，同时使 Session FAILED 并回收 Worker。
- 当前服务只维护一个实时 Session；历史 Session 可以很多。关闭旧 Session 后才能创建新实时 Session。
- `.hako/web-history.db` 为本地展示数据，`.hako/` 已忽略；不得把它当作模型 Conversation 快照。
- Fake Worker 证明协议和状态机，不证明真实 DeepSeek 成功率。

接口见 [WEB_CONSOLE_API.md](./WEB_CONSOLE_API.md)，完整决策见 [WEB_SESSION_RUN_ARCHITECTURE.md](./WEB_SESSION_RUN_ARCHITECTURE.md)。本轮没有 stage、commit 或 push。

## 启动就绪门禁补充（2026-08-29）

`start-web.ps1` 不再同时放出两个服务地址：脚本先轮询后端 `/api/v1/health` 并确认状态为 `UP`，之后才启动前端并等待首页返回成功。新增 8080/5173 端口占用预检、服务提前退出检测以及后端 180 秒/前端 30 秒就绪超时；失败仍通过原有 `finally` 回收本次启动的进程树。PowerShell 5.1 语法、`-CheckOnly` 预检和 Fake 模式完整冒烟均通过；实测约 29 秒后后端与前端依次 ready，两个端点均返回 HTTP 200，测试进程已清理。
