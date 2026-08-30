# hako 项目时间线与待办

更新日期：2026-08-29。本文只记录可由代码、测试或运行结果确认的事实；计划变化时只更新这里，README 保持面向使用者。

## 已完成时间线

| 日期 | 节点 | 完成内容 | 证据 |
|---|---|---|---|
| 2026-08-27 | D1 核心骨架 | 事件总线、Agent 主循环、四个初始工具、配置、上下文机制和 TUI | commit `b089f8e` |
| 2026-08-27 | 双平台 CI | GitHub Actions 在 Windows / Ubuntu、Python 3.12 上运行测试 | commit `513fbd2` |
| 2026-08-27 | V4-Flash 接入 | 硅基流动默认模型、输出 token 上限、thinking 开关和请求参数测试 | 当前本地改动，尚未提交 |
| 2026-08-27 | 真实闭环 #1 | `list→read→write→pytest→结束` | 5 次模型决策，8,011 tokens，约 28.5 秒；独立复测 `2 passed` |
| 2026-08-27 | 局部编辑 | 新增唯一匹配 `edit_file`，接入审批、陈旧读取失效和 TUI | 0/1/多匹配、重叠匹配、CRLF、GBK、路径逃逸测试通过 |
| 2026-08-27 | Verified Finish | 增加修改状态、验证证据和三类完成结果 | 核心回归 `140 passed, 1 skipped` |
| 2026-08-27 | 真实闭环 #2 | `list→read→edit→pytest→DONE_VERIFIED` | 5 次模型决策，9,662 tokens，约 51.3 秒；独立复测 `3 passed` |
| 2026-08-28 | 本地评测标本 | Router Header、SSE 流式解析、EAGLE + DP 三个隔离项目、隐藏测试和统一 runner | 全部位于已忽略 `tmp/local-evals/`，每轮新建副本并保存 JSON/diff |
| 2026-08-28 | 当前 Agent 基线 | 三个场景各冻结第一次真实运行 | Router 通过；SSE 只分析未修改；EAGLE 测试全过但修改越界 |
| 2026-08-28 | 只读 subagent | 独立上下文、read/list 物理权限、一次委派、成本回传与 TUI 事件 | 真实探针 4 步、5,542 tokens、零文件修改；自然 EAGLE 两次均未采用 |
| 2026-08-28 | 数据门控 | 根据实测决定 compaction 与只读工具并发 | 上下文峰值 0.6241%，read/list 墙钟占比 <0.1%，两项均不实现 |
| 2026-08-28 | 截断续跑 | `finish_reason` 进入终止判断，有限续跑后明确失败 | 确定性测试覆盖行动恢复与连续截断；不再误报 `DONE_READ_ONLY` |
| 2026-08-28 | pytest 解释器一致性 | Windows bare pytest 使用当前 `sys.executable -m pytest` | EAGLE post-fix 无工具失败、无 `conftest.py` 越界、公开/隐藏全过 |
| 2026-08-28 | 分级权限与 shell 审计 | 默认拒绝、危险命令逐次确认、命令前后净文件变化进入 `touched_paths` | 真实子进程测试覆盖新增/修改/删除/超时；核心回归 `166 passed, 1 skipped` |
| 2026-08-28 | `DESIGN.md` 第一版 | 将当前实现整理为十二项可答辩决策，并区分事实、推断和未证明边界 | 361 行；代码引用路径全部存在；未把 compaction、并发或 subagent 收益写成已证明能力 |
| 2026-08-28 | Web 控制台闭环 | Vue 3 + Vant 4、Spring Boot REST/SSE、真实/假 Python Worker 与刷新恢复 | Python `174 passed, 1 skipped`；Java `13 passed`；前端构建通过；浏览器完成两次审批与 Verified Finish |
| 2026-08-29 | 跨语言验证与 Web 证据主线 | 修复 C++ 构建产物误清验证，扩展常见语言构建识别；过程区改为结构化主事件 + 灰色内部细节 | Python `188 passed, 1 skipped`；Java `13 passed`；前端构建及 `1280×720`、`1024×700` 浏览器复测通过 |
| 2026-08-29 | Web Session/Run P0+P1 | 一 Session 多 Run 共享 Agent/Conversation；取消本轮保活 Worker；独立新会话；附件；SQLite 只读历史；迟到事件隔离 | Python `191 passed, 1 skipped`；Spring Boot `15 passed`；前端 303 模块构建与完整浏览器状态流通过；P2 明确不做 |

