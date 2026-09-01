# hako

`hako` 是一个面向本地代码仓库的通用 Coding Agent。它可以完成 Bug 修复、小型功能开发、局部重构和补充测试，也能在空目录中创建基础程序；项目重点不是堆叠功能，而是让多轮代码修改具备**状态一致性、修改可控性和完成可信度**。

模型不会直接接触文件系统。hako 把当前 Conversation 与手写工具 Schema 发送给兼容 OpenAI Tool Calling 的模型，解析模型返回的文本或 `tool_calls`，经过参数校验、路径边界和人工审批后，才在本地读取/修改文件或执行命令。工具结果继续写回 Conversation，直到主循环依据真实执行证据结束。

```text
用户任务
   ↓
Conversation + tool schemas ──> LLM
   ↑                              │
   │                       text / tool_calls
   │                              ↓
ToolResult <── 本地工具 <── 解析、校验、审批
   │
   ├─ 更新 Conversation，继续决策
   └─ 事件总线 → CLI / Web / 持久化历史

最后一次业务修改
   ↓
新的测试 / 构建 / 静态检查成功
   ↓
DONE_VERIFIED
```

## 为什么这样设计

一个 Coding Agent 真正困难的地方，不是让模型输出代码，而是管理一个会变化、会产生副作用、也会失败的工程环境。hako 围绕这条主线实现了四组机制：

1. **上下文一致性**：文件写入后，Conversation 中该文件更早的读取结果立即失效；长工具输出按预算截断并保留继续读取指针。跨 Run 只自动携带最近三组语义对和有界事件事实，旧细节按需检索。
2. **受控精准修改**：`edit_file` 只执行唯一匹配的 search-replace；0 匹配返回近似候选，多匹配拒绝猜测。路径必须位于 Workspace 内，普通副作用需要批准，高风险 shell 命令必须逐次确认。
3. **可信完成（Verified Finish）**：模型说“完成”不等于任务完成。发生业务文件修改后，必须在最后一次修改之后留下退出码为 0 的受认可测试、构建或检查证据，否则返回 `DONE_UNVERIFIED`。
4. **可追溯产品层**：事件总线将模型公开说明、工具调用、审批、文件副作用、验证和终止状态交给 CLI 或 Web。Web 以 `Workspace → Session → Run` 管理多轮工程对话，同一 Session 的后续 Run 复用语义 Conversation。

## 已实现能力

- 五个核心工具：`list_dir`、`read_file`、`edit_file`、`write_file`、`run_command`
- OpenAI 兼容模型接口；配置内置硅基流动、DeepSeek、阿里云百炼和智谱选项
- Tool Call JSON 有限修补、参数校验、未知/多余参数处理和可恢复错误回传
- 工作区路径约束、交互命令拦截、超时/取消与子进程树回收
- shell 执行前后净文件副作用审计，区分作者文件与 `.exe/.class/.jar` 等派生产物
- 分层终止：只读完成、验证完成、未验证修改、截断未完成、拒绝、取消、错误、重复调用和步数上限
- 模型回复截断续跑；有限重试后明确返回 `INCOMPLETE`
- 可选只读 subagent：隔离短上下文，只能列目录和读取文件，不能写入、执行命令或替代主 Agent 验证
- inline CLI，兼容 Windows / Linux
- Vue 3 + Vant 4 前端、Spring Boot REST/SSE 控制面、Python JSONL Worker
- 一 Session 多 Run、仅取消当前 Run、文本附件、SQLite 历史、SUSPENDED 会话语义恢复
- 三层记忆：当前 Conversation、事件确定性生成的 RunMemory、作为当前事实来源的 Workspace；`search_session_history` 可按需查找旧目标、修改、审批、失败和验证
- 根目录 `AGENTS.md` 项目指令：有界读取并注入 system prompt 之后、用户目标之前；缺失时无影响，且不能覆盖安全、路径与审批规则

核心 Agent 运行时只直接依赖 `openai` 与 `rich`。对话历史、工具定义与本地执行、模型输出解析、循环与终止条件、错误处理、权限和事件总线均在本仓库自行实现；没有使用 LangChain、LlamaIndex、OpenAI Agents SDK、Claude Agent SDK 等 Agent 框架，也没有把文件或命令执行托管给外部 API。

## 真实工程场景

本地脱敏的 PromoOps 电商营销后台用于验证同一个 Session 中连续处理“一个线上 Bug + 一次产品迭代”：

