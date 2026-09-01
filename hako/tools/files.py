"""文件读写工具。"""

from __future__ import annotations

from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from ..truncate import clip_lines
from .base import Tool, ToolError, ToolResult, rel, resolve_in_workspace

DEFAULT_READ_LIMIT = 200


def _normalize_model_edit_args(args: dict[str, Any]) -> dict[str, Any]:
    """兼容 Claude Code Edit 的 replace_all，但不放宽唯一定位约束。"""
    normalized = dict(args)
    replace_all = normalized.pop("replace_all", None)
    if replace_all is None:
        return normalized
    if isinstance(replace_all, str):
        replace_all = replace_all.strip().lower() in {"1", "true", "yes"}
    if bool(replace_all):
        raise ToolError(
            "edit_file 不支持 replace_all=true；为避免批量改错，本工具只接受唯一匹配。"
            "请增加 old_string/old_text 的前后文，或拆成多次可审计的局部编辑"
        )
    return normalized


def _read_text_with_encoding(target: Path, *, allow_lossy: bool = True) -> tuple[str, str]:
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
            return raw.decode(encoding), encoding
        except UnicodeDecodeError:
            continue
    if allow_lossy:
        return raw.decode("utf-8", errors="replace"), "utf-8"
    raise ToolError(f"{target.name} 的文本编码无法可靠识别，为避免损坏已拒绝编辑")


def _read_text(target: Path) -> str:
    return _read_text_with_encoding(target)[0]


def _dominant_newline(text: str) -> str:
    """返回文件的主换行风格；新文件或单行文件使用 LF。"""
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    cr = text.count("\r") - crlf
    if crlf >= lf and crlf >= cr and crlf:
        return "\r\n"
    if cr > lf and cr:
        return "\r"
    return "\n"