## 本轮具体做了什么

### `edit_file`

- 接口为 `path + old_text + new_text`，只修改已有文本文件的唯一匹配片段。
- 0 匹配不落盘，返回最接近候选及行号；多匹配和重叠匹配不猜测，要求增加上下文。
- 模型从 `read_file` 得到 LF 文本时仍可编辑 CRLF 文件，只转换目标片段，不制造全文件换行 diff。
- 编辑已有 GBK 文件时保留原编码；新文本无法编码时先在内存失败，不截断原文件。
- 成功编辑声明规范化 `touched_paths`，让旧读取失效并触发 Verified Finish 的 `DIRTY` 状态。
- 审批界面同时展示路径、原片段和新片段；`write_file` 继续负责新文件或小文件整体重写。

### Verified Finish

完成标准是：最后一次 `edit_file/write_file` 成功后，必须出现一次内核认可、退出码为 0 的测试、构建或静态检查，并且之后没有再次修改。

```text
edit/write → DIRTY
DIRTY + 验证失败 → DIRTY
DIRTY + 验证成功 → VERIFIED
VERIFIED + 再次修改 → DIRTY
```

- 结果区分 `DONE_READ_ONLY`、`DONE_VERIFIED` 和 `DONE_UNVERIFIED`；只有前两者令 CLI 返回成功。
- 模型在 `DIRTY` 状态直接结束时，内核会回传一次完成检查并要求继续验证；再次直接结束则明确记为未验证失败，不无限循环。
- 验证证据结构化保存类型、原命令、摘要和发生时的模型决策序号，不从模型自述或 TUI 文本推断。
- 只认可单一、实际执行的常见验证命令；覆盖 Python/Node/Rust/Go/.NET/Java 测试、C/C++ 与 Java 编译及主流构建工具；`pytest --collect-only`、`pytest || true`、管道和命令拼接不算证据。
- 任何后续写入或后续验证失败都会清空旧证据；交互模式下每个新任务重新建立运行状态。
- shell 生成的 `.exe/.class/.jar/.o` 与常见构建目录会保留在审计和界面中，但归入 `derived_paths`，不会把成功构建后的状态重新打回 `DIRTY`；源码、配置和脚本仍计入 `authored_paths`。

## V4-Flash 真实测试记录

两次调用均使用 `deepseek-ai/DeepSeek-V4-Flash` 和硅基流动 OpenAI 兼容端点，临时仓库位于主仓库的 `tmp/` 下并被忽略；`-y` 只用于这些明确授权的隔离仓库。

| 项目 | 闭环 #1：整文件写入 | 闭环 #2：局部编辑与验证完成 |
|---|---|---|
| 隔离仓库 | `tmp/v4-flash-smoke-20260827/` | `tmp/v4-flash-edit-verified-20260827/` |
| 基线 commit | `563b13d` | `b6d8b0c` |
| 初始测试 | `2 failed` | `2 failed, 1 passed` |
| 核心轨迹 | `list→read×2→write→pytest→结束` | `list→read×2→edit→pytest→DONE_VERIFIED` |
| 唯一语义差异 | `left - right` → `left + right` | 折扣公式 `1 + percent / 100` → `1 - percent / 100` |
| 测试文件 | 未修改 | 未修改 |
| 模型运行 | 5 次模型决策，8,011 tokens，约 28.5 秒 | 5 次模型决策，9,662 tokens，约 51.3 秒 |
| 独立复核 | `2 passed in 0.02s` | 仅 `M pricing.py`，`git diff --check` 通过，`3 passed in 0.03s` |

证据边界：两个受控样例证明接口、tool calling、局部编辑和完成判定可以闭环，不能推出任意 Bug 的成功率。真实密钥只保存在被忽略的本地 `.env`；代码、测试和文档未保存密钥、请求头或完整凭据。

## 2026-08-28 三场景本地评测

三个项目是从真实事故抽出的可运行故障标本，不复制生产 GPU/网络环境，也不提交 Git。固定标准包含修复前失败、`DONE_VERIFIED`、公开与隐藏测试、源码 diff、修改白名单和禁改项。

