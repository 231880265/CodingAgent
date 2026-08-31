# hako Web 控制台需求

> 版本：2026-08-30。产品定位：面向本地已有代码仓库的可验证工程任务控制台。

## 1. 用户目标

用户选择一个本地 Workspace，在连续会话里依次提出“定位问题 → 修复 → 补测试”等 Run；hako 复用或重建同一 Session 的语义 Conversation，展示调查、受控修改、审批、执行验证和 Verified Finish。用户可以停止某个 Run 后继续追问，也可以切换到上下文隔离的新 Session，并从左侧恢复历史会话继续。

## 2. 概念与范围

| 概念 | 含义 |
|---|---|
| Workspace | 工具实际读写和执行命令的本地目录 |
| Session | 一次连续工程对话；OPEN 时拥有 Worker，SUSPENDED 时保留可重建的语义 Conversation |
| Run | 用户在 Session 中发送的一条工程目标 |
| Attachment | 某个 Run 增加的文本上下文，不改变 Workspace |

P0 支持同 Session 多 Run。P1 支持独立 Session、上下文隔离和 SQLite 历史。P2 支持从 SUSPENDED 历史建立新 Worker，恢复 user/assistant 语义 Conversation 后继续；不恢复旧进程、工具观察或精确 token 状态。

## 3. 功能需求

| ID | 需求 | 验收 |
|---|---|---|
| FR-001 | 创建 Session 时选择允许根目录内的绝对 Workspace | 非法或越界路径由后端拒绝 |
| FR-002 | 同一 Session 连续发送多个 Run | OPEN 时 workerId 不变；恢复时 sessionId 不变、workerId/runId 变化，后续 Agent 看得到前文 |
| FR-003 | 输入框 `+` 添加文本/日志/代码附件 | 附件进入当前 user context，不改变 Workspace |
| FR-004 | 工具副作用逐次审批 | DENY 作为 observation；高风险不可整 Session 放行 |
| FR-005 | “停止本轮”仅取消活动 Run | Run 到 CANCELLED；Session 仍 OPEN；Worker/Conversation 保活 |
| FR-006 | “新会话”独立于附件按钮 | 活动 Run 先取消，旧 Session 到 SUSPENDED、Worker 退出后才清 UI |
| FR-007 | 结构化展示工程过程 | 主文字来自工具结果/内核状态；模型自述和原始输出可展开 |
| FR-008 | 展示 Verified Finish | 区分只读完成、验证完成、修改后未验证、取消和故障 |
| FR-009 | 保存 Session 历史 | Session、Run、事件、审批、Agent 文本、工具和证据写入 SQLite |
| FR-010 | 恢复历史会话 | 查看不启动 Worker；第一次继续输入时懒恢复语义 Conversation |

## 4. 状态与副作用要求

Session 与 Run 使用独立状态机；SUSPENDED 可恢复，CLOSED/FAILED 与 Run 终态不可逆。取消不是回滚：已写入文件继续存在；如果 `run_command` 正在执行，必须终止它的进程树，不能误杀仍需保活的 Worker。新 Session 可以继续使用同一 Workspace 当前磁盘状态，但 Conversation 必须为空。

所有事件至少带 sessionId；Run 级事件还必须带 runId。服务端和前端都要丢弃旧 Session/Run 的迟到事件，防止旧 pytest 日志污染新会话。

## 5. UI 要求

- 单列 Codex 风格工作台；页面本身不滚动，时间线在内部滚动。
- 桌面端左侧常驻会话导航，移动端由顶栏打开；停止本轮位于输入框，新会话位于侧栏，输入框 `+` 只表示附件。
- 同一 Session 的多 Run 连续出现在一条工程时间线上；“Run”文案解释为一条用户目标，不再使用含糊的“回合”。
- 审批界面使用克制的中性色；失败、等待、成功只用状态色传达语义。
- 左侧会话条目展示 workspace、最后目标、Run 数和时间，不暴露内部状态枚举；选中后恢复时间线并允许继续输入。

## 6. 非功能与安全边界

- 浏览器不接收或保存 API Key；模型密钥只由 Python 环境读取。
- HTTP 正文、附件数量/类型/长度、Workspace 根目录和 Worker JSONL 单行长度均受限。
- 当前是 localhost 单用户产品，不提供登录、TLS、远程执行或系统级沙箱，不得直接暴露公网。
- Fake Worker 只用于协议与 UI 测试，不能作为真实模型能力证据。

详细状态、取消和持久化设计见 [WEB_SESSION_RUN_ARCHITECTURE.md](./WEB_SESSION_RUN_ARCHITECTURE.md)，接口字段见 [WEB_CONSOLE_API.md](./WEB_CONSOLE_API.md)。