def _adapt_newlines(text: str, newline: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return normalized.replace("\n", newline)


def _match_positions(text: str, needle: str) -> list[int]:
    """返回全部匹配位置，包括重叠匹配，避免把歧义误判成唯一。"""
    positions: list[int] = []
    start = 0
    while True:
        found = text.find(needle, start)
        if found < 0:
            return positions
        positions.append(found)
        start = found + 1


def _nearest_candidate(text: str, needle: str) -> str:
    """给 0 匹配错误一个可操作的近似位置，而不是只说“没找到”。"""
    lines = text.splitlines()
    wanted = needle.splitlines() or [needle]
    if not lines:
        return "文件为空，请重新确认目标文件。"

    anchor_offset = next((i for i, line in enumerate(wanted) if line.strip()), 0)
    anchor = wanted[anchor_offset].strip()
    best_index = max(
        range(len(lines)),
        key=lambda i: SequenceMatcher(None, anchor, lines[i].strip()).ratio(),
    )
    width = max(1, len(wanted))
    start = min(max(0, best_index - anchor_offset), max(0, len(lines) - width))
    candidate = "\n".join(lines[start : start + width])
    score = SequenceMatcher(
        None,
        _adapt_newlines(needle, "\n"),
        _adapt_newlines(candidate, "\n"),
    ).ratio()
    preview = candidate[:800] + ("…" if len(candidate) > 800 else "")
    end = min(len(lines), start + width)
    return f"最接近候选位于第 {start + 1}-{end} 行（相似度 {score:.0%}）：\n{preview}"


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
        body, has_more = clip_lines(
            window, limit, start_line=offset + 1, total_lines=total, path=path
        )
        shown = len(window)
        return ToolResult(
            ok=True,
            detail=body or "(空文件)",
            summary=f"{rel(workspace, target)} 第 {offset + 1}-{offset + shown} 行 / 共 {total} 行",
            # 声明"我读的是这个文件"，用规范化路径，好让写操作之后能匹配上。
            subject_path=rel(workspace, target),
            next_offset=(offset + shown if has_more else None),
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
        argument_aliases={"file_path": "path"},
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
        argument_aliases={"file_path": "path"},
    )


def make_edit_file(workspace: Path) -> Tool:
    """创建唯一匹配的 search-replace 工具。

    不接受 unified diff：模型生成的行号容易因上下文漂移失效；也不静默选择第一个
    匹配，因为“成功执行但改错位置”比明确失败更危险。
    """

    def handler(path: str, old_text: str, new_text: str) -> ToolResult:
        target = resolve_in_workspace(workspace, path)
        if not target.exists():
            raise ToolError(f"文件不存在：{path}")
        if target.is_dir():
            raise ToolError(f"{path} 是目录，无法编辑")
        if not old_text:
            raise ToolError("old_text 不能为空；空定位串无法证明编辑位置唯一")

        content, encoding = _read_text_with_encoding(target, allow_lossy=False)
        newline = _dominant_newline(content)
        matched_old = old_text
        positions = _match_positions(content, matched_old)

        # read_file 回传统一使用 LF；目标文件可能是 CRLF。只适配定位串和替换片段，
        # 不规范化整个文件，因此不会制造“全文件换行变化”的噪声 diff。
        if not positions:
            adapted = _adapt_newlines(old_text, newline)
            if adapted != old_text:
                matched_old = adapted
                positions = _match_positions(content, matched_old)

        canonical = rel(workspace, target)
        if not positions:
            raise ToolError(
                f"old_text 在 {canonical} 中匹配 0 处，文件可能已变化。"
                "请根据候选重新 read_file 后构造定位串。\n"
                f"{_nearest_candidate(content, old_text)}"
            )
        if len(positions) > 1:
            line_numbers = [content.count("\n", 0, pos) + 1 for pos in positions[:8]]
            suffix = "…" if len(positions) > 8 else ""
            raise ToolError(
                f"old_text 在 {canonical} 中匹配 {len(positions)} 处"
                f"（起始行：{', '.join(map(str, line_numbers))}{suffix}）。"
                "为避免改错位置，本次未写入；请增加前后文使定位串唯一。"
            )

        replacement = _adapt_newlines(new_text, newline)
        position = positions[0]
        if matched_old == replacement:
            return ToolResult(
                ok=True,
                detail=f"{canonical} 的目标片段与 new_text 相同，无需写入",
                summary=f"{canonical} 无变化",
            )

        updated = content[:position] + replacement + content[position + len(matched_old) :]
        try:
            encoded = updated.encode(encoding)
        except UnicodeEncodeError:
            raise ToolError(
                f"new_text 无法使用原文件编码 {encoding} 表示，为避免截断文件已拒绝编辑"
            ) from None
        try:
            target.write_bytes(encoded)
        except OSError as exc:
            raise ToolError(f"写入失败：{exc}") from None

        line = content.count("\n", 0, position) + 1
        return ToolResult(
            ok=True,
            detail=f"已编辑 {canonical} 第 {line} 行附近；唯一匹配已替换",
            summary=f"编辑 {canonical}  第 {line} 行",
            touched_paths=(canonical,),
        )

    return Tool(
        name="edit_file",
        description=(
            "局部编辑已有文本文件：把唯一匹配的 old_text 替换为 new_text。"
            "匹配 0 处会返回最接近候选，匹配多处会拒绝写入；绝不静默选择第一处。"
            "编辑前必须先 read_file，old_text 应包含足够前后文且保持原缩进。"
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "工作目录内的已有文件路径"},
                "old_text": {"type": "string", "description": "必须唯一出现的原始文本"},
                "new_text": {"type": "string", "description": "替换后的文本，可为空字符串"},
            },
            "required": ["path", "old_text", "new_text"],
        },
        handler=handler,
        needs_approval=True,
        argument_aliases={
            "file_path": "path",
            "old_string": "old_text",
            "new_string": "new_text",
        },
        argument_adapter=_normalize_model_edit_args,
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