| 场景 | 第一次结果 | 步骤 / tokens / 时间 | 主上下文峰值 | 关键发现 |
|---|---|---|---|---|
| Router Header | 通过 | 8 / 25,968 / 39.368s | 4,434（0.4434%） | 只改归一化函数；直接 pytest 失败后恢复为 `python -m pytest` |
| SSE Stream | 失败 | 4 / 12,298 / 111.019s | 2,745（0.2745%） | 模型推导正确但未调用编辑工具，误以只读完成结束 |
| EAGLE + DP | 失败 | 11 / 48,842 / 49.667s | 6,241（0.6241%） | 功能与隐藏测试全过，但为导入环境新增 `conftest.py`，越过白名单 |

证据边界：这是三个不同难度任务的单次案例，不能换算成成功率。它们分别暴露了可恢复工具失败、模型“会说但未行动”、以及功能正确但工程范围不合格。

只读 subagent 默认关闭。离线测试证明子模型只拿到 `read_file/list_dir`，无 shell、写入和递归委派；真实机制探针在 EAGLE 上 4 步完成跨文件调查，5,542 tokens，独立上下文峰值 2,021，文件前后零变化。但启用模式的两次自然主 Agent 运行均未调用该工具，因此没有自主采用、提速或省 token 结论。

硅基流动官方标注 V4‑Flash 为 1M 上下文。自然任务最高只用 0.6241%，未达到 compaction 的 70% 实现门槛；三条基线的 list/read 累计耗时分别为 36ms、6ms、46ms，均不足墙钟 0.1%，未达到并发的 10% / 1s 门槛。两项都因无真实需求而不实现。

## 截断续跑与解释器一致性修复

SSE 旧基线的模型文本在句中达到输出上限，但 `LLMClient` 没有保留 `finish_reason`，内核仅凭“无工具调用”误判 `DONE_READ_ONLY`。现在 `length/max_tokens` 或 completion usage 撞到上限都会触发明确的 continuation；最多两次，仍截断则 `INCOMPLETE`。确定性测试证明截断后可以继续写入并验证，也证明连续截断不会被算作成功。

Windows `run_command` 只把 bare `pytest/pytest.exe` 改写为当前 `sys.executable -m pytest`；显式解释器和非 Windows 命令保持原样。EAGLE post-fix 第一次测试即通过，只改两个允许文件，公开与隐藏测试均通过；旧基线中的全局 pytest 失败、sys.path 调查和 `conftest.py` 越界没有再出现。

| 场景 | 修复后结果 | 步骤 / tokens / 时间 | 证据边界 |
|---|---|---|---|
| SSE | 通过，`DONE_VERIFIED` | 14 / 85,870 / 203.711s | 只改 parser，公开/隐藏全过；本次未触发 continuation，续跑因果证据来自确定性测试 |
| EAGLE | 通过，`DONE_VERIFIED` | 7 / 25,211 / 118.442s | 0 工具失败、无越界文件、bare pytest 由当前 `.venv` 一次通过；不因单次墙钟声称提速 |

## 分级权限与 shell 净副作用审计

权限检查现在分三层：只读工具不询问；普通写入和命令默认询问，可由 `-y` 或会话内记忆放行；高风险 shell 命令必须逐次人工确认，检查顺序早于所有快捷放行。非交互环境没有人能给出显式同意，因此普通操作必须使用 `-y`，高风险操作即使有 `-y` 也拒绝。直接把 `Agent` 当库使用而忘记传审批策略时，默认拒绝所有 `needs_approval` 工具，不再默认全授权。

`DANGER_PATTERNS` 已从未接线的字符串列表变成保守词法门禁，覆盖递归/强制删除、Git push/reset/clean、网络下载、系统/磁盘破坏和 DROP 语句，并处理 `Remove-Item` 参数顺序等变体。它不是完整 shell AST；误报的代价是多问一次，不能把它宣传成系统沙箱。

`run_command` 执行前后会对工作区做快照，向 `ToolResult` 结构化报告新增、修改和删除路径，并把三者并入 `touched_paths`。其中编译产物归入 `derived_paths`，仍会让历史读取失效并进入事件审计，但只有 `authored_paths = touched_paths - derived_paths` 会推进 Verified Finish 的业务修改状态。因此 shell 写源码、失败后留下业务文件或超时前产生业务文件都会进入 DIRTY，而成功编译生成 `qsort.exe` 不会反向抹掉本次 build 证据。测试缓存、版本库内部状态、虚拟环境、依赖目录和 `tmp` 生成区会在 `os.walk` 入口剪枝；小于 1MB 的业务文件额外做内容摘要，能识别同大小且恢复 mtime 的覆盖。

