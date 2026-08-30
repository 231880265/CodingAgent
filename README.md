# hako

`hako` 是一个从零实现的 Coding Agent，提供命令行入口与本地 Web 控制台。模型不会直接操作系统，而是通过主循环反复执行“模型决策 → 工具调用 → 结果回传”，直到给出最终答复或触发终止条件。当前重点不是堆功能，而是把工具协议、上下文、权限和失败恢复做成可解释、可测试的工程机制。

## 当前状态

已实现一条可真实运行的最小闭环：

- 五个工具：`list_dir`、`read_file`、`edit_file`、`write_file`、`run_command`
- OpenAI 兼容接口，支持硅基流动、DeepSeek、阿里云百炼和智谱
- 工具参数修补与校验、错误回传、重复调用检测、步数上限
- 模型回复截断续跑：保留 `finish_reason`，有限重试后明确 `INCOMPLETE`
- 文件工具的工作区路径约束、默认拒绝与危险命令逐次审批
- shell 命令前后净文件副作用审计：区分新增、修改和删除
- 陈旧读取失效、长工具输出截断与继续读取指针
- Verified Finish：区分只读完成、已验证完成和未验证修改
- 可选只读 subagent：独立短上下文，仅能列目录/读文件，主 Agent 保留唯一写入与验证权
- 事件总线和 inline TUI，兼容 Windows / Linux
- 本地 Web 控制台：Vue 3 + Vant 4 前端、Spring Boot REST/SSE 控制面；每个 Session 独占一个 Python Worker，同一 Session 可连续执行多个 Run 并共享 Agent + Conversation；支持文本附件、分级审批、仅取消当前 Run、独立新会话、SQLite 只读历史和 Verified Finish 证据摘要

2026-08-27 已用硅基流动 `deepseek-ai/DeepSeek-V4-Flash` 在隔离的临时 Git 仓库完成最新版真实多轮验证：

```text
list_dir → read_file × 2 → edit_file → pytest -q (exit=0) → DONE_VERIFIED
```

初始测试为 `2 failed, 1 passed`，Agent 只把折扣公式中的 `1 + percent / 100` 改为 `1 - percent / 100`，测试文件没有变化；内核根据修改后的 `pytest` 成功证据输出 `DONE_VERIFIED`，独立复测为 `3 passed`。本次运行共 5 次模型决策、9,662 tokens、约 51.3 秒。它证明 `edit_file`、真实 tool calling 和 Verified Finish 能端到端闭合；单个样例不能代表任务成功率，后续仍需评测集和消融实验。

完整设计辩护见 [DESIGN.md](DESIGN.md)，时间线与待办见 [PROJECT_STATUS.md](PROJECT_STATUS.md)。Web 的 P0/P1 Session/Run 架构见 [docs/WEB_SESSION_RUN_ARCHITECTURE.md](docs/WEB_SESSION_RUN_ARCHITECTURE.md)，需求、接口和联调记录见 [docs/WEB_CONSOLE_REQUIREMENTS.md](docs/WEB_CONSOLE_REQUIREMENTS.md)、[docs/WEB_CONSOLE_API.md](docs/WEB_CONSOLE_API.md) 与 [docs/WEB_BACKEND_COMPLETION.md](docs/WEB_BACKEND_COMPLETION.md)。

## 快速开始

项目 CI 使用 Python 3.12。Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

编辑本地 `.env`，选择一家服务商填写 API Key。`.env` 已被 `.gitignore` 排除，禁止提交到仓库。以硅基流动为例：

```dotenv
SILICONFLOW_API_KEY=
```

只在本地把密钥填到等号右侧。默认会使用 `https://api.siliconflow.cn/v1` 和 `deepseek-ai/DeepSeek-V4-Flash`；可通过 `HAKO_MODEL`、`HAKO_BASE_URL` 覆盖。硅基流动默认关闭长思考以降低工具交互延迟，可用 `HAKO_ENABLE_THINKING=true` 显式开启。公开且不含密钥的全部配置项见 [.env.example](.env.example)。

