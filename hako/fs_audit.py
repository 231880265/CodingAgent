"""shell 命令前后的工作区净副作用审计。

这不是操作系统沙箱：它只能观察命令返回时仍存在的净变化，无法证明变化一定
由该命令造成，也看不到一次命令内部“创建后又删除”的瞬时文件。它的目标是
补上工程可观测性，让 shell 不再天然绕过文件工具的 touched_paths 协议。
"""

from __future__ import annotations

import hashlib
import os
import stat
from dataclasses import dataclass
from pathlib import Path

# 不把依赖、版本库内部状态和测试缓存误算成业务代码修改；os.walk 会在入口处
# 直接剪枝，而不是遍历后再过滤，避免扫描 .venv/node_modules 的巨大代价。
IGNORED_DIRECTORY_NAMES = frozenset(
    {
        ".git",
        ".hg",
        ".svn",
        ".venv",
        "venv",
        "node_modules",
        "__pycache__",
        ".pytest_cache",
        ".mypy_cache",
        ".ruff_cache",
        ".cache",
        ".tox",
        ".nox",
        "tmp",
        "temp",
    }
)
IGNORED_FILE_SUFFIXES = frozenset({".pyc", ".pyo"})

# 这些文件仍进入 touched_paths，供审计与 UI 展示；只是另外标记为派生产物，
# 不让编译本身看起来像“编译后又改了一次源码”。列表故意保守，只覆盖常见、
# 语义明确的编译产物；无法确认的文件继续按普通变更处理。
DERIVED_DIRECTORY_NAMES = frozenset(
    {
        "build",
        "dist",
        "out",
        "target",
        "obj",
        ".gradle",
    }
)
DERIVED_FILE_SUFFIXES = frozenset(
    {
        ".exe",
        ".dll",
        ".so",
        ".dylib",
        ".o",
        ".obj",
        ".a",
        ".lib",
        ".class",
        ".jar",
        ".war",
        ".ear",
        ".pdb",
    }
)

# 小型源码做内容摘要，能识别“同大小且恢复了 mtime”的覆盖；大文件只比较
# size/mtime/mode，避免每次测试命令都读取模型权重或大型数据集。
MAX_HASH_BYTES = 1_000_000


@dataclass(frozen=True)
class FileFingerprint:
    size: int
    mtime_ns: int
    mode: int
    digest: str = ""


@dataclass(frozen=True)
class WorkspaceSnapshot:
    files: dict[str, FileFingerprint]
    skipped_paths: tuple[str, ...] = ()


@dataclass(frozen=True)
class FileEffects:
    created: tuple[str, ...] = ()
    modified: tuple[str, ...] = ()
    deleted: tuple[str, ...] = ()
    skipped_paths: tuple[str, ...] = ()

    @property
    def touched_paths(self) -> tuple[str, ...]:
        return tuple(sorted(set(self.created) | set(self.modified) | set(self.deleted)))

    @property
    def derived_paths(self) -> tuple[str, ...]:
        return tuple(path for path in self.touched_paths if is_derived_path(path))

    @property
    def changed(self) -> bool:
        return bool(self.created or self.modified or self.deleted)

    @property
    def summary(self) -> str:
        if not self.changed:
            return ""
        return f"files +{len(self.created)} ~{len(self.modified)} -{len(self.deleted)}"

    def detail(self, limit_per_kind: int = 8) -> str:
        lines: list[str] = []
        for label, paths in (
            ("新增", self.created),
            ("修改", self.modified),
            ("删除", self.deleted),
        ):
            if not paths:
                continue
            shown = ", ".join(paths[:limit_per_kind])
            omitted = len(paths) - limit_per_kind
            suffix = f"，另有 {omitted} 个" if omitted > 0 else ""
            lines.append(f"{label}：{shown}{suffix}")
        if self.skipped_paths:
            lines.append(
                f"审计边界：有 {len(self.skipped_paths)} 个路径在快照时不可读取"
            )
        if not lines:
            return ""
        return "[hako] shell 文件副作用（命令前后净变化）：\n" + "\n".join(lines)


def is_derived_path(raw: str) -> bool:
    """保守识别常见构建产物；不把普通源码、测试和配置文件误排除。"""
    path = Path(raw)
    lowered_parts = {part.lower() for part in path.parts[:-1]}
    name = path.name.lower()
    return (
        bool(lowered_parts & DERIVED_DIRECTORY_NAMES)
        or path.suffix.lower() in DERIVED_FILE_SUFFIXES
        or name == "a.out"
    )


def snapshot_workspace(root: Path) -> WorkspaceSnapshot:
    """获取可比较快照；单个路径不可读时记录边界而不是让命令无法执行。"""
    root = root.resolve()
    files: dict[str, FileFingerprint] = {}
    skipped: set[str] = set()

    def remember_error(error: OSError) -> None:
        skipped.add(_relative_or_name(root, getattr(error, "filename", "")))

    for directory, dirnames, filenames in os.walk(
        root, topdown=True, onerror=remember_error, followlinks=False
    ):
        directory_path = Path(directory)
        dirnames[:] = sorted(
            name
            for name in dirnames
            if name.lower() not in IGNORED_DIRECTORY_NAMES
            and not (directory_path / name).is_symlink()
        )
        for name in sorted(filenames):
            path = directory_path / name
            if path.suffix.lower() in IGNORED_FILE_SUFFIXES:
                continue
            relative = _relative_or_name(root, path)
            try:
                info = path.lstat()
                digest = ""
                if stat.S_ISREG(info.st_mode) and info.st_size <= MAX_HASH_BYTES:
                    digest = _digest(path)
                elif stat.S_ISLNK(info.st_mode):
                    digest = f"link:{os.readlink(path)}"
                files[relative] = FileFingerprint(
                    size=info.st_size,
                    mtime_ns=info.st_mtime_ns,
                    mode=info.st_mode,
                    digest=digest,
                )
            except OSError:
                skipped.add(relative)

    return WorkspaceSnapshot(files=files, skipped_paths=tuple(sorted(skipped)))


def diff_snapshots(before: WorkspaceSnapshot, after: WorkspaceSnapshot) -> FileEffects:
    before_paths = set(before.files)
    after_paths = set(after.files)
    created = tuple(sorted(after_paths - before_paths))
    deleted = tuple(sorted(before_paths - after_paths))
    modified = tuple(
        sorted(
            path
            for path in before_paths & after_paths
            if before.files[path] != after.files[path]
        )
    )
    return FileEffects(
        created=created,
        modified=modified,
        deleted=deleted,
        skipped_paths=tuple(sorted(set(before.skipped_paths) | set(after.skipped_paths))),
    )


def _digest(path: Path) -> str:
    digest = hashlib.blake2b(digest_size=16)
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(64 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _relative_or_name(root: Path, raw: str | os.PathLike[str]) -> str:
    if not raw:
        return "(unknown)"
    path = Path(raw)
    candidate = path if path.is_absolute() else root / path
    try:
        # 只做词法相对化，不 resolve 符号链接；审计展示的是工作区中的入口路径，
        # 不是链接指向的外部绝对路径。
        return candidate.relative_to(root).as_posix()
    except ValueError:
        return path.name or str(path)
