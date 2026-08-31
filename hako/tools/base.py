"""工具契约。

设计决策（见 DESIGN.md #3）：schema 手写，不从类型注解反射生成。
工具描述是 **prompt engineering**，不是类型注解的副产品——"路径必须相对于
工作目录""编辑失败时先重新读文件"这类话是写给模型看的行为约束，
反射生成器写不出来。多打几行字换取对模型行为的直接控制。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


DangerCheck = Callable[[dict[str, Any]], str | None]


@dataclass
class ToolResult:
    """工具执行结果。

    ok=False **不终止循环**：detail 会作为 tool 消息回传给模型，让它自我修正。
    对 agent 来说，错误是给模型的输入，而不是要抛出的异常。
    """

    ok: bool
    detail: str                 # 回传给模型的内容
    summary: str = ""           # 一行摘要，给 TUI
    # 副作用声明：哪些文件被新增、修改或删除。上下文管理器据此让历史里的
    # 旧读取失效；shell 工具还会把三类净变化分别列出，便于审计。
    touched_paths: tuple[str, ...] = ()
    created_paths: tuple[str, ...] = ()
    modified_paths: tuple[str, ...] = ()
    deleted_paths: tuple[str, ...] = ()
    # 编译器、构建系统生成的派生文件仍属于可审计副作用，但不等于作者又修改了源码。
    # 否则 `g++ a.cpp -o a.exe` 会先留下成功构建证据，又因 a.exe 新增而立即把证据清空。
    derived_paths: tuple[str, ...] = ()
    # 这次调用读的是哪个文件（规范化后的相对路径）。
    # 为什么不用调用参数里的 path：模型写 "./src/a.py"，写工具报的是 "src/a.py"，
    # 字符串比不上——失效机制会静默失灵。规范化必须发生在工具边界，
    # 由工具自己声明"我读的是谁"（见 DESIGN.md #5）。
    subject_path: str = ""
    # run_command 若执行了内核认可的验证命令，会声明验证类型与原命令。
    # 主循环只消费这份结构化元数据，不靠 TUI 摘要或模型自述判断是否验证成功。
    verification_kind: str = ""
    verification_command: str = ""
    # run_command 的机器可读结果。RunMemory 只消费这些字段，不从 detail/summary
    # 中用正则猜 exit=0，避免展示文本变化后破坏证据链。
    command_status: str = ""
    exit_code: int | None = None
    # 工具内部若调用了模型（例如只读 subagent），把真实 usage 交回主循环。
    # 否则总 token 只统计主模型，会让 A/B 成本比较失真。
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def __post_init__(self) -> None:
        if not self.summary:
            first = self.detail.strip().splitlines()
            self.summary = first[0][:120] if first else ("ok" if self.ok else "failed")

    @property
    def authored_paths(self) -> tuple[str, ...]:
        """可能代表交付内容的变更；排除已识别的构建产物。"""
        derived = set(self.derived_paths)
        return tuple(path for path in self.touched_paths if path not in derived)


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]          # JSON Schema
    handler: Callable[..., ToolResult]
    # 工具是否保证只读。当前循环仍然串行执行；这个标记供权限与后续调度使用。
    read_only: bool = False
    # 需要用户批准才能执行
    needs_approval: bool = False
    # 动态高风险检查。返回规则说明表示必须逐次显式批准，不能被 -y 或
    # “本会话记住此工具”绕过；None 表示普通权限路径。
    danger_check: DangerCheck | None = None
    # 0 表示不限。昂贵的委派工具可限制每个 Agent.run 的调用次数。
    max_calls_per_run: int = 0

    def danger_reason(self, args: dict[str, Any]) -> str | None:
        return self.danger_check(args) if self.danger_check is not None else None

    def to_openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolError(Exception):
    """工具内部的可恢复错误。会被捕获并转成 ok=False 的 ToolResult 回传给模型。"""


# ---------------------------------------------------------------- 路径沙箱


def resolve_in_workspace(workspace: Path, raw: str) -> Path:
    """把模型给的路径解析到工作目录内，拒绝逃逸。

    先 resolve() 再比前缀，这样 `../`、符号链接、`C:\\Windows\\...` 绝对路径
    都会被同一个检查挡住。只挡 `..` 字符串是挡不住符号链接的。
    """
    if not raw or not raw.strip():
        raise ToolError("path 不能为空")

    candidate = Path(raw.strip())
    target = (workspace / candidate).resolve() if not candidate.is_absolute() else candidate.resolve()

    try:
        target.relative_to(workspace)
    except ValueError:
        raise ToolError(
            f"拒绝访问工作目录之外的路径：{raw}（工作目录：{workspace}）"
        ) from None
    return target


def rel(workspace: Path, path: Path) -> str:
    """转成相对路径显示——绝对路径又长又泄露用户名，还白占 token。"""
    try:
        return path.relative_to(workspace).as_posix()
    except ValueError:
        return path.as_posix()
