# hako Web 前端 MVP 完成手册

> 日期：2026-08-28｜状态：前端阶段记录；后续 Spring Boot、真实/假 Worker 与 API 联调已完成，最终状态见 [`WEB_BACKEND_COMPLETION.md`](./WEB_BACKEND_COMPLETION.md)。

## 1. 本轮交付

本轮在不改动 Python Agent 内核的前提下，新建 `web/frontend/`，完成一个单任务工程控制台。页面主线固定为“提交任务 → 查看调查与工具事件 → 审批副作用 → 查看 Verified Finish 证据”，没有做聊天气泡、在线 IDE、多任务列表或模型配置页。默认 `mock` 模式使用与正式接口相同的数据结构稳定演示全流程，并醒目标注“不会读取工作区、不会调用模型”；`api` 模式已经实现 REST/SSE 客户端，但需要后续 Spring Boot 服务才能真实执行。

已实现能力：

- 任务表单：工作区、工程任务、最大回合；运行期间锁定输入，终态后可再次启动。
- 实时事件：按 `eventId` 去重，展示 Agent 说明、工具开始/结果、上下文、验证要求、结束原因和基础设施错误。
- 权限控制：显示工具、关键参数、风险等级，支持单次允许、同类会话允许和拒绝；取消任务有二次确认。
- 完成证据：区分 Agent 自述和可执行验证，展示停止原因、改动文件、最终版本测试命令与结果。
- 接口适配：覆盖任务创建/查询/取消/审批/摘要、SSE 订阅与断线状态；接口字段与 `WEB_CONSOLE_API.md` 对齐。
- 界面质量：桌面端三栏聚焦任务、过程、证据；窄屏顺序降级；键盘焦点、语义标签、错误提示和减少动画偏好已处理。

## 2. 目录与职责

```text
web/frontend/
├─ src/App.vue                         页面编排与三栏工作区
├─ src/components/TaskComposer.vue     任务提交
├─ src/components/RunTimeline.vue      事件时间线容器
├─ src/components/EventItem.vue        类型化事件展示
├─ src/components/ApprovalPanel.vue    副作用审批
├─ src/components/EvidenceSummary.vue  Verified Finish 摘要
├─ src/composables/useTaskController.ts 任务状态与事件协调
├─ src/services/apiGateway.ts          正式 REST/SSE 客户端
├─ src/services/mockGateway.ts         确定性本地演示
├─ src/types/api.ts                    API/SSE 类型
└─ src/styles/base.css                 视觉、响应式与可访问性样式
```

`ApiGateway` 与 `MockGateway` 都实现同一个 `HakoGateway` 接口，因此组件不关心事件来自演示数据还是真实后端。正式接入时不需要重写页面，只需让 Spring Boot 实现既定接口。

## 3. 本地运行

需要 Node.js 20.19+ 或 22.12+。Windows PowerShell：

```powershell
cd web\frontend
npm install
Copy-Item .env.example .env.local
npm run dev
```

打开终端给出的本机地址。默认配置如下，不含任何密钥：

```dotenv
VITE_HAKO_MODE=mock
VITE_HAKO_PROXY_TARGET=http://127.0.0.1:8080
```

演示时点击“开始任务”，依次批准 `edit_file` 和 `pytest -q`，即可看到确定性的“调查 → 修改 → 修改后验证 → Verified Finish”闭环。Mock 只更新浏览器内存，刷新页面即清空，不会触碰示例路径。

连接 Spring Boot 时把 `.env.local` 改为 `VITE_HAKO_MODE=api`，后端默认监听 `127.0.0.1:8080`，Vite 会把同源 `/api` 代理过去。浏览器不接收、保存或显示模型 API Key；密钥仍只能由 Python Worker 的运行环境读取。

## 4. 前端使用的接口

| 用途 | 方法与路径 |
|---|---|
| 健康检查 | `GET /api/v1/health` |
| 创建任务 | `POST /api/v1/tasks` |
| 查询任务 | `GET /api/v1/tasks/{taskId}` |
| 实时事件 | `GET /api/v1/tasks/{taskId}/events`（SSE） |
| 处理审批 | `POST /api/v1/tasks/{taskId}/approvals/{approvalId}` |
| 取消任务 | `POST /api/v1/tasks/{taskId}/cancel` |
| 完成摘要 | `GET /api/v1/tasks/{taskId}/summary` |

完整字段、状态机、错误体和 Worker JSONL 协议见 [WEB_CONSOLE_API.md](./WEB_CONSOLE_API.md)。前端不会从文本猜测工具结果；只消费类型化事件与结构化摘要。

## 5. 设计取舍

界面采用明亮、克制的工程工作台，而不是“AI 聊天页”：左侧只负责输入，中央保持连续证据链，右侧把审批和最终证据放在决策位置。只使用一个蓝色交互色，成功、警告、失败仅承担状态含义；无渐变、玻璃效果、营销插画和无意义动画。这个结构直接突出 hako 的三个重点：修改受控、过程可追溯、完成有证据。

当前刻意不做后端伪实现、文件浏览器、Diff 编辑器、Git 操作、账号系统、多任务并发和 API Key 页面。它们会扩大工程面，却不能增强当前答辩主线。

## 6. 验收记录

- `npm run build`：TypeScript 类型检查与 Vite 生产构建通过。
- 视口适配复测：`1366×650`、`1024×768`、`1440×900` 下 `html/body/#app` 均与视口等高且无页面级滚动；左侧任务卡和中央运行面板固定占满工作区，空闲态左表单没有溢出。
- 运行态复测：低高度审批场景中页面仍不滚动；事件时间线与超过可用高度的证据栏分别内部滚动。`800×700` 以下切换为单列块级流，只让主工作区滚动，避免三栏挤压或重叠。
- 桌面浏览器：完整跑通两次批准、局部修改事件、测试事件、`done_verified` 和结构化摘要；另行验证拒绝编辑后以 `denied` 结束且不伪造验证证据；控制台无 error/warn。
- 390 × 844 窄屏：无横向溢出，任务、时间线、审批和证据仍可按顺序访问。
- 交互验收发现并修复：Mock 曾先发送 `task_result` 再保存摘要，前端随即请求摘要会误报不可用；现改为先固化终态结果，再发送终态事件。拒绝与取消路径使用同一顺序。
- 工具链兼容修复：TypeScript 7 与当前 `vue-tsc` 的内部入口不兼容，最终固定为 TypeScript 5.9.3；该版本组合已构建通过。

本轮未重跑 Python 测试，因为没有修改 Agent、工具或 Python 测试代码；最近一次内核全量记录仍是 `166 passed, 1 skipped`，不能把前端构建结果冒充内核回归。

## 7. 已知边界与下一步

本文件保留前端阶段的原始验收口径。后续阶段已实现 Spring Boot 状态机、确定性假 Worker、真实 Python Worker 和 REST/SSE 联调；真实大模型 Web smoke 仍应在隔离仓库手动执行，不能用假 Worker 结果替代。

## 8. 回退方式

本轮没有执行 Git stage、commit 或 push。若要只撤销 Web 前端实现，先确认文件没有承载后续修改，再移除 `web/frontend/`、`PRODUCT.md` 和本手册；从 `.gitignore` 移除 Node/Web 六行；把需求与 API 文档顶部状态恢复为“设计基线/尚未实现”，并移除 README 中新增的 Web 描述。不要使用 `git reset --hard`，也不要覆盖真实 `.env`。
