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
| 2026-08-30 | PromoOps 收束 | 演示只保留 Run1 线上发布 Bug 与 Run2 Priority/Conflict 产品迭代 |

## 当前核心机制

### 工具和循环

- 五个核心工具：`list_dir`、`read_file`、`edit_file`、`write_file`、`run_command`。
- 模型每轮收到 Conversation 与手写 Tool Schema；本地解析、Schema 校验、路径边界和审批后才执行。
- 工具错误作为配对的 `ToolResult` 回写模型，允许调整方案；不可恢复错误才终止。
- 重复调用按进展阶段计数：成功作者修改或真实 shell 文件副作用开启新阶段，失败编辑不伪造进展。

### 上下文

- 作者文件修改后，同路径旧 `read_file` 结果立即失效，避免模型继续依据旧版本构造编辑。
- 长文件按行分页，长命令输出保留头尾并提供恢复提示。
- 历史恢复只重建完整的 user/assistant 语义对，不恢复旧 tool call、读取内容或 stdout/stderr。
- 当前真实主线没有接近上下文容量，也没有出现 context-length 错误，因此暂不实现通用 compaction。

### 执行、安全和完成

- 所有文件路径必须在 Workspace 内；普通副作用需审批，高风险 shell 命令必须逐次确认。
- Windows bare pytest 使用启动 hako 的当前 Python；显式解释器保持原样。
- shell 执行前后比较工作区快照，结构化报告创建、修改、删除和派生产物。
- 最后一次作者文件修改后必须出现受认可、退出码为 0 的测试、构建或静态检查，才能 `DONE_VERIFIED`；后续修改会使旧证据过期。
- Cancel 只停止后续执行并回收命令进程树，不回滚已落盘文件。

## 最终演示口径：同一 Session 两个 Run

### Run1：发布成功但线上仍使用旧版本

运营观察到“618 数码满减”发布 v2 后页面提示成功，刷新却仍显示线上 v1，320 元订单仍应付 300 元而不是草稿预览的 270 元。Agent 自己调查 API、Service、Repository、UoW 与测试，定位 `CampaignRepository.save()` 漏持久化 `published_revision_id`，只修改业务 Repository 并重新运行完整测试。

最终模板口径：Run1 初态 `1 failed, 26 passed`；修复后进入 Run2 的起点为 `27 passed`；独立 Run1 held-out 为 `9 passed`。一次保留的真实 Web 轨迹为 14 次模型决策、27 次工具调用、4 次审批，只修改 `campaign_repository.py`，以 `DONE_VERIFIED` 结束。该轨迹证明链路可闭合，不代表任意任务成功率。

### Run2：Priority 与同优先级冲突

Run2 在 Run1 修改后的同一 Workspace、同一 Session 中继续。需求是允许非负 Priority；同范围多个活动只选择最高者；保存后若形成同范围同 Priority 冲突，必须立即指出冲突对象；发布冲突不得留下半完成状态；不同范围互不影响。交付覆盖领域逻辑、Service、API、页面和回归测试。

最终模板口径：Run2 完成基线 `36 passed`；独立 Run2 held-out 为 `6 passed`。演示主线是“一个真实线上 Bug + 一次真实产品迭代”。

演示仓库、只读标准模板、reset runner 和 held-out tests 位于相邻的本地 `promoops-demo`，不进入 CodingAgent Git 仓库。每次从 `run1_initial` 重建 `work/`，保证可重复测试且不会把 Agent 的答案写回初始模板。

## 当前统一测试口径

2026-08-30 当前工作树最近一次完整复测：

| 范围 | 结果 |
|---|---|
| Python Agent 核心 | `203 passed, 1 skipped` |
| Spring Boot 控制面 | `18 passed` |
| Vue/Vitest | `34 passed` |
| 前端生产构建 | TypeScript 检查通过，`324 modules transformed` |
| PromoOps Run1 初态 | `1 failed, 26 passed` |
| PromoOps Run1 修复 / Run2 起点 | `27 passed` |
| PromoOps Run2 完成态 | `36 passed` |
| 外部 held-out | Run1 `9 passed`；Run2 `6 passed` |

这些测试集检查对象不同，不合并成一个夸大的“总通过数”。Fake Worker 只用于 UI 和协议回归，不能计入模型能力证据；真实模型单次演示也不能写成成功率。

## 当前限制

- `edit_file` 只做精确唯一匹配；它会返回候选，但不自动应用模糊编辑。
- Verified Finish 只证明最后一次作者修改后某项验证成功，不证明覆盖充分或代码无隐藏缺陷。
- shell 快照与危险命令门禁不是操作系统沙箱；工作区外副作用和命令内已抵消的瞬时变化不可见。
- 工具保持串行；当前主线没有测出值得增加事件乱序和异常聚合复杂度的墙钟收益。
- 没有通用 compaction；达到上下文阈值或真实任务被历史阻断时再实现。
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
