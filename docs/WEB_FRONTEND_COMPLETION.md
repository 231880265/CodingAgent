# hako Web 前端完成记录

> 更新：2026-08-30。当前界面是带常驻会话列表的 Session/Run 对话工作台。

## 本轮完成

- 用 `useSessionController` 替代单任务控制器：一个 Session 内可连续创建多个 Run，SSE 在 Run 结束后继续保持，只有 Session 终态才关闭。
- “停止本轮”只请求 Run 取消；“新会话”严格执行 cancel → 等待 CANCELLED → suspend → 等待 SUSPENDED → 进入空启动页。
- 输入框旁 `+` 只上传文本、日志和代码附件；Workspace 选择是独立入口。附件最多 5 个，并在发送前做类型、空内容和约 48 KiB 总量检查。
- 历史会话固定在桌面端左侧；点击后恢复原时间线，第一次发送 follow-up 时再懒启动 Worker 并重建语义 Conversation。
- 前端按 `sessionId` 过滤全部事件，Run 级状态再校验 `runId`；终态后到达的旧事件不会覆盖当前状态。
- 保留结构化工程时间线：黑色主信息来自工具结果与后端状态，模型文本、参数、原始输出、审批和 token 放在灰色可展开区域。
- 页面继续保持视口自适应与内部滚动；左侧只承担会话导航，不恢复旧版任务表单卡。

## 目录职责

```text
src/App.vue                              页面编排、取消/新会话确认
src/composables/useSessionController.ts  Session/Run/SSE/历史协调
src/components/AppHeader.vue             Workspace、连接与运行状态、移动端会话入口
src/components/TaskComposer.vue          prompt、附件、Workspace、发送/停止 Run
src/components/SessionSidebar.vue        常驻会话导航、新会话与历史切换
src/components/RunTimeline.vue           事件配对、模型说明归并、当前或历史工程日志
src/components/EventItem.vue             用户目标与内核关键转折
src/components/ToolActivity.vue          调查、修改、命令与按需工具证据
src/components/GoalResult.vue            Run 最终交付证据
src/components/ApprovalPanel.vue         当前 Run 审批
src/services/apiGateway.ts               正式 REST/SSE
src/services/mockGateway.ts              同契约确定性演示
```

## 已验证

前端测试 `34 passed`，`npm run typecheck` 与 `npm run build` 均通过，生产构建转换 324 个模块。浏览器已经覆盖：左侧历史列表不显示 OPEN/CLOSED/SUSPENDED 或旧版灰色解释；点击历史恢复完整时间线；首次 follow-up 懒启动新 Worker；两次审批后同一会话从 1 个 Run 增至 2 个 Run；恢复结果能引用上一轮上下文，控制台无错误。此前同 Session 多 Run、取消后继续、附件/Workspace 分离和视口自适应场景继续由回归测试覆盖。

## 明确不做

不上传目录作为附件；不在浏览器保存 API Key；不实现多 Session 并发、远程 Worker、无限历史压缩或 Python 进程快照恢复。本轮没有 stage、commit 或 push。

## UI 信息架构收束（2026-08-29）

本轮只修改前端展示，没有改 Agent 内核、状态机、ToolResult、Verified Finish 或 Session/Run 契约，也没有新增后端 DTO。`assistant_text` 不再作为重复的主线步骤，而是并入随后工具调用的折叠说明；主线只保留用户目标、调查、修改、关键失败、可执行验证和权威 RunResult。工具参数、原始输出、模型说明、耗时与 token 默认折叠，失败详情不再自动展开。最终结果改成“修改 / 验证 / 结果”三项可读摘要，运行统计降到完整证据内部。

生产构建、类型检查与 Worker 专项测试通过。在确定性演示事件上完成 `read → edit → test → done_verified` 浏览器闭环；`1920×1080` 下页面外层高度严格等于视口、时间线无需滚动、所有详情默认收起，`1366×768` 下同样没有页面级横向或纵向滚动，仅时间线按需内部滚动。本轮未修改、暂存或提交 Git 历史。当前统一测试数字见 `PROJECT_STATUS.md`。

### 空状态二次精简

根据 2026-08-29 实际页面反馈，删除空状态 Logo、产品说明和四步流程，只保留“选择工作区，描述任务”。顶部在没有 Session 时隐藏工作区入口、未开始状态和新会话按钮；工作区绝对路径合并进唯一的输入框，不再额外显示工作区卡片或工作区按钮。执行日志的普通成功说明进一步压缩为短句，详细行号、参数与原始结果仍保留在折叠证据中。改动仍然只发生在前端展示层，生产构建通过。

随后进一步把首页与运行页拆成两种页面结构：没有 Session 时显示独立 Launcher，只包含“开始一个 Coding Task”、一句操作提示和紧随其后的工作区/任务输入框；Session 创建后才挂载工程时间线、审批区和底部继续输入 Dock。首页因此不再是运行页的空壳，页面切换仍复用原有 Session 创建逻辑，没有引入新的状态或接口。

### 运行中取消入口修复

发送按钮现在与 Codex 一样按 Run 状态原位切换：空闲时显示发送箭头，`PENDING / RUNNING / WAITING_APPROVAL` 时显示停止方块，`CANCELLING` 时显示不可重复点击的加载态，终态后恢复发送。停止按钮复用既有确认弹窗与 `cancelRun()`，页头不再重复放置取消入口。生产构建通过；确定性浏览器流程已验证“发送 → 停止按钮出现 → 确认 → CANCELLED → 输入框恢复并可继续 Run”。本次未修改后端、Agent 内核或取消语义。

### 历史会话恢复与左侧导航（2026-08-30）

历史抽屉已替换为桌面端常驻左侧导航；条目只显示任务标题，不展示工作区、轮数、时间、`OPEN / CLOSED / SUSPENDED` 或解释文案。选中项使用中性深灰底色，不再使用蓝色描边。移动端仍通过页头“会话”按钮打开同一组件。选择历史会话只加载 SQLite 时间线，不立即启动 Worker；底部继续输入会调用 resume API，同一个 sessionId 建立新 Worker 后继续。前端会把已持久化事件先放入去重集合，再接入新 SSE，避免恢复后旧事件重复或新旧 Worker 事件混排。

### 调查轨迹归组与会话删除（2026-08-30）

连续 `list_dir` 调用现在折叠为“已查看 N 个位置”，与连续 `read_file` 的“已读取 N 个文件”使用同一套摘要优先结构；路径、参数与原始结果仍可逐项展开。历史条目悬停后提供删除入口：删除会清理 SQLite 中对应 Session、Run 与事件，但不会删除工作区文件或回滚修改；活动 Run 明确拒绝删除。前端 Vitest 共 `34 passed`，类型检查、生产构建与 Spring Boot 完整测试均通过；浏览器复核确认侧栏无横向溢出、选中态为中性深灰、连续工作区查看已正确归组。本轮没有修改 Agent loop、ToolResult 或 Verified Finish。

### 修改轨迹归组与事实型实时进度（2026-08-30）

连续 `edit_file` / `write_file` 现在折叠为“已修改 N 个文件”，创建、修改、失败状态及 Diff 仍可逐项展开；单次修改继续使用原有工具行，避免为一个动作制造额外容器。运行中底部状态不再固定显示“正在分析代码与现有证据”，而是回看已有事件，按真实路径、已修改文件数、测试/构建结果或模型刚刚公开的说明生成短句。当前端没有足够证据时只显示“正在理解任务并选择首批相关文件”，不会猜测根因。没有修改 Agent loop、Web 后端 DTO、ToolResult 或 Verified Finish。前端全量 `34 passed`，类型检查与生产构建通过。
