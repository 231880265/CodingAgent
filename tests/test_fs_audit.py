"""工作区快照只报告有意义的净文件副作用。"""

from __future__ import annotations

import os
from pathlib import Path

from hako.fs_audit import diff_snapshots, snapshot_workspace


def test_snapshot_distinguishes_create_modify_delete_and_ignores_caches(
    workspace: Path,
):
    changed = workspace / "changed.py"
    deleted = workspace / "deleted.txt"
    changed.write_text("before\n", encoding="utf-8")
    deleted.write_text("gone\n", encoding="utf-8")
    before = snapshot_workspace(workspace)

    changed.write_text("after\n", encoding="utf-8")
    deleted.unlink()
    (workspace / "created.py").write_text("new\n", encoding="utf-8")
    cache = workspace / ".pytest_cache"
    cache.mkdir()
    (cache / "noise").write_text("ignore\n", encoding="utf-8")
    pycache = workspace / "pkg" / "__pycache__"
    pycache.mkdir(parents=True)
    (pycache / "a.pyc").write_bytes(b"ignore")
    generated = workspace / "tmp"
    generated.mkdir()
    (generated / "scratch.txt").write_text("ignore\n", encoding="utf-8")

    effects = diff_snapshots(before, snapshot_workspace(workspace))

    assert effects.created == ("created.py",)
    assert effects.modified == ("changed.py",)
    assert effects.deleted == ("deleted.txt",)
    assert effects.touched_paths == ("changed.py", "created.py", "deleted.txt")
    assert effects.summary == "files +1 ~1 -1"


def test_small_file_digest_catches_same_size_change_with_restored_mtime(
    workspace: Path,
):
    target = workspace / "same-size.txt"
    target.write_text("aa", encoding="utf-8")
    original = target.stat()
    before = snapshot_workspace(workspace)

    target.write_text("bb", encoding="utf-8")
    os.utime(target, ns=(original.st_atime_ns, original.st_mtime_ns))
    effects = diff_snapshots(before, snapshot_workspace(workspace))

    assert effects.modified == ("same-size.txt",)