初版在当前含多份回退材料的仓库中单次快照约 2.5s；剪枝生成目录后实际扫描 43 个业务文件，单次快照约 17.85ms。这个数字只代表当前仓库快照，不外推到任意大型项目。机制只能观察命令返回时仍存在的净变化：同一命令内部创建后删除、工作区外副作用、并发进程归因及大于 1MB 且刻意保持 metadata 的覆盖仍是边界。

## 2026-08-28 Web 一键启动脚本

新增仓库根目录 `start-web.ps1`，把原本需要两个 PowerShell 手动配置的启动流程收束为一条命令。脚本默认启动真实 Worker，也支持 `-Mode Fake` 的无模型联调；它统一设置后端仓库路径、允许访问范围、Python 解释器、Worker 入口和前端 API 代理，检查 Java 21、Node.js 及虚拟环境，并在缺少前端依赖时自动执行 `npm install`。前后端在当前 VS Code 终端并行运行，任一服务异常退出或用户按 `Ctrl+C` 时，脚本只回收自己启动的两个进程树。`-CheckOnly` 可在不启动服务、不安装依赖的情况下完成环境预检。实际冒烟还发现 Maven Wrapper 3.3.4 在 Windows 普通 `.m2` 目录上直接访问空的 `Target[0]` 会退出；现改为先区分普通目录与符号链接，不改变 Maven 版本、下载地址或校验和。验证结果：PowerShell 5.1 语法解析通过，当前 Java 21.0.5、Node.js 22.12.0 和前端依赖预检通过，Maven Wrapper 固定解析到 3.9.16，Fake 模式下后端健康接口与前端首页均返回 HTTP 200；Fake 结果不计作真实模型能力证据。

## 2026-08-28 Web 视口与滚动边界修复

Web 外壳改为严格占满 `100dvh`，`html/body/#app` 禁止页面级滚动，Header、Footer 和提示条固定参与纵向布局，三栏工作区自动获得全部剩余高度。桌面端左侧任务卡完整固定，中央只滚动事件时间线，右侧只在审批与证据超过高度时内部滚动；移除了中央面板强制 `540px` 最小高度造成的小窗口溢出，并压缩任务输入区的最低高度。`900px` 以下改为单列块级流，由主工作区内部滚动，避免 Grid 隐式行在窄屏把左右栏压成零高度。

验证覆盖空闲态和等待审批运行态：`1366×650`、`1024×768`、`1440×900` 的页面 `scrollHeight` 均等于视口高度，左表单无溢出；运行态低高度窗口中时间线和证据栏按预期独立滚动；`800×700` 单列内容顺序正确、页面外壳仍不滚动。前端类型检查与生产构建通过。本次只修改布局样式和任务文本域尺寸，没有修改 Agent、后端或接口状态机。

## 2026-08-29 跨语言 Verified Finish 与 Web 执行主线

真实 C++ 快排任务暴露了一个内核矛盾：DeepSeek 通过 `write_file` 创建 `qsort.cpp`，再调用 `g++` 和 `qsort.exe`；旧注册表不把 `g++` 识别为 build，且 shell 快照又把生成的 `qsort.exe` 当作新的业务修改，于是模型看到运行成功，内核却只能返回 `DONE_UNVERIFIED`。现在验证注册表增加 `g++/clang++/c++/cl/javac/cmake --build/make/ninja/msbuild`，文件副作用明确区分人工修改与派生产物；端到端回归固定 `write → g++ → derived qsort.exe → DONE_VERIFIED`，同时验证 `bin/release.ps1` 仍是人工文件，避免用过宽规则掩盖真实修改。

Web 不再把每条协议事件等权铺满时间线。`tool_call_started` 与 `tool_call_finished` 按 `callId` 配对，黑色主文字只由工具结果和内核状态生成，例如“已读取文件”“已完成局部修改”“构建通过”；模型说明、调用参数、原始输出、审批记录、token 和状态同步收进灰色可展开区。顶部“回合”统一改为“模型决策”，说明它表示一次模型请求，不等于一次工具调用。取消按钮缩短并禁止换行，审批面板改为中性卡片，只用少量风险色表达状态；`DONE_UNVERIFIED` 显示为“已结束 · 验证不足”，与基础设施错误分开。

