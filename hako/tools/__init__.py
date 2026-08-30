"""工具注册表与分派。

参数校验自己写（见 DESIGN.md #5）：模型给的 arguments 是不可信输入——
少必填字段、类型给错（"200" 而不是 200）、多塞不存在的字段都真实发生过。
校验失败**不抛异常终止**，而是把"哪里错了 + 正确签名"回传给模型重试。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from .base import Tool, ToolError, ToolResult
from .files import make_edit_file, make_list_dir, make_read_file, make_write_file
from .shell import make_run_command

__all__ = [
    "Registry",
    "Tool",
    "ToolError",
    "ToolResult",
    "build_default_registry",
    "build_readonly_registry",
]


class Registry:
    def __init__(self, tools: list[Tool]) -> None:
        self._tools = {tool.name: tool for tool in tools}

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.to_openai_schema() for tool in self._tools.values()]

    def names(self) -> list[str]:
        return list(self._tools)

    # ------------------------------------------------------------ 校验与执行

    def _validate(self, tool: Tool, args: dict[str, Any]) -> dict[str, Any]:
        schema = tool.parameters
        props: dict[str, Any] = schema.get("properties", {})
        required: list[str] = schema.get("required", [])

        missing = [key for key in required if key not in args or args[key] is None]
        if missing:
            raise ToolError(
                f"缺少必填参数 {missing}。{tool.name} 的参数：{_signature(props, required)}"
            )

        cleaned: dict[str, Any] = {}
        for key, value in args.items():
            if key not in props:
                # 多余字段丢掉而不是报错：模型偶尔会加 "explanation" 之类的字段，
                # 为此中断一整轮不值得。真正缺东西的情况上面已经拦住了。
                continue
            cleaned[key] = _coerce(key, value, props[key].get("type"), tool.name)
        return cleaned

    def invoke(self, name: str, args: dict[str, Any]) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(
                ok=False,
                detail=f"不存在的工具 {name!r}。可用工具：{', '.join(self._tools)}",
                summary=f"未知工具 {name}",
            )
        try:
            return tool.handler(**self._validate(tool, args))
        except ToolError as exc:
            # 预期内的可恢复失败：原样回传给模型
            return ToolResult(ok=False, detail=str(exc), summary=str(exc).splitlines()[0][:120])
        except TypeError as exc:
            return ToolResult(ok=False, detail=f"参数不匹配：{exc}", summary="参数不匹配")
        except Exception as exc:  # noqa: BLE001
            # 意料之外的 bug：也回传给模型（它可能换个路子绕开），但带上类型名便于排查
            return ToolResult(
                ok=False,
                detail=f"{tool.name} 执行时发生未预期错误：{type(exc).__name__}: {exc}",
                summary=f"{type(exc).__name__}: {exc}"[:120],
            )


def _coerce(key: str, value: Any, expected: str | None, tool_name: str) -> Any:
    """宽容地转换类型。模型经常把整数写成字符串。"""
    if expected == "integer" and not isinstance(value, bool):
        if isinstance(value, int):
            return value
        try:
            return int(str(value).strip())
        except (TypeError, ValueError):
            raise ToolError(f"{tool_name} 的参数 {key} 需要整数，收到 {value!r}") from None
    if expected == "string" and not isinstance(value, str):
        return str(value)
    return value


def _signature(props: dict[str, Any], required: list[str]) -> str:
    return ", ".join(
        f"{k}: {v.get('type', 'any')}{'' if k in required else ' (可选)'}"
        for k, v in props.items()
    )


def build_readonly_registry(workspace: Path) -> Registry:
    """只读调查工具集；刻意没有 shell、写入或递归委派。"""
    return Registry([make_read_file(workspace), make_list_dir(workspace)])


def build_default_registry(
    workspace: Path,
    tool_result_budget: int = 6000,
    extra_tools: list[Tool] | None = None,
    *,
    cancelled: Callable[[], bool] | None = None,
) -> Registry:
    """核心工具集：列目录、读取、局部编辑、整文件写入和执行命令。

    刻意保持小：每个工具的 description 在**每一轮**请求里都要付 token，
    语义重叠还会让模型选错。能力覆盖靠 run_command 兜底，
    高频且需要结构化输出控制的操作才单独建工具。
    """
    tools = [
        make_read_file(workspace),
        make_edit_file(workspace),
        make_write_file(workspace),
        make_list_dir(workspace),
        make_run_command(
            workspace,
            tool_result_budget,
            cancelled=cancelled,
        ),
    ]
    tools.extend(extra_tools or [])
    return Registry(tools)
