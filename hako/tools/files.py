"""文件读写工具。"""

from __future__ import annotations

from pathlib import Path

from ..truncate import clip_lines
from .base import Tool, ToolError, ToolResult, rel, resolve_in_workspace

DEFAULT_READ_LIMIT = 200


def _read_text(target: Path) -> str:
    """读文本。二进制文件直接拒绝——把 PNG 塞进上下文没有任何意义，纯烧 token。"""
    try:
        raw = target.read_bytes()
    except OSError as exc:
        raise ToolError(f"读取失败：{exc}") from None

    if b"\x00" in raw[:8192]:
        raise ToolError(f"{target.name} 看起来是二进制文件，拒绝读取")

    # utf-8 优先，失败退 gbk（Windows 上的中文源码常见），最后强制替换。
    for encoding in ("utf-8", "gbk"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def make_read_file(workspace: Path) -> Tool:
    def handler(path: str, offset: int = 0, limit: int = DEFAULT_READ_LIMIT) -> ToolResult:
        target = resolve_in_workspace(workspace, path)
        if not target.exists():
            raise ToolError(f"文件不存在：{path}")
        if target.is_dir():
            raise ToolError(f"{path} 是目录，不是文件。用 list_dir 查看目录内容")

        lines = _read_text(target).splitlines()
        total = len(lines)
        offset = max(0, int(offset))
        limit = max(1, min(int(limit), 2000))

        if offset >= total and total > 0:
            raise ToolError(f"offset={offset} 超出文件范围（{path} 共 {total} 行）")

        window = lines[offset : offset + limit]
        body, _ = clip_lines(
            window, limit, start_line=offset + 1, total_lines=total, path=path
        )
        shown = len(window)
        return ToolResult(
            ok=True,
            detail=body or "(空文件)",
            summary=f"{rel(workspace, target)} 第 {offset + 1}-{offset + shown} 行 / 共 {total} 行",
            # 声明"我读的是这个文件"，用规范化路径，好让写操作之后能匹配上。
            subject_path=rel(workspace, target),
        )

    return Tool(
        name="read_file",
        description=(
            "读取文件内容，返回带行号的文本。默认只读前 200 行；"
            "文件更长时结果末尾会给出继续读取的 offset。"
            "编辑一个文件之前必须先读它——不要凭猜测构造 edit_file 的参数。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对于工作目录的文件路径"},
                "offset": {"type": "integer", "description": "从第几行开始（0 起），默认 0"},
                "limit": {"type": "integer", "description": f"最多读多少行，默认 {DEFAULT_READ_LIMIT}"},
            },
            "required": ["path"],
        },
        handler=handler,
        read_only=True,
    )


def make_write_file(workspace: Path) -> Tool:
    def handler(path: str, content: str) -> ToolResult:
        target = resolve_in_workspace(workspace, path)
        if target.is_dir():
            raise ToolError(f"{path} 是目录，无法写入")

        existed = target.is_file()
        old_lines = len(_read_text(target).splitlines()) if existed else 0

        target.parent.mkdir(parents=True, exist_ok=True)
        try:
            # newline="" 保留 content 里的换行风格，不让 Python 在 Windows 上
            # 把 \n 悄悄转成 \r\n —— 那会让整个文件在 git diff 里全行变更。
            target.write_text(content, encoding="utf-8", newline="")
        except OSError as exc:
            raise ToolError(f"写入失败：{exc}") from None

        new_lines = len(content.splitlines())
        verb = "覆盖" if existed else "创建"
        return ToolResult(
            ok=True,
            detail=f"已{verb} {rel(workspace, target)}（{new_lines} 行）",
            summary=f"{verb} {rel(workspace, target)}  {old_lines} → {new_lines} 行",
            touched_paths=(rel(workspace, target),),
        )

    return Tool(
        name="write_file",
        description=(
            "写入文件，会完整覆盖原有内容，父目录不存在时自动创建。"
            "仅用于新建文件或整体重写小文件；"
            "修改已有文件的局部内容请用 edit_file，那样更省 token 也更安全。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "相对于工作目录的文件路径"},
                "content": {"type": "string", "description": "完整文件内容"},
            },
            "required": ["path", "content"],
        },
        handler=handler,
        needs_approval=True,
    )


def make_list_dir(workspace: Path) -> Tool:
    def handler(path: str = ".") -> ToolResult:
        target = resolve_in_workspace(workspace, path)
        if not target.is_dir():
            raise ToolError(f"不是目录：{path}")

        skip = {".git", "__pycache__", ".venv", "node_modules", ".hako"}
        entries = sorted(
            (e for e in target.iterdir() if e.name not in skip),
            key=lambda e: (e.is_file(), e.name.lower()),
        )
        if not entries:
            return ToolResult(ok=True, detail="(空目录)", summary=f"{path} 为空")

        lines = [
            f"{e.name}/" if e.is_dir() else f"{e.name}\t{e.stat().st_size}B"
            for e in entries[:200]
        ]
        if len(entries) > 200:
            lines.append(f"… 另有 {len(entries) - 200} 项未列出")
        return ToolResult(
            ok=True,
            detail="\n".join(lines),
            summary=f"{rel(workspace, target)}/ 共 {len(entries)} 项",
        )

    return Tool(
        name="list_dir",
        description="列出目录内容。自动跳过 .git / __pycache__ / node_modules 等噪音目录。",
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "目录路径，默认当前工作目录"}
            },
            "required": [],
        },
        handler=handler,
        read_only=True,
    )
