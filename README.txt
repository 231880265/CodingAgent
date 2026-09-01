hako 编程智能体

Git 仓库：____________________________（公开后填写）

简介：hako 是从零实现的本地通用 Coding Agent，可完成 Bug 修复、功能开发、重构和补充测试。模型不能直接访问系统；hako 把手写 Tool Schema 与 Conversation 发给兼容 OpenAI Tool Calling 的模型，校验 tool_calls 后受控执行，再将 ToolResult 写回对话。对话历史、工具、解析、循环终止和错误处理均自行实现，未使用 Agent 框架或外部文件执行 API。

运行环境：Python 3.12；Web 另需 Java 21、Node.js 20.19+ 或 22.12+。Windows PowerShell 执行：
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
在本地 .env 填写 API Key，再运行：
.\start-web.ps1 -AllowedRoot "D:\path\to\allowed-root"
访问 http://127.0.0.1:5173。真实密钥不得提交。

特色：文件修改后旧读取自动失效；edit_file 只允许唯一匹配的局部替换；路径边界、分级审批、高风险命令门禁和 shell 副作用审计共同约束执行。Verified Finish 要求最后一次修改后必须有成功测试、构建或检查，否则不会宣告验证完成。同一 Session 支持多 Run、取消和恢复。每轮结束后从事件提取目标、修改、验证、失败与验收约束；同仓库的新 Session 可按相关度、重要度和时间检索这些经验。失效的历史代码观察会提示重新读取当前文件。

验证：2026-09-01 实测 Python 254 passed/1 skipped、Spring Boot 27 passed、前端 42 passed，生产构建通过。Verified Finish 不代表测试覆盖全部风险。
