"""Shared test data builders; runtime and eval code do not depend on these fixtures."""

import subprocess
import uuid

import pytest


@pytest.fixture
def file_tree(tmp_path):
    def create(files):
        root = tmp_path / str(uuid.uuid4())
        root.mkdir()
        for relative, content in files.items():
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content if isinstance(content, bytes) else content.encode("utf-8"))
        return root

    return create


@pytest.fixture
def sandbox(file_tree):
    def create(files):
        root = file_tree(files)
        for args in (
            ["init", "-q", "-b", "main"],
            ["config", "core.autocrlf", "false"],
            ["config", "core.hooksPath", str(root / ".git" / "hooks")],
            ["add", "-A"],
            ["-c", "user.email=t@local", "-c", "user.name=t", "commit", "-q", "--no-verify", "-m", "seed"],
        ):
            subprocess.run(["git", *args], cwd=root, capture_output=True, check=True, text=True)
        return root

    return create