```text
Run 1：发布 v2 显示成功，但刷新后线上仍读取 v1
  → 调查 Service / Repository / UoW
  → 定位 published_revision_id 未持久化
  → 唯一匹配局部修复
  → 完整 pytest 通过

Run 2：在活动详情页增加 Priority 自助编辑
  → 在 Run 1 修改后的仓库继续开发
  → 非负校验、持久化、页面和回归测试共同演进
  → 公开测试与外部 held-out 验收
```

Run 1 的一次真实 Web 轨迹包含 14 次模型决策、27 次工具调用和 4 次审批，只修改 `campaign_repository.py`，最终以 `27 passed` 和 `DONE_VERIFIED` 结束。这个样例证明工具协议、局部编辑、失败恢复和完成判定可以端到端闭合；它不代表任意任务成功率。演示仓库、隐藏测试与运行现场保留在本地且不进入公开仓库，脱敏过程记录见 [PromoOps 彩排文档](docs/PROMOOPS_REHEARSAL_20260829.md)。

## 项目结构

```text
main.py                         CLI 入口与依赖装配
hako/
  config.py                     环境变量、模型与运行参数
  loop.py                       Agent 主循环和分层终止条件
  llm.py                        模型请求、重试、Tool Call 解析与 JSON 修补
  history.py                    Conversation 与陈旧读取失效
  project_instructions.py       根目录 AGENTS.md 有界读取与优先级包装
  prompt.py                     系统行为约束与运行环境
  events.py                     内核与呈现层之间的事件总线
  fs_audit.py                   shell 工作区快照与净副作用比较
  truncate.py                   工具结果预算、截断和继续读取指针
  subagent.py                   可选只读调查 Agent
  tools/
    base.py                     Tool / ToolResult 契约与路径边界
    files.py                    list / read / edit / write
    shell.py                    命令执行、验证分类、超时与高风险门禁
  ui/                           inline TUI 与审批
tests/                          Python 单元及集成测试
web/
  worker/                       真实 / Fake JSONL Worker
  backend/                      Spring Boot Session/Run、SSE 与历史持久化
  frontend/                     Vue 3 对话界面、审批与证据呈现
docs/                           设计、接口、联调和演示记录
```

更完整的设计决策、被否决方案、代价和验证见 [DESIGN.md](DESIGN.md)；实现时间线和证据口径见 [PROJECT_STATUS.md](PROJECT_STATUS.md)。

## 快速开始

### 1. 环境

- Python 3.12
- 使用 Web 控制台时另需 Java 21
- Vite 8 要求 Node.js 20.19+ 或 22.12+

Windows PowerShell：

```powershell
git clone <公开仓库地址>
Set-Location CodingAgent

python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

在本地 `.env` 中填写模型配置。推荐使用统一变量，切换 OpenAI 或提供 OpenAI-compatible 接口的 GPT/Claude 网关时只需替换这三项；原有硅基流动等提供商专用 Key 仍兼容。`.env`、`.hako/`、`.claude/`、`.codex/`、`tmp/`、视频和压缩包均已忽略，不应提交：

```dotenv
HAKO_API_KEY=
HAKO_BASE_URL=https://your-openai-compatible-endpoint/v1
HAKO_MODEL=your-model-id
```

公开配置模板见 [.env.example](.env.example)。不要把真实密钥写进 README、源码、测试或提交历史。

### 2. CLI

执行单个任务：

```powershell
.\.venv\Scripts\python.exe main.py -C 'D:\path\to\repo' '修复失败测试并重新运行 pytest'
```

不提供任务会进入交互模式；同一进程中的多次输入复用 Agent 与 Conversation：

```powershell
.\.venv\Scripts\python.exe main.py -C 'D:\path\to\repo'
```

常用参数：

| 参数 | 作用 |
|---|---|
| `-C, --workspace` | 指定 Agent 实际操作的工作目录 |
| `-v, --verbose` | 展开工具结果和上下文统计 |
| `-y, --yes` | 自动批准普通写入和命令；高风险命令仍需逐次确认 |
| `--max-steps` | 覆盖单个 Run 的模型决策安全预算 |

### 3. Web 控制台

一条命令同时启动 Spring Boot 后端和 Vue 前端。`-AllowedRoot` 是浏览器允许选择 Workspace 的本地根目录，不是默认操作仓库：

```powershell
.\start-web.ps1 -AllowedRoot 'D:\path\to\allowed-root'
```

脚本会检查 Python、Java、Node、端口和前端依赖，先等待后端健康，再启动并确认前端可访问。成功后打开 `http://127.0.0.1:5173`；按 `Ctrl+C` 会回收本次启动的前后端进程树。

