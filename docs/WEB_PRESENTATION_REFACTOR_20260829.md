# Web 展示语义与 Markdown 重构记录（2026-08-29）

## 本轮边界

本轮只修改前端展示层与测试。没有修改 `hako/**`、`web/worker/**`、`web/backend/**`，也没有改变 DONE_READ_ONLY / DONE_VERIFIED、Session/Run、Verified Finish、ToolResult 或 termination reason 的判断规则。现有事件已经提供工具调用、`changedPaths`、`verification`、`finalText` 与 `stopReason`，因此没有新增后端 DTO。

## 展示映射

前端新增纯证据选择器 `src/utils/runPresentation.ts`，不使用关键词规则或 LLM 意图分类：

| 展示类型 | 可观察事实 | 主视觉 |
| --- | --- | --- |
| Conversation | `done_read_only`，无工具、无变更、无验证 | 自然的用户消息与 Assistant Markdown 回答 |
| Repository Analysis | `done_read_only`，存在仓库工具调用、无变更 | 轻量 Analysis Result、调查范围、可展开调查过程 |
| Verified Change | 存在变更路径，`done_verified`，且验证条目包含 kind/command/summary | 绿色 Verified Result、修改文件与验证证据 |
| Non-success | unverified / denied / cancelled / error / incomplete 等 | 警告或错误结果，不包装成成功 |

`RunTimeline.vue` 在 Run 结束后依据同一选择器决定信息层级：Conversation 与 Analysis 收起默认工具噪声；Analysis 的调用链保留在“查看调查过程”；Verified Change 继续显示调查、修改、验证主线。运行次数、token、终止原因等诊断信息统一进入折叠的“运行详情”。

## Markdown 链路

新增 `MarkdownContent.vue` 与 `src/utils/markdown.ts`。正文使用 Marked 的 GFM 模式并明确设置 `breaks: false`，因此普通单换行按自然文本流渲染，空行才分段。渲染结果经 DOMPurify 清洗；链接补充 `target="_blank"` 与 `rel="noopener noreferrer"`；表格增加局部横向滚动容器。当前覆盖段落、标题、列表、代码围栏及语言类名、行内代码、粗体、斜体、引用、表格和链接。

## 组件变化

- `GoalResult.vue`：从统一大卡片改为展示分发器。
- `ConversationResult.vue`：普通回答 transcript。
- `AnalysisResult.vue`：只读仓库分析及折叠调查轨迹。
- `VerifiedResult.vue`：只用于有真实变更和验证证据的成功结果。
- `OutcomeResult.vue`：未验证、拒绝、取消、错误和未完成状态。
- `RunDiagnostics.vue`：收纳内部运行元数据。
- `EventItem.vue`：`run_started` 改为自然“你”的消息，不再显示“目标 N / 用户任务”。
- `presentation.ts`：COMPLETED 只有 `done_verified` 使用绿色“已验证完成”，只读完成显示中性“已完成”。

## 验证结果

执行命令：

```text
npm test
npm run build
```

结果：专项前端测试、Vue TypeScript 检查与 Vite 生产构建均通过。测试覆盖普通省会问答、Java/C++ Markdown 长回答、仓库只读分析、带变更与验证的修改、DONE_UNVERIFIED、ERROR、CANCELLED、DENIED、INCOMPLETE，以及 DONE_VERIFIED 缺证据时不得显示为 Verified。当前统一测试数字见 `PROJECT_STATUS.md`。

浏览器使用 Fake Worker 完成 `read → edit → test → done_verified` 闭环，最终页面只在 Verified Result 使用绿色强调；工具参数和原始证据默认折叠；浏览器控制台没有 error/warning。Real Worker 与 Fake Worker 都通过同一个前端事件选择器，不存在两套展示判断。

## Conversation transcript 角色层级收束

随后将时间线从“事件平铺”重组为真正的 Run 会话对：`conversation-turn` 同时持有右侧 compact 用户 prompt 与左侧开放式 hako response。User 通过右对齐、浅色 surface 和“你”头像区分；hako 通过左对齐、无气泡 Markdown 正文和深色 `h` 头像区分，角色时间紧邻名称。取消轮次之间的全宽分隔线，改用成对留白。

工具活动现在统一位于对应 `.assistant-message > .assistant-content > .assistant-activity-stream` 下并再缩进一级；参数、stdout/stderr、修改前后内容仍是工具内部的二级 `details`。`VerifiedResult`、Analysis Result 与非成功 Outcome 同样位于 hako response 末尾，不再成为独立顶层消息。普通聊天只保留 User → hako Markdown，Coding Task 则在同一 hako response 中增加执行轨迹和结果。

新增 `RunTimeline.test.ts` 验证三项结构不变量：User 必须先于 hako、工具和 Verified Result 必须是 hako response 后代、Repository Analysis 的调查轨迹必须留在对应回复内。专项测试、`vue-tsc --noEmit` 与 Vite 生产构建通过，并用历史普通问答与 mergesort 工程任务分别完成浏览器验收。全程未修改 Agent、Worker 或后端文件；当前统一测试数字见 `PROJECT_STATUS.md`。
