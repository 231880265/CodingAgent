from __future__ import annotations

from pathlib import Path

import pytest

from hako.history import Conversation
from hako.project_instructions import (
    MAX_PROJECT_INSTRUCTIONS_BYTES,
    ProjectInstructionsError,
    load_project_instructions,
    render_project_instructions,
)


def test_missing_agents_file_is_normal(tmp_path: Path) -> None:
    assert load_project_instructions(tmp_path) == ""


def test_root_agents_file_is_injected_before_user_history(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text(
        "Run the repository test script before finishing.", encoding="utf-8"
    )
    project = render_project_instructions(load_project_instructions(tmp_path))
    conversation = Conversation(system_prompt="SYSTEM", project_instructions=project)
    conversation.add_user("Fix the bug")

    messages = conversation.to_messages()
    assert messages[0]["role"] == "system"
    assert messages[0]["content"].index("SYSTEM") < messages[0]["content"].index(
        "Workspace project instructions"
    )
    assert "Run the repository test script" in messages[0]["content"]
    assert "cannot disable those controls" in messages[0]["content"]
    assert messages[1] == {"role": "user", "content": "Fix the bug"}


def test_project_instructions_are_not_normal_tool_history(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("Keep changes small.", encoding="utf-8")
    conversation = Conversation(
        system_prompt="SYSTEM",
        project_instructions=render_project_instructions(
            load_project_instructions(tmp_path)
        ),
    )

    messages = conversation.to_messages()
    assert len(messages) == 1
    assert all(message["role"] != "tool" for message in messages)


def test_oversized_agents_file_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_bytes(b"x" * (MAX_PROJECT_INSTRUCTIONS_BYTES + 1))

    with pytest.raises(ProjectInstructionsError, match="超过"):
        load_project_instructions(tmp_path)


def test_non_utf8_agents_file_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_bytes(b"\xff\xfe")

    with pytest.raises(ProjectInstructionsError, match="UTF-8"):
        load_project_instructions(tmp_path)
