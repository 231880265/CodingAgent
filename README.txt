
一、Git 仓库
https://github.com/231880265/CodingAgent.git

二、项目简介
Hako 面向本地代码仓库，由大语言模型规划行动，自主完成搜索、读写、命令执行与结果观察，形成“决策—执行—反馈”闭环。项目不依赖 Agent 框架；模型负责决策，自研 Harness 负责 Tool Calling、参数校验、本地执行、权限、上下文、错误恢复与终止。

三、运行方式
环境：Windows 10/11，Python 3.12，Java 21，Node.js 20.19+ 或 22.12+。前端采用 Vue 3，后端采用 Spring Boot 与内嵌 SQLite，无需单独启动数据库。

1. 克隆仓库并按 README.md 安装依赖。
2. 复制 .env.example 为 .env，配置 HAKO_API_KEY、HAKO_BASE_URL 和 HAKO_MODEL，勿提交真实密钥。
3. 执行：
   .\start-web.ps1 -AllowedRoot "D:\path\to\allowed-root"

脚本统一预检并启动前后端；创建 Session 时由后端管理 Python Worker。在 Web 页面选择允许根目录内的 Workspace 后即可提交任务。

四、特色功能

1. 自研 Harness：实现 Agent Loop、Tool Calling、本地工具执行、权限边界、错误恢复与终止控制；edit_file 采用 exact + unique Search/Replace，无法唯一定位时拒绝写入并重新决策。
2. Verified Finish：模型声明完成不等于任务完成；最后一次代码修改后必须存在新的成功测试、构建或静态检查证据，Harness 才允许进入 DONE_VERIFIED。
3. Repository Experience Memory：从真实 Run 事件沉淀目标、修改、失败与验证等工程经验，按相关度、工程重要度和时间新鲜度召回；历史经验只辅助决策，当前代码始终以 Workspace 为准。
4. Session / Run 生命周期管理：同一 Session 可连续执行多个 Run，共享 Conversation 与 Workspace；Run 状态、关键事件和历史独立持久化，支持恢复、取消与审计。
5. Context Compaction：长 Run 接近上下文阈值时压缩较早执行轨迹，保留原始 Goal、用户约束和最近消息，避免上下文无限增长，同时完整历史仍保留。