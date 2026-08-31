# PromoOps 两轮工程任务彩排与独立验收

> 最终口径更新于 2026-08-30。演示由 Run1 线上发布 Bug 与 Run2 Priority/Conflict 产品迭代组成。

## 1. 目的与边界

本演示验证同一个 `Session + Agent + Conversation` 能否在一个持续变化的真实仓库中连续完成“一个真实线上 Bug + 一次真实产品迭代”，并让每轮作者修改都经过 Verified Finish。演示仓库、只读模板、reset runner 和 held-out tests 位于相邻的本地 `../promoops-demo`，不进入 CodingAgent Git 仓库。

PromoOps 是脱敏的小型电商营销运营后台，不复制生产源码或公司标识。Issue 只描述运营可观察症状与期望结果，不提供源码位置、测试命令或修复答案。Agent 可以读取公开项目测试，但看不到外部 held-out tests。

## 2. 可回退工作区

标准模板保持只读，Agent 只操作 `work/`。重新测试前停止仍在运行的 PromoOps 服务，再执行：

```powershell
Set-Location 'D:\学校事务\预推免\项目vibecoding\promoops-demo'
.\.venv\Scripts\python.exe -B .\reset_demo.py run1
.\start-demo.ps1
```

Reset 会用 `templates/run1_initial` 重新生成 `work/`，不会删除 hako Web 历史，也不会改变标准模板。若要保留一次运行后的代码，应先复制 `work/`；Cancel 只停止 Agent 后续行为，不回滚已经落盘的修改。

启动真实 hako Web：

```powershell
Set-Location 'D:\学校事务\预推免\项目vibecoding\coding-agent'
.\start-web.ps1 -AllowedRoot 'D:\学校事务\预推免\项目vibecoding\promoops-demo\work'
```

## 3. Run1：发布成功后线上仍读旧版本

### 运营反馈

“618 数码满减”线上 v1 是满 150 元减 20 元；运营保存 v2 草稿，规则是满 300 元减 50 元。320 元订单在草稿预览中应付 270 元。点击“发布 v2”后页面提示成功，但刷新仍显示线上 v1，实际应付仍是 300 元。

期望：发布成功后线上立即切换到刚发布草稿；未发布草稿不能提前影响线上；发布失败时原线上版本保持可用；以后线上始终采用最后一次成功发布的版本。

### 根因与交付

发布服务已经在内存对象中设置 `campaign.published_revision_id = draft_id`，接口因此能返回成功；但 `CampaignRepository.save()` 漏掉了该字段的数据库持久化。下一请求重新从数据库读取后，线上指针仍是 v1。修复只需要在 Repository 保存路径补齐该字段，不改测试配置或工作区外文件。

最终模板证据：

- Run1 初态：`1 failed, 26 passed`；失败稳定复现发布后线上仍读取旧版本。
- 修复后 / Run2 起点：`27 passed`。
- 独立 Run1 held-out：`9 passed`。
- 一次保留的真实 Web 轨迹：14 次模型决策、27 次工具调用、4 次审批，只修改 `app/repositories/campaign_repository.py`，最终 `DONE_VERIFIED`。

该轨迹证明 Real Worker、模型 Tool Calling、本地编辑、失败恢复和 Verified Finish 能端到端闭合；单次样例不代表任意任务成功率。

## 4. Run2：活动 Priority 与发布冲突

### 运营反馈

同一商品范围可能同时命中多个活动，运营无法明确控制最终采用哪一个，也不能在发布前发现同优先级冲突，存在重复优惠或选错活动的风险。

期望：列表和详情可查看、修改非负 Priority；同范围多个有效活动只选择 Priority 最高者；保存后若形成同范围同 Priority 冲突，必须立即指出冲突活动和风险；发布冲突应拒绝且不能留下半完成状态；不同范围互不影响。

### 同一 Session 的产品迭代

Run2 直接在 Run1 修好的 `work/` 中继续，不复制标准答案模板，也不创建新的 Conversation。交付跨越：

```text
领域冲突规则
  → Campaign/Conflict Service
  → 发布前无副作用校验
  → 按最高 Priority 选择线上报价
  → Priority API
  → 页面保存后的即时冲突反馈
  → 持久化、冲突、跨范围、无部分提交和 UI 回归测试
```

关键验收之一是：周末闪促从 50 调到 100、与“618 数码满减”同范围同优先级时，保存后不能只弹“优先级已更新”，必须立即显示冲突对象；冲突发布后活动仍保持草稿状态。

最终模板证据：

- Run2 起点：`27 passed`。
- Run2 完成态：`36 passed`。
- 独立 Run2 held-out：`6 passed`。

## 5. 独立验收为什么必要

Agent 内部运行公开测试回答“它为什么认为自己修好了”；外部 held-out runner 回答“评测者是否应该相信它”。外部验收从 Agent 不可见的位置加载，检查公开行为契约和允许变更范围，不接受模型自己的总结作为证据。

当前最终口径：

| 阶段 | 公开测试 | held-out | 结论 |
|---|---:|---:|---|
| Run1 初态 | `1 failed, 26 passed` | 不适用 | Bug 可稳定复现 |
| Run1 修复 / Run2 起点 | `27 passed` | Run1 `9 passed` | 发布一致性独立通过 |
| Run2 完成态 | `36 passed` | Run2 `6 passed` | Priority/Conflict 独立通过 |

这些数字属于不同阶段和测试集，不相加成“总通过数”。Verified Finish 也只证明最后一次作者修改之后存在有效验证，不代表测试覆盖全部风险。

## 6. 演示与答辩口径

视频故事固定为：

```text
运营发现真实线上 Bug
  → 只提供症状和期望
  → hako 调查跨层调用链
  → 只做必要 Repository 修复
  → 最后修改后完整测试通过
  → 刷新后台，线上 v2 与草稿一致
  → 同一 Session 追加 Priority/Conflict 需求
  → 仓库、页面和测试继续演进
  → 独立 held-out 复核
```

答辩时不要把 Run2 说成第二个故意埋的 Bug，它是一次真实产品迭代。核心结论是：hako 既能修复已有工程缺陷，也能在同一持续会话中继续开发功能，并且每次完成都由本地结构化证据而不是模型自述决定。

## 7. 与 hako 内核问题的关系

PromoOps 彩排还帮助固定了四个通用机制：修改后重新读取不应被 STUCK 误伤；Windows 测试必须使用正确 Python 环境；`python -B -m pytest` 应被识别为安全验证；Web 应合并连续读取和修改，只把参数、stdout/stderr 放在折叠证据里。这些机制已经进入核心或前端回归测试，不再依赖保留旧演示数据才能成立。