复杂的跨日志、跨文件根因调查可在本地 `.env` 加 `HAKO_ENABLE_SUBAGENT=true` 开启一次/任务的只读委派；它默认关闭，子 Agent 不能写文件或运行命令，返回内容也不算验证证据。

执行一次任务：

```powershell
.\.venv\Scripts\python.exe main.py -C D:\path\to\repo "修复失败测试并重新运行 pytest"
```

不传任务会进入多轮交互模式：

```powershell
.\.venv\Scripts\python.exe main.py -C D:\path\to\repo
```

### 本地 Web 控制台

需要 Java 21、Node.js 和仓库内 Python 虚拟环境。在仓库根目录执行一条命令即可同时启动前后端；默认使用真实 Agent，`-AllowedRoot` 决定页面允许选择的仓库范围：

```powershell
.\start-web.ps1 -AllowedRoot 'D:\path\to\allowed-root'
```

只验证界面和协议、不调用模型也不修改文件时，使用 Fake Worker：

```powershell
.\start-web.ps1 -Mode Fake
```

脚本会检查 Java、Node.js 和 Python 虚拟环境，首次运行自动执行 `npm install`。它先等待后端健康状态达到 `UP`，再启动并确认前端可访问，避免浏览器在 Spring Boot 尚未就绪时得到 HTTP 502；端口已占用、服务提前退出或就绪超时都会给出明确错误并回收本次启动的进程树。看到两个 `ready` 后打开 `http://127.0.0.1:5173`；按 `Ctrl+C` 会同时回收前后端进程。只检查环境而不启动可执行 `.\start-web.ps1 -CheckOnly`。

需要分别观察或调试两个服务时，也可以手动启动。第一个 PowerShell 启动后端：

```powershell
Set-Location web\backend
$env:HAKO_REPOSITORY_ROOT=(Resolve-Path ..\..).Path
$env:HAKO_WEB_ALLOWED_ROOTS='D:\path\to\allowed-root'
$env:HAKO_PYTHON_EXECUTABLE=(Resolve-Path ..\..\.venv\Scripts\python.exe).Path
.\mvnw.cmd spring-boot:run
```

第二个 PowerShell 启动前端 API 模式：

```powershell
Set-Location web\frontend
npm install
$env:VITE_HAKO_MODE='api'
npm run dev
```

打开终端输出中的本机地址。后端默认使用真实 `web/worker/main.py`；只想稳定演示协议且不调用模型时，在启动后端前设置 `$env:HAKO_WORKER_ENTRYPOINT='web/worker/fake_worker.py'`。Fake Worker 不读取或修改工作区，不得把它的结果描述成真实 Agent 成功率。

常用参数：

| 参数 | 作用 |
|---|---|
| `-C, --workspace` | 指定工作区；文件工具只允许访问该目录内部 |
| `-v, --verbose` | 展开工具结果与上下文统计 |
| `-y, --yes` | 自动批准普通写入和命令；高风险命令仍需逐次确认 |
| `--max-steps` | 覆盖默认 40 次模型决策上限 |

## 运行机制

```text
用户任务
  ↓
Agent 主循环 ──请求──> LLM
  ↑                    │
  │                    └─文本 / tool_calls
  │                               ↓
对话历史 <──工具结果── 注册表校验 ──> 文件或命令工具
  │
事件总线 ──> TUI / 后续评测订阅者
```

关键代码：