事件协议为 `tool_call_finished` 增加 touched/created/modified/deleted/derived paths 与 verification kind/command，前端不再解析模型自述猜阶段。验证结果：核心聚焦回归 `93 passed`，Python 全量 `188 passed, 1 skipped`，Spring Boot `13 passed`，前端 TypeScript/Vite 构建通过；浏览器在 `1280×720` 和 `1024×700` 下完成等待审批与 Verified Finish 流程，页面无外层滚动，取消按钮、审批面板和成对工具活动均正常。沙箱内首次 Java 集成测试因 Windows 用户 Temp 权限不匹配中止，使用正常 Windows 权限重跑后 13 项全过，该环境错误不计作产品失败。

## 2026-08-29 Web Session/Run P0+P1

Web 的核心对象已从“一个 task 绑定一次 Agent”调整为 `Workspace → Session → Run`：每个 Session 持有一个 Worker、一个 Agent 和一个 Conversation，用户在同一 Session 里继续发送 Run 时只生成新 runId，复用 workerId 与前文；同一 Workspace 可以先后建立多个上下文隔离的 Session。普通“停止本轮”只把 Run 推进到 `CANCELLING → CANCELLED`，终止活动命令树但保留 Worker、Conversation 和已落盘文件；`DENY` 只是一条工具 observation。只有独立“新会话”才按 cancel → 等待 CANCELLED → close → 等待 CLOSED 的顺序关闭旧 Session，前端确认旧 Worker 退出后才清理时间线。

输入框 `+` 现在只添加本 Run 的文本/日志/代码附件，Workspace 仍由独立入口决定；附件以 user context 送入同一个 Conversation，不获得系统指令地位。事件信封改为 sessionId 必填、runId 按层级可选，服务端与前端各做一次身份过滤，且所有终态不可逆。P1 使用 `.hako/web-history.db` 保存 Session、Run 和完整展示事件，历史可以只读复盘但不能恢复 Python Agent/Conversation；P2 暂不实现。详细决策、API 与完成记录分别见 `docs/WEB_SESSION_RUN_ARCHITECTURE.md`、`docs/WEB_CONSOLE_API.md` 和前后端完成手册。

实现过程中真实发现 Windows `taskkill` 在受限环境可能只结束 PowerShell 父进程，子 Python 仍持有输出管道。现改为先向独立进程组发送 `CTRL_BREAK`，再用 `taskkill /T /F` 兜底；回归要求取消 30 秒命令在 5 秒内返回，而不是只检查状态字符串。正常关闭 Worker 也去掉了重复 `worker_exited` 事件，避免进程回调与 terminate 回调生成两条冲突证据。

## 2026-08-29 STUCK 阶段修复与 PromoOps 连续彩排

重复调用止损器完成最小修复：只有工具产生真实 `touched_paths` 才清空重复签名计数，因此成功编辑或 shell 净文件副作用会开启新阶段，失败编辑不会伪造进展，纯 `read → read → read` 仍会 STUCK。聚焦回归 4 项、`test_loop.py` 39 项和 Python 全量均通过；全量口径为 `194 passed, 1 skipped`。

真实 Worker 在同一个 Session 中从 Run1 模板连续完成三个产品 Run：线上版本指针 Bug 修复、Priority/冲突开发、发布审计/版本回滚，三轮均为 `done_verified`。首次外部 held-out 仍发现 API 方法、业务版本字段和页面文案契约差异；这些失败没有被包装成成功，而是作为同一 Session 的两个 QA follow-up 输入。最终仓库公开测试 `61 passed`，独立 Run1/Run2/Run3 held-out 分别 `9/6/5 passed`，20 项全部通过。完整过程、Run ID、失败现场和 reset 命令见 `docs/PROMOOPS_REHEARSAL_20260829.md`。

## 当前限制

