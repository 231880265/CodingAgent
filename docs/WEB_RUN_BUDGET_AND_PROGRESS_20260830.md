# Web Run 安全预算与进度呈现修复记录（2026-08-30）

## 现场结论

PromoOps Run2 并非 Worker 崩溃：它在第 40 次模型决策后刚完成 `campaign_service.py` 编辑，尚未来得及重新运行测试，随后以内核 `max_steps` 结束。历史中同类任务曾在 Web 安全预算为 100 时于第 52 次完成并通过验证，说明固定 40 对该综合场景过低。

## 本次改动

- 删除 Web 输入框中的“最大模型决策”设置；新建、追问和恢复会话统一使用 100 次内部安全预算。
- 不删除 `hako.loop` 的最终步数保险，也不改变 Verified Finish、stuck detector 或 Session/Run 语义。该保险用于阻止失控循环和无限 token 消耗，正常任务仍会被更早的完成、错误、拒绝、取消或重复调用检测终止。
- `max_steps` 不再渲染成红色“运行失败”，而显示“本轮已暂停，可以继续”；Conversation 与已落盘修改会保留。
- 审批面板新增“为什么需要这一步”，根据真实工具名与参数解释只读搜索、测试/构建或文件修改的目的和副作用。
- 未完成工具显示“正在读取/修改/执行…”动态状态；工具结束后切换为“已读取/已修改/测试通过”等事实状态。模型调用间隙只显示由最近真实事件推导的阶段状态，不伪造隐藏思维。
- system prompt 要求模型用简短中文公开可核查进度：说明正在核对什么、已确认事实和下一步动作，不输出隐藏思维链。

## 验证

- 前端：`34 passed`；`npm run build` 成功，324 个模块参与构建转换。
- Agent：完整 pytest 为 203 passed、1 skipped；最大步数止损、Verified Finish 与 stuck detector 回归均通过。
- Spring Boot：18 个测试全部通过。
- 浏览器 mock 验收：启动页不再出现步数设置；审批目的、等待状态、修改与测试证据正常显示；控制台无 error/warning。

## 对 PromoOps 现场的处理

本次没有直接修改 `promoops-demo/work`，因此当时 Run2 的失败轨迹和 10 个已落盘文件仍可用于复盘。该版综合 `Priority/Conflict` 场景已于 2026-09-01 被更聚焦的“活动详情页 Priority 自助编辑”Run2 取代，本节旧续跑提示仅是历史记录，不再用于当前演示或评测。当前口径以 `promoops-demo/issues/run2.md`、`README.md` 与 `PROJECT_STATUS.md` 为准。

## 回退范围

若需回退本次机制，只需恢复 Web 默认预算、`TaskComposer`、运行结果映射、审批/时间线组件、样式和 prompt；PromoOps 演示仓库不在本次修改范围内。
