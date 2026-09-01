# hako 当前状态与提交前待办

更新日期：2026-08-30。本文只保留当前仍有效、可由代码或复测确认的事实。README 面向使用者，DESIGN 解释取舍，本文负责当前进度与统一证据口径。

## 当前定位

`hako` 是从零实现的本地通用 Coding Agent，可完成 Bug 修复、小型功能开发、局部重构和补充测试。核心主张是：模型负责开放式计划，本地内核负责工具、权限、路径、执行、上下文一致性和完成判定；模型说“完成”不能替代修改后的真实验证证据。

## 已完成节点

| 日期 | 节点 | 当前有效结果 |
|---|---|---|
| 2026-08-27 | Agent 核心 | EventBus、主循环、OpenAI 兼容 LLM、手写工具 Schema、CLI 与双平台 CI |
| 2026-08-27 | 受控修改 | `edit_file` 唯一匹配 search-replace、CRLF/GBK 保留、0/多匹配显式失败 |
| 2026-08-27 | Verified Finish | 修改状态、结构化验证、只读/已验证/未验证等分层终止 |
| 2026-08-28 | 可靠执行 | 截断续跑、当前 Python 解释器 pytest、分级审批、shell 净副作用审计 |
| 2026-08-28 | 可选只读 subagent | 独立上下文，只拥有 list/read 权限；机制有测试，产品收益未证明 |
| 2026-08-28 | Web 控制台 | Vue 3 + Vant、Spring Boot REST/SSE、Python JSONL Worker |
| 2026-08-29 | 跨语言验证 | C/C++、Java 与常见构建命令可形成验证；作者文件与派生产物分离 |
| 2026-08-29 | Session/Run | 一 Session 多 Run 共享 Agent/Conversation；取消当前 Run 不关闭 Session、不回滚文件 |
| 2026-08-30 | 历史恢复 | SUSPENDED 会话以同 sessionId、新 workerId 恢复语义 Conversation；旧工具观察不恢复 |
| 2026-08-30 | Web 信息架构 | 普通对话、仓库分析、Verified Change 分别呈现；连续读取与修改分组，细节按需展开 |
| 2026-08-30 | PromoOps 收束 | 演示只保留 Run1 线上发布 Bug 与 Run2 Priority 产品迭代 |
| 2026-08-31 | 三层会话记忆 | RunMemory 硬事实由事件确定性生成；最近三轮自动携带，旧事实由 `search_session_history` 按需检索 |
| 2026-08-31 | 问答入口收束 | 知识问答无需手动选择工作区；空值由后端解析为首个受控默认目录，初始等待文案不再虚构文件调查 |
| 2026-08-31 | Run1 验证入口修复 | 通用 pytest 优先走仓库 `test.ps1`；项目测试脚本可登记为验证证据，修复 `27 passed` 却 `DONE_UNVERIFIED` 的误判 |
| 2026-08-31 | 模型配置统一 | 新增 `HAKO_API_KEY + HAKO_BASE_URL + HAKO_MODEL` 通用入口；旧提供商 Key 保持兼容，Web 继续展示 Worker 实际上报的模型名 |
| 2026-08-31 | Run2 Python 环境修复 | Windows bare Python 使用仓库或 `test.ps1` 明确声明的项目解释器；全量测试通过后不再鼓励额外猜测 venv 的内联冒烟 |
| 2026-09-01 | 项目指令与展示收束 | 根目录 `AGENTS.md` 作为低于安全规则的项目级约束；Web 在同一阶段按文件聚合读写；Run2 收缩为详情页 Priority 编辑 |

## 当前核心机制

### 工具和循环

- 五个核心工具：`list_dir`、`read_file`、`edit_file`、`write_file`、`run_command`。
- 模型每轮收到 Conversation 与手写 Tool Schema；本地解析、Schema 校验、路径边界和审批后才执行。
- 工具错误作为配对的 `ToolResult` 回写模型，允许调整方案；不可恢复错误才终止。
- 重复调用按进展阶段计数：成功作者修改或真实 shell 文件副作用开启新阶段，失败编辑不伪造进展。

### 上下文

- 作者文件修改后，同路径旧 `read_file` 结果立即失效，避免模型继续依据旧版本构造编辑。
- 长文件按行分页，长命令输出保留头尾并提供恢复提示。
- 每个 Run 结束后，从持久化事件确定性生成修改文件、验证命令/退出码、审批和失败；模型最终说明只作为非权威语义摘要。
- Run 边界只保留最近三组 user/assistant 语义对和有界 Session 事实，不恢复旧 tool call、读取内容或 stdout/stderr；更早事实由只读 `search_session_history` 检索。
- 当前没有实现运行中按 token 阈值自动摘要的通用 compaction；当前 Workspace 始终高于历史记忆，引用旧代码后必须重新读取。
- Session 启动时可有界读取 Workspace 根目录 `AGENTS.md`，把稳定项目规范放在 system prompt 之后、用户目标之前；它不是普通工具观察，不能覆盖安全和权限边界。

### 执行、安全和完成

