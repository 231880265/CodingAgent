# hako Web 前端完成记录

> 更新：2026-08-29。当前界面已从“单 Goal 三栏表单”收束为 Session/Run 对话工作台。

## 本轮完成

- 用 `useSessionController` 替代单任务控制器：一个 Session 内可连续创建多个 Run，SSE 在 Run 结束后继续保持，只有 Session 终态才关闭。
- 顶栏拆分“停止本轮”和“新会话”。停止只请求 Run 取消；新会话严格执行 cancel → 等待 CANCELLED → close → 等待 CLOSED → 清理旧时间线。
- 输入框旁 `+` 只上传文本、日志和代码附件；Workspace 选择是独立入口。附件最多 5 个，并在发送前做类型、空内容和约 48 KiB 总量检查。
- 新增历史抽屉和只读历史时间线；页面明确提示 P1 只恢复展示事实，不恢复 Agent Conversation。
- 前端按 `sessionId` 过滤全部事件，Run 级状态再校验 `runId`；终态后到达的旧事件不会覆盖当前状态。
- 保留结构化工程时间线：黑色主信息来自工具结果与后端状态，模型文本、参数、原始输出、审批和 token 放在灰色可展开区域。
- 页面继续保持视口自适应与内部滚动，不恢复左侧固定任务卡。

## 目录职责

```text
src/App.vue                              页面编排、取消/新会话确认
src/composables/useSessionController.ts  Session/Run/SSE/历史协调
src/components/AppHeader.vue             Workspace、历史、运行状态、新会话
src/components/TaskComposer.vue          prompt、附件、Workspace、发送/停止 Run
src/components/SessionHistoryDrawer.vue  P1 只读历史入口
src/components/RunTimeline.vue           事件配对、模型说明归并、当前或历史工程日志
src/components/EventItem.vue             用户目标与内核关键转折
src/components/ToolActivity.vue          调查、修改、命令与按需工具证据
src/components/GoalResult.vue            Run 最终交付证据
src/components/ApprovalPanel.vue         当前 Run 审批
src/services/apiGateway.ts               正式 REST/SSE
src/services/mockGateway.ts              同契约确定性演示
```

## 已验证

`npm run typecheck` 与 `npm run build` 均通过，生产构建转换 303 个模块。浏览器已经覆盖：同 Session 三个 Run、等待审批时停止本轮、取消后继续 Run、活动 Run 点击新会话、附件与 Workspace 入口分离、CLOSED 历史只读视图，以及 `1280×720`、`1024×700` 页面无外层滚动。验收中还修复了一个终态竞态：前端先刷新 Session 以恢复输入，再容忍摘要 GET 极短暂地慢于 SSE 完成事件；演示网关也改为先固化摘要、后发布 COMPLETED。

## 明确不做

历史 Session 没有“继续”按钮；不上传目录作为附件；不在浏览器保存 API Key；不实现多 Session 并发、远程 Worker 或 P2 Conversation 恢复。本轮没有 stage、commit 或 push。

## UI 信息架构收束（2026-08-29）

本轮只修改前端展示，没有改 Agent 内核、状态机、ToolResult、Verified Finish 或 Session/Run 契约，也没有新增后端 DTO。`assistant_text` 不再作为重复的主线步骤，而是并入随后工具调用的折叠说明；主线只保留用户目标、调查、修改、关键失败、可执行验证和权威 RunResult。工具参数、原始输出、模型说明、耗时与 token 默认折叠，失败详情不再自动展开。最终结果改成“修改 / 验证 / 结果”三项可读摘要，运行统计降到完整证据内部。

生产构建与类型检查通过，`tests/test_web_worker.py` 为 `9 passed`。在确定性演示事件上完成 `read → edit → test → done_verified` 浏览器闭环；`1920×1080` 下页面外层高度严格等于视口、时间线无需滚动、所有详情默认收起，`1366×768` 下同样没有页面级横向或纵向滚动，仅时间线按需内部滚动。本轮未修改、暂存或提交 Git 历史。

### 空状态二次精简

根据 2026-08-29 实际页面反馈，删除空状态 Logo、产品说明和四步流程，只保留“选择工作区，描述任务”。顶部在没有 Session 时隐藏工作区入口、未开始状态和新会话按钮；工作区绝对路径合并进唯一的输入框，不再额外显示工作区卡片或工作区按钮。执行日志的普通成功说明进一步压缩为短句，详细行号、参数与原始结果仍保留在折叠证据中。改动仍然只发生在前端展示层，生产构建通过。

随后进一步把首页与运行页拆成两种页面结构：没有 Session 时显示独立 Launcher，只包含“开始一个 Coding Task”、一句操作提示和紧随其后的工作区/任务输入框；Session 创建后才挂载工程时间线、审批区和底部继续输入 Dock。首页因此不再是运行页的空壳，页面切换仍复用原有 Session 创建逻辑，没有引入新的状态或接口。

### 运行中取消入口修复

发送按钮现在与 Codex 一样按 Run 状态原位切换：空闲时显示发送箭头，`PENDING / RUNNING / WAITING_APPROVAL` 时显示停止方块，`CANCELLING` 时显示不可重复点击的加载态，终态后恢复发送。停止按钮复用既有确认弹窗与 `cancelRun()`，页头不再重复放置取消入口。生产构建通过；确定性浏览器流程已验证“发送 → 停止按钮出现 → 确认 → CANCELLED → 输入框恢复并可继续 Run”。本次未修改后端、Agent 内核或取消语义。
