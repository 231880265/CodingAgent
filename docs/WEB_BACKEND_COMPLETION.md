# hako Web 后端与前后端联调完成手册

> 日期：2026-08-28｜状态：代码完成，确定性端到端验收通过；真实模型 Web smoke 待手动执行

## 1. 本轮交付

本轮把上一阶段的静态/Mock 控制台接成了可运行的本地产品链路：浏览器通过 REST 创建、查询、审批和取消任务，通过 SSE 接收类型化事件；Spring Boot 管理单活动任务、事件重放与 Python 子进程；真实 Worker 只把现有 `EventBus`、同步审批回调和 `RunResult` 转成 JSONL，没有复制 `Agent.run()`；确定性假 Worker 供测试和答辩预演使用，不调用模型、不读写工作区。

```text
Vue 3 + Vant 4
   │ REST + SSE
   ▼
Spring Boot 4.1.1
   │ stdin/stdout JSONL v1
   ▼
Python Worker ──复用──> hako Agent / tools / Verified Finish
```

## 2. 主要实现

- `web/backend/`：Java 21、Spring Boot 4.1.1；实现 7 个 `/api/v1` 接口、严格 JSON 校验、64 KiB HTTP 正文上限、本机 CORS、统一错误体和健康检查。
- `TaskService`：单任务状态机、Worker 连续序号校验、SSE `eventId`、2,000 条/10 MiB 有界重放、审批 ID 与风险决策校验、取消及进程树回收、终态摘要。
- `ProcessWorkerLauncher`：使用 argv 启动当前 Python，stdout 只接受 UTF-8 JSONL，单行最大 1 MiB且必须以 LF 结束；stderr 仅保留 256 KiB 脱敏尾部；只向子进程传递显式环境变量。
- `web/worker/main.py`：构造现有 `Config`、`LLMClient`、工具注册表与 `Agent`，订阅 `EventBus`，把 Web 审批同步返回原审批回调。
- `web/worker/fake_worker.py`：真实子进程协议替身，覆盖正常两次审批、拒绝、高风险、非法 JSON 和提前退出，不触碰文件或模型。
- 前端 API 模式：终态主动关闭 EventSource，避免浏览器反复重连；把 `taskId` 写入 URL 查询参数，刷新后先 GET Task，再重放事件并恢复未决审批；Worker 故障也会拉取结构化摘要。
- Maven Wrapper：固定 Maven 3.9.16，并在 properties 中校验官方分发包 SHA-256；项目无需预装 Maven。