- 所有文件路径必须在 Workspace 内；普通副作用需审批，高风险 shell 命令必须逐次确认。
- Windows 仓库若提供 `test.ps1`，通用 pytest 请求优先走项目入口；bare Python 在可发现时绑定仓库声明的项目解释器，不再把 hako 自身 venv 误当业务环境。
- shell 执行前后比较工作区快照，结构化报告创建、修改、删除和派生产物。
- 最后一次作者文件修改后必须出现受认可、退出码为 0 的测试、构建或静态检查，才能 `DONE_VERIFIED`；后续修改会使旧证据过期。
- Cancel 只停止后续执行并回收命令进程树，不回滚已落盘文件。

## 最终演示口径：同一 Session 两个 Run

### Run1：发布成功但线上仍使用旧版本

运营观察到“618 数码满减”发布 v2 后页面提示成功，刷新却仍显示线上 v1，320 元订单仍应付 300 元而不是草稿预览的 270 元。Agent 自己调查 API、Service、Repository、UoW 与测试，定位 `CampaignRepository.save()` 漏持久化 `published_revision_id`，只修改业务 Repository 并重新运行完整测试。

最终模板口径：Run1 初态 `1 failed, 26 passed`；修复后进入 Run2 的起点为 `27 passed`；独立 Run1 held-out 为 `9 passed`。一次保留的真实 Web 轨迹为 14 次模型决策、27 次工具调用、4 次审批，只修改 `campaign_repository.py`，以 `DONE_VERIFIED` 结束。该轨迹证明链路可闭合，不代表任意任务成功率。

### Run2：详情页 Priority 自助编辑

Run2 在 Run1 修改后的同一 Workspace、同一 Session 中继续。需求是让运营在活动详情页修改 Priority，保存并刷新后仍保持新值；只接受非负整数，错误输入不能污染旧值；活动列表维持只读，既有发布行为不能回归。交付覆盖 Service、Repository、API、页面和回归测试。

最终模板口径：Run2 完成基线 `32 passed`；独立 Run2 held-out 为 `4 passed`。演示主线是“一个真实线上 Bug + 一次边界清晰的产品迭代”。

演示仓库、只读标准模板、reset runner 和 held-out tests 位于相邻的本地 `promoops-demo`，不进入 CodingAgent Git 仓库。每次从 `run1_initial` 重建 `work/`，保证可重复测试且不会把 Agent 的答案写回初始模板。

## 当前统一测试口径

2026-09-01 当前工作树最近一次完整复测：

| 范围 | 结果 |
|---|---|
| Python Agent 核心 | `247 passed, 1 skipped` |
| Spring Boot 控制面 | `25 passed` |
| Vue/Vitest | `40 passed` |
| 前端生产构建 | TypeScript 检查通过，`324 modules transformed` |
| PromoOps Run1 初态 | `1 failed, 26 passed` |
| PromoOps Run1 修复 / Run2 起点 | `27 passed` |
| PromoOps Run2 完成态 | `32 passed` |
| 外部 held-out | Run1 `9 passed`；Run2 `4 passed` |

这些测试集检查对象不同，不合并成一个夸大的“总通过数”。Fake Worker 只用于 UI 和协议回归，不能计入模型能力证据；真实模型单次演示也不能写成成功率。

## 当前限制

- `edit_file` 只做精确唯一匹配；它会返回候选，但不自动应用模糊编辑。
- Verified Finish 只证明最后一次作者修改后某项验证成功，不证明覆盖充分或代码无隐藏缺陷。
- shell 快照与危险命令门禁不是操作系统沙箱；工作区外副作用和命令内已抵消的瞬时变化不可见。
- 工具保持串行；当前主线没有测出值得增加事件乱序和异常聚合复杂度的墙钟收益。
- 只有 Run 边界确定性裁剪，没有运行中自动摘要 compaction；达到上下文阈值或真实任务被阻断时再实现。
- 可选只读 subagent 的权限隔离有确定性测试，但没有稳定的自然采用和收益数据，不列为核心卖点。
- 历史恢复是语义重建，不是旧 Python 进程、模型隐藏状态或工具快照的精确续传。
- Web 适合本机单用户，没有登录、TLS、远程执行、多租户或系统级沙箱。

## 提交前待办

| 优先级 | 待办 | 完成标准 |
|---|---|---|
| P0 | 审阅并提交当前改动 | 一个机制一个 commit；不 stage `.env`、`tmp/`、视频或私人配置 |
| P0 | 统一公开叙事 | README、README.txt、DESIGN、视频只使用最终 Run1/Run2 和当前测试口径 |
| P0 | 最终全量复测 | 重新运行 Python、Spring、前端测试与构建；结果与文档一致 |
| P0 | 密钥与文件检查 | `git diff --check`；扫描 API Key；确认 `.claude/.codex/tmp/.env` 未跟踪 |
| P1 | 录制两分钟视频 | Real Worker；展示 Bug→修复→验证→同 Session 产品迭代；mp4 ≤200 MB |
| P1 | 公开仓库与打包 | 填写 README.txt 仓库地址，保留提交历史，截止后不再 push |

## 提交纪律

真实 API Key 只保存在本地 `.env`；公开 `.env.example` 只放空变量和说明。开发使用 AI 辅助是允许的，但候选人必须能解释每项设计选择、失败现场、代价和验证。由使用者审阅后手动 stage、commit 和 push；自动化工具不擅自改写 Git 历史。
