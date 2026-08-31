hako 编程智能体

Git 仓库：____________________________（公开后填写）

项目简介：hako 是从零实现的本地通用 Coding Agent，支持 Bug 修复、功能开发、重构和补充测试。模型不直接访问系统；hako 将手写 Tool Schema 与 Conversation 发给兼容 OpenAI Tool Calling 的模型，校验 tool_calls 后在本地受控执行，再将 ToolResult 写回对话。历史、工具、解析、循环终止和错误处理均自行实现，未使用 Agent 框架或外部文件执行 API。

环境：Python 3.12；Web 另需 Java 21、Node.js 20.19+ 或 22.12+。

安装配置（Windows PowerShell）：
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
在本地 .env 填写 API Key，真实密钥不得提交。

CLI：
.\.venv\Scripts\python.exe main.py -C "D:\path\to\repo" "修复失败测试并重新验证"
Web：
.\start-web.ps1 -AllowedRoot "D:\path\to\allowed-root"
打开 http://127.0.0.1:5173。

特色：修改后自动失效旧读取；edit_file 只做唯一匹配局部替换；具备分级审批、路径边界、高风险命令门禁和 shell 副作用审计；Verified Finish 要求最后一次业务修改后出现成功测试、构建或检查，否则返回 DONE_UNVERIFIED；同一 Session 支持多 Run、取消和恢复。Run 结束后从事件确定性生成修改、验证与审批事实，最近对话自动携带，旧事实可用只读工具按需检索。

验证：2026-08-31 实测 Python 208 passed/1 skipped、Spring Boot 19 passed、前端 34 passed，生产构建通过。单次演示不等同于成功率；Verified Finish 不代表覆盖全部风险。