其他模式：

```powershell
# 只做环境预检
.\start-web.ps1 -AllowedRoot 'D:\path\to\allowed-root' -CheckOnly

# 只验证 UI/协议，不调用模型、不修改工作区
.\start-web.ps1 -Mode Fake
```

Fake Worker 只用于确定性 UI 和协议测试，不能作为真实模型能力证据。Web 的 Session/Run、取消、迟到事件隔离和恢复语义见 [架构说明](docs/WEB_SESSION_RUN_ARCHITECTURE.md) 与 [恢复完成记录](docs/WEB_SESSION_RESUME_COMPLETION.md)。

## 完成判定

| 结果 | 含义 |
|---|---|
| `DONE_READ_ONLY` | 没有作者文件修改，模型正常结束；前端再根据工具事实区分普通问答与仓库分析 |
| `DONE_VERIFIED` | 有作者文件修改，且最后一次修改后存在成功的测试、构建或静态检查 |
| `DONE_UNVERIFIED` | 已修改文件，但没有可接受的修改后验证证据 |
| `INCOMPLETE` | 模型输出被截断且有限续跑仍未完成 |
| `CANCELLED / DENIED / ERROR / STUCK / MAX_STEPS` | 分别表示取消、拒绝、不可恢复错误、无进展重复或达到安全预算 |

验证命令必须是单一、可审计的执行，例如 `python -m pytest -q`、`npm test`、`mvn test`、`cargo test`、`g++ main.cpp -o main.exe`。包含管道、命令拼接、`|| true` 或仅收集测试的命令不会成为完成证据。任何后续业务文件写入都会让此前验证过期。

## 测试与 CI

Python 核心：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

Spring Boot：

```powershell
Set-Location web\backend
.\mvnw.cmd test
```

前端：

```powershell
Set-Location web\frontend
npm install
npm test
npm run build
```

2026-09-01 在当前工作树复测：

| 范围 | 结果 |
|---|---|
| Python | `247 passed, 1 skipped` |
| Spring Boot | `25 passed` |
| Vue/Vitest | `40 passed` |
| 前端生产构建 | TypeScript 检查通过，`324 modules transformed` |

GitHub Actions 在 Windows 与 Ubuntu、Python 3.12 上运行 Python 核心测试。真实模型测试不进入 CI，避免提交密钥、产生外部费用并混入网络服务波动。

## 明确边界

- Verified Finish 只证明最后一次业务修改后某项受认可验证成功，不证明测试覆盖充分，更不是形式化正确性证明。
- `run_command` 固定 Workspace、执行风险门禁并审计命令结束时的净变化，但不是操作系统沙箱；工作区外副作用和命令内部已经抵消的瞬时变化不可见。
- 高风险识别是保守词法门禁，不是 PowerShell/sh 完整语法分析；生产级隔离仍应使用容器、虚拟机或独立系统沙箱。
- 历史恢复只重建最近三组成对的用户输入与最终回答，并注入有界 RunMemory；不恢复旧 Worker、模型隐藏状态或过期工具观察。历史代码细节必须通过工具按需查找并重新读取当前仓库。
- 当前工具串行执行；本地基线中 list/read 耗时不足总墙钟 0.1%，没有把未证明有收益的工具并发包装成卖点。
- 已实现 Run 边界的确定性裁剪，避免多轮工具日志无限累积；尚未实现运行中根据 token 阈值自动摘要的通用 compaction，因为当前场景没有触发该需求。
- 可选只读 subagent 的权限隔离和直接调用已测试，但真实标本中尚无稳定的模型自主采用收益，因此不列为核心卖点。

## 相关文档

- [DESIGN.md](DESIGN.md)：核心设计决策、备选方案、代价与验证
- [PROJECT_STATUS.md](PROJECT_STATUS.md)：开发时间线、真实结果和当前边界
- [PRODUCT.md](PRODUCT.md)：产品定位与核心场景
- [Web API](docs/WEB_CONSOLE_API.md)：REST/SSE 协议
- [Web 需求](docs/WEB_CONSOLE_REQUIREMENTS.md)：信息架构与产品语义
- [PromoOps 彩排](docs/PROMOOPS_REHEARSAL_20260829.md)：真实连续工程任务和外部验收记录