- `edit_file` 是精确 search-replace：它会给近似候选，但不会自动应用模糊匹配，这是有意保留的安全边界。
- Verified Finish 证明“最终修改后某项验证成功”，不证明测试覆盖充分，更不是形式化正确性证明。
- shell 净副作用已进入 `touched_paths`，但它不是操作系统沙箱，也不能观察工作区外或命令内部已经抵消的瞬时变化。
- 高风险检查是保守词法门禁，不是 PowerShell/sh 完整语法分析；真正的进程级隔离仍需外部沙箱。
- 有陈旧读取失效和工具输出截断；本地评测离上下文阈值很远，因此没有长对话 compaction。
- 工具串行执行；实测 list/read 占墙钟不足 0.1%，当前没有并发收益证据。
- 已有三个固定本地故障标本与单次基线，但没有重复样本意义上的成功率或统计消融结论。
- 已修复“达到输出上限却无工具”的误结束；若模型以正常 `stop` 明确结束但用户意图实际要求修改，内核仍没有通用的语义意图分类器。
- 历史 Session 当前只保存展示数据，不能重新构造旧 Agent/Conversation；这是明确的 P1 边界，不应描述为“可恢复对话”。
- Windows bare pytest 已绑定当前解释器；常见 C/C++、Java 和构建命令可成为验证证据，但注册表不是任意语言/工具的自动语义识别器，新工具链仍需显式扩展和测试。
- 只读 subagent 权限和直接机制已验证，但当前 EAGLE 标本没有自然采用证据。
- `DESIGN.md` 第一版已经对齐代码中的 #1–#9 锚点并扩展到 #12；最终提交前仍需在代码冻结后补稳定 commit/行号链接。
- Web 已保存 P1 只读 Session/Run 历史，但服务重启后不能恢复原 Worker、Agent 或 Conversation；没有登录、TLS、远程执行或系统级命令沙箱，只适合绑定本机地址。
- 真实 Worker Web smoke 和同 Session 多 Run 已有证据；Fake Worker 仍只用于确定性 UI/协议回归，不能混入模型能力数字。

## 待办与优先级

| 优先级 | 待办 | 完成标准 |
|---|---|---|
| P0 | 审阅并提交当前本地改动 | `.env` 和 `tmp/` 不入库；配置、编辑/完成判定、可靠行动、安全审计和文档按机制拆分 commit |
| P0 | 整理真实 Worker Web smoke 证据 | 已完成连续 Run 与 held-out；提交前仅需把脱敏截图、Run ID 和 `docs/PROMOOPS_REHEARSAL_20260829.md` 对齐视频口径 |
| P1 | 保留本地评测 | 三个故障标本、隐藏测试和真实结果继续留在 ignored `tmp/`，不混入公开仓库 |
| P1 | 审校 `DESIGN.md` | 代码冻结后补 commit/行号锚点；与最终 README.txt、视频口径逐项一致 |
| P2 | 准备交付物 | 约 2 分钟演示视频、精简 README.txt、公开仓库和最终 zip，与实测数据一致 |

## 建议时间节点

| 日期 | 目标 | 当天可交付结果 |
|---|---|---|
| 8/27（原计划 8/28） | 编辑与完成判定 | `edit_file`、Verified Finish、测试和真实 V4-Flash 闭环已完成；待手动提交 |
| 8/28 | 评测与真实场景 | 三个本地基准、runner、当前基线、只读 subagent 与数据门控已完成 |
| 8/29 | 可靠行动与安全 | 截断续跑、当前解释器 pytest、分级权限和 shell 净副作用审计均已完成 |
| 8/30 | 设计辩护与 Web smoke | `DESIGN.md` 初稿和 Web 确定性闭环已提前完成；补最终锚点并跑一次真实 Worker Web smoke |
| 8/31 | 冻结与演示 | 录屏、README.txt、仓库清理；不再增加大功能 |
| 9/1–9/2 | 缓冲与提交 | 全量复测、密钥扫描、公开仓库、打包提交；9/2 24:00 前完成最终 push |

## 测试与提交纪律

测试口径固定为当前解释器运行：Python 核心测试 `194 passed, 1 skipped`，Spring Boot 与前端口径仍按各自完成手册记录；PromoOps 公开测试 `61 passed`，外部 held-out 为 Run1/Run2/Run3 `9/6/5 passed`。这些测试集含义不同，不合并成一个夸大的总数。每次提交前还应运行 `git diff --check` 和公开文件密钥扫描。

从本轮开始按“一个可解释机制一个 commit”提交，例如配置接入、文档、`edit_file`、Verified Finish 和评测框架分别提交。真实 `.env`、`tmp/`、`.claude/`、`.codex/`、视频和 zip 保持忽略；由使用者审阅后手动提交，本次实现不自动 stage、commit 或 push。