```text
main.py                 CLI 入口与依赖装配
hako/
  config.py             .env、服务商与运行参数
  fs_audit.py           shell 工作区快照与净副作用比较
  loop.py               主循环和分层终止条件
  llm.py                请求、重试、tool call 解析与 JSON 修补
  history.py            对话历史与陈旧读取失效
  truncate.py           工具结果截断和恢复指针
  events.py             内核与呈现层之间的事件总线
  prompt.py             系统提示词
  subagent.py           隔离的只读调查工具与成本回传
  tools/
    base.py             工具契约、参数 schema、路径边界
    files.py            list_dir / read_file / edit_file / write_file
    shell.py            run_command、超时和交互命令拦截
  ui/                    inline 渲染、审批和跨平台读键
tests/                   单元、集成和 CLI 测试
web/
  frontend/              Vue 3 + Vant 4 控制台与 Mock/API Gateway
  backend/               Spring Boot REST/SSE、状态机与 Worker 进程管理
  worker/                真实/确定性假 Python JSONL Worker
```

几个边界需要明确：

- `edit_file` 只执行唯一匹配的局部替换；0 匹配返回候选，多匹配拒绝猜测，`write_file` 仍用于新文件或小文件整体重写。
- 文件工具会校验路径不能逃出工作区；`run_command` 只固定工作目录并观察命令前后净变化，不是操作系统沙箱。
- Windows 上 bare `pytest` 由工具自动绑定到启动 hako 的当前 Python；验证注册表同时覆盖常见 Python/Node/Rust/Go/.NET/Java 测试，以及 C/C++、Java 和主流构建工具。
- 库调用默认拒绝有副作用工具；普通写入/命令可逐次批准或由 `-y` 放行，高风险命令永远逐次确认，非交互环境即使有 `-y` 也拒绝高风险命令。
- 修改后必须在最后一次业务文件写入之后运行受认可且退出码为 0 的单一测试、构建或检查命令；否则只能得到 `DONE_UNVERIFIED`，CLI 返回失败。`.exe/.class/.jar` 与常见构建目录会作为派生产物展示和审计，但不会反向清空刚成功的构建证据。
- 当前只做陈旧读取失效与单条工具输出截断，尚未实现长对话 compaction。
- shell 审计只能观察命令结束时的净变化：同一命令内创建后删除、工作区外副作用及并发进程归因不在证明范围；`.git/.venv/node_modules/tmp` 与常见测试缓存会剪枝。
- 高风险识别是保守的词法门禁，不是完整 shell 语法分析；同一次模型决策返回的多个工具调用目前串行执行。
- 本地三场景评测的最大主上下文仅为 0.6241%，list/read 累计执行占墙钟不足 0.1%，因此当前没有为演示而实现 compaction 或工具并发。
- 只读 subagent 的真实模型机制探针能在 4 步内返回跨文件证据且零写入，但两次自然 EAGLE 运行都未调用它，所以尚无自主采用或性能收益结论。

## 测试与 CI

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
```

测试覆盖配置选择、路径逃逸、参数修补、上下文失效、截断、终止条件、事件流、CLI 和跨平台行为。GitHub Actions 在 Windows 与 Ubuntu 上使用 Python 3.12 运行同一套测试。真实模型 smoke test 不放进 CI，避免泄露密钥、产生费用和引入外部服务波动。

2026-08-29 最新回归：Python `191 passed, 1 skipped`；Spring Boot `15 passed`；前端 `npm run build` 通过 TypeScript 检查并转换 303 个模块。浏览器另在 `1280×720` 与 `1024×700` 验证页面无外层滚动，并走通同 Session 三个 Run、取消本轮后继续、活动 Run 切换新会话、附件/工作区语义分离及 CLOSED 历史只读复盘。Fake Worker 和本地故障标本不代表真实模型成功率。

## 下一阶段

`DESIGN.md` 第一版已经完成，十二项决策均记录问题、选择、否决方案、代价、验证与踩坑。下一步冻结接口与数据口径，审校最终代码/commit 锚点，再准备两分钟演示视频和不超过 1000 汉字的 `README.txt`；不再为了展示继续堆内核功能。当前完成项、证据和逐日安排统一维护在 [PROJECT_STATUS.md](PROJECT_STATUS.md)。