依赖版本以项目锁定文件为准。Spring Boot 版本和 Java 要求可从 [Spring Boot 官方系统要求](https://docs.spring.io/spring-boot/system-requirements.html)核对；Wrapper 的用途与运行方式见 [Apache Maven Wrapper 官方文档](https://maven.apache.org/tools/wrapper/)。

## 3. 与需求/API 的最终对齐

- HTTP 审批返回 `202 + status=ACCEPTED + acceptedAt`，只表示决定已经写入 Worker；真正处理完成由后续 `approval_resolved` SSE 确认，避免 HTTP 提前声称工具已执行。
- `stream_gap` 是传输提示，使用 `eventId=0` 且不发送 SSE `id`，不会破坏任务业务事件的单调序号。
- `task_result`、`worker_error`、`task_cancelled` 前，后端已经固化 Summary；前端收到终态事件后再查询，不存在“先收到结束、摘要还没写好”的竞态。
- 成功只接受 `done_read_only` 与 `done_verified`。测试绿灯来自 Worker 返回的结构化 `RunResult`；Spring 不解析模型文字，也不自行判断测试输出。
- workspace 在 Java 侧用 `toRealPath()` 与允许根目录比较，Python 工具仍保留自己的路径边界；`run_command` 仍不是操作系统沙箱。

接口和事件字段的权威说明已同步到 [`WEB_CONSOLE_API.md`](./WEB_CONSOLE_API.md)，产品范围和验收口径已同步到 [`WEB_CONSOLE_REQUIREMENTS.md`](./WEB_CONSOLE_REQUIREMENTS.md)。

## 4. 验证结果

| 验证 | 结果 | 能证明什么 |
|---|---:|---|
| Python 全量 | `174 passed, 1 skipped` | 原 Agent/CLI 回归与新增 Worker 协议映射通过 |
| Spring Boot | `13 passed` | JSONL 行边界、真实 Python 子进程、两次审批、拒绝、高风险限制、取消、协议失败与 HTTP 错误契约通过 |
| 前端构建 | `npm run build` 通过，299 modules | TypeScript 类型与生产构建通过 |
| 浏览器 API 联调 | 通过 | `POST task → SSE → 刷新恢复 → edit 审批 → pytest 审批 → Verified Finish Summary` 全链路可见 |

浏览器联调使用 Fake Worker，因此只证明 Web 协议、状态机和交互正确，不证明真实模型会稳定解决 Router Issue。真实 Worker 代码已接入同一链路，但本轮没有消耗密钥重新调用大模型；提交前应在隔离小仓库手动执行一次真实 smoke。

## 5. 实施中发现并修正的问题

- Spring Boot 4 使用 Jackson 3，包名已迁到 `tools.jackson.*`；首次编译据此统一适配，没有退回旧框架版本。
- Worker stdout 原先允许 EOF 前残留半行，现改为必须以 LF 完成，避免把截断 JSON 当成有效帧。
- EventSource 在服务端关闭后会默认重连；前端现于权威终态事件主动关闭连接，终态显示“事件流已结束”。
- 刷新恢复后左侧表单曾显示默认 workspace，与恢复任务不一致；现由恢复后的 Task 回填并锁定表单。
- 系统 pytest 临时目录在受限环境中无权限；最终全量回归显式使用仓库内 `tmp/` 作为 `basetemp`。这属于测试环境修正，不是通过修改断言掩盖失败。

## 6. 本地运行

推荐在仓库根目录用一条命令同时启动前后端：

```powershell
.\start-web.ps1 -AllowedRoot 'D:\path\to\allowed-root'
```

默认使用真实 Worker；只验证 Web 链路时执行 `.\start-web.ps1 -Mode Fake`，只检查依赖时执行 `.\start-web.ps1 -CheckOnly`。脚本的 Fake 启动冒烟已验证后端健康接口和前端首页均返回 HTTP 200；这只证明启动与 Web 链路可用，不代表真实模型完成了任务。

真实 Worker（会读取本地 `.env`，工具副作用仍逐次审批）：

```powershell
Set-Location web\backend
$env:HAKO_REPOSITORY_ROOT=(Resolve-Path ..\..).Path
$env:HAKO_WEB_ALLOWED_ROOTS='D:\path\to\allowed-root'
$env:HAKO_PYTHON_EXECUTABLE=(Resolve-Path ..\..\.venv\Scripts\python.exe).Path
.\mvnw.cmd spring-boot:run
```

若只验证界面与协议，在上述命令前增加：

```powershell
$env:HAKO_WORKER_ENTRYPOINT='web/worker/fake_worker.py'
```

另开 PowerShell：

```powershell
Set-Location web\frontend
npm install
$env:VITE_HAKO_MODE='api'
npm run dev
```

默认后端仅监听 `127.0.0.1:8080`。不要把本版本直接暴露到公网；它没有登录、TLS、CSRF 防护和系统级命令沙箱。

## 7. 回退与剩余工作

实现前快照位于本地 ignored 的 `tmp/rollback/20260828-before-web-backend/`，共 9 个文件、75,276 字节，不含 `.env`。本轮没有 stage、commit 或 push。若只撤销本阶段，必须先比较后续用户修改，再按本地 `tmp/local-evals/ROLLBACK.md` 的文件清单恢复；不要使用 `git reset --hard`，不要删除整个 `web/`，不要覆盖真实 `.env`。

剩余一项阻塞提交前完整 Web MVP 证据：用真实 Worker 和隔离仓库跑一次 `read/list → edit → pytest → done_verified`，记录模型、任务、审批、修改范围和独立复测。数据库、多任务历史、远程执行、GitHub 联动、diff、baseline 对照均不属于本轮范围。
