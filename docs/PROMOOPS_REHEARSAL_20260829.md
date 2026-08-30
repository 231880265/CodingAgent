# PromoOps 连续工程任务彩排与外部评测记录（2026-08-29）

## 目的与边界

本轮验证两件事：修复 hako 的重复调用止损器后，仍能拦截真正无进展的循环；同一个 `Session + Worker + Agent + Conversation` 能否在一个持续变化的真实仓库中连续完成 Bug 修复、产品开发和审计回滚。演示仓库位于 `../promoops-demo/work`，模板、reset runner 和 held-out tests 位于 `../promoops-demo`，不进入 CodingAgent Git 仓库。整个过程未提交、未 push，也未删除旧失败历史。

## 1. 内核最小修复

问题不是 STUCK 阈值过严，而是重复签名计数跨越了有效修改阶段：`read A → edit A 成功 → reread A` 仍沿用修改前的 read 计数，可能把“修改后确认”误判成重复调查。修复只在工具结果包含真实 `touched_paths` 时清空重复调用计数；仅 `ok=True` 不算进展，因此失败编辑不能洗掉计数；`run_command` 即使退出失败，只要确实改变工作区文件，也会开启新阶段。

确定性回归：

- `read → read → read`：仍为 STUCK；
- `read → edit 成功 → reread`：不 STUCK；
- `read → edit 失败 → reread`：不能清零；
- `run_command` 真实改文件：视为 progress。

验证结果：聚焦 4 项 `4 passed`，`tests/test_loop.py` 为 `39 passed`，hako Python 全量为 `194 passed, 1 skipped`。Skipped 项单独保留，不计作通过。

## 2. 失败现场与可回退性

旧失败 Session `925e9e0c-da8f-4110-bbce-e5e74eab9c0f` 保持关闭且可从 Web 历史只读查看：Run1 完成；Run2 在已修正负 Priority 后、重新验证前达到 40 步上限。它证明旧问题不是简单的同签名 STUCK，同时保留了修复前后的真实对照。更早的失败 Session `683a197f-0144-4643-bf4a-cb569894f4d7` 也未删除。

彩排前由 `reset_demo.py run1` 从只读模板重建 `work/`，校验结果为 `DIFF_COUNT=0, FILES=44`；模板不含 `.git`、数据库和生成缓存。再次演示时可先停止正在运行的 PromoOps 服务，再执行：

```powershell
cd "D:\学校事务\预推免\项目vibecoding\promoops-demo"
.\.venv\Scripts\python.exe -B .\reset_demo.py run1
```

Reset 只恢复演示仓库，不会删除 hako Web 历史。若需要保留某次运行后的源码，应先复制 `work/`，因为 Reset 的语义是重新生成演示工作区。

## 3. 同一 Session 连续彩排

Session `ba47db46-259a-4750-97b9-726eedfd5abe` 始终复用 Worker `c90990e9-333e-48c7-b992-175aaedec157`。前三个 Run 是视频主线；后两个 Run 是 held-out 反馈后的 QA follow-up，不改变三轮产品故事。

| Run | Run ID | 结果 | 步数 | 交付 |
|---|---|---:|---:|---|
| Run1：发布成功但线上仍读旧版本 | `7047412e-7093-4918-96ec-ec0276b22678` | `done_verified` | 11 | 修复 Repository 漏持久化 `published_revision_id`；完整 pytest 26 passed |
| Run2：Priority 与发布冲突 | `500e0497-5c8d-49e4-aa4c-919e069461c8` | `done_verified` | 52 | Priority、范围定价、409 冲突与无副作用、UI 和回归测试；38 passed |
| Run3：发布审计与版本回滚 | `39558b02-f4ed-4597-af95-0f6e3c528648` | `done_verified` | 51 | 审计表、Repository/UoW、发布审计、原子回滚、API、真实 UI；53 passed |
| QA1：外部契约兼容 | `11d4c4d5-3265-4e47-baab-6aca9f1e13a6` | `done_verified` | 20 | PUT/PATCH 兼容、业务版本号、`restored_version`、页面文案；60 passed |
| QA2：剩余字段/信息层级 | `5ae36461-9456-49e0-9279-157a0172470b` | `done_verified` | 15 | `timestamp` 别名、单一“发布记录”面板；61 passed |

审批过程中还保留了两个真实受控执行例子：一次含字面量 `\\n`、会写坏 Python import 的模型编辑被拒绝后正确重试；一次为了匹配两个 UI 词语而复制整张审计表的提议被两次拒绝，最终改为单一“发布记录”面板加“操作审计”标签。说明审批不是装饰，而是阻止模型把“测试可过”当成“产品合理”。

## 4. 独立 held-out evaluation

外部 runner 每次先跑仓库公开测试，再从 `held_out/` 加载独立验收；Agent 没有读取或修改 held-out 源码，只收到失败症状和公开产品契约。

第一次外测：Run1 为 9/9；Run2 因 Priority HTTP method 合约差异为 2/6；Run3 因业务版本字段、rollback 响应和页面文案差异为 1/5。核心行为存在，但外部消费者无法按约定使用，不能记作通过。

QA follow-up 后最终复验：

| 评测 | 公开测试 | held-out | 结论 |
|---|---:|---:|---|
| Run1 | 61 passed | 9 passed | independently verified |
| Run2 | 61 passed | 6 passed | independently verified |
| Run3 | 61 passed | 5 passed | independently verified |

总计 20 项 held-out 全部通过。唯一剩余输出是 Starlette TestClient 对依赖迁移的 deprecation warning，不影响本次行为结果，但后续依赖升级时应处理。

## 5. 答辩口径

这不是“模型一次生成三项功能”的成功截图。可信故事是：hako 先在同一会话中连续完成一个线上 Bug 和两次产品迭代，每次修改后由 Verified Finish 要求新的可执行验证；随后独立验收仍找出 API 消费者契约差异，Agent 根据外部证据做兼容修复，最终公开测试与 held-out 同时通过。需要诚实说明前三个 Run 是产品主线，后两个是 QA 闭环；这比隐藏首次失败更能证明框架支持真实软件工程中的实现、反馈、修正与复验。

## 6. 2026-08-30 彩排缺陷收束

真实 Run1 中，DeepSeek 使用 `..\.venv\Scripts\python.exe -B -m pytest ...` 并得到测试通过，但验证注册表过去只识别解释器后直接跟 `-m` 的形式，最终被保守地终止为 `done_unverified`。最小修复只允许安全的 `-B` 出现在 `-m` 前，同时覆盖 pytest、unittest 与 compileall 既有边界；聚焦 Shell 测试 49 passed，hako Python 全量 196 passed、1 skipped。

同轮还收束了两项演示与呈现问题：PromoOps 未发布活动不再因空线上报价返回 500，本地服务默认监听 Agent 修改的 Python 源码；Web 将相邻的多个 `read_file` 合并成一个“已读取 N 个文件”步骤，展开后先看文件清单，再按文件查看参数和原始结果，非连续读取和其他工具仍保持原顺序。前端 24 项测试、TypeScript 检查和生产构建均通过。Issue 文案只保留运营反馈与期望效果，不再把测试命令、源码位置或修复方向透露给 Agent。

本次 `work/` 原现场已保存在 `../promoops-demo/work-run1-unverified-20260830-131000/`，随后从修订后的 Run1 模板重新生成；关键故障文件哈希一致，说明仍保留可复现 Bug，没有把 Agent 的答案写回初始模板。本轮未 commit、未 push。
