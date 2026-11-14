"""Structure and safety checks for .vscode/tasks.json (VS Code itself is not available in CI)."""

import json
import re
import subprocess
import sys

import pytest

from release_helpers import ROOT

TASKS_FILE = ROOT / ".vscode" / "tasks.json"
tasks_json = json.loads(TASKS_FILE.read_text(encoding="utf-8"))  # plain JSON: no comments allowed
TASKS = tasks_json["tasks"]
INPUTS = {i["id"]: i for i in tasks_json["inputs"]}


def test_labels_are_unique_and_every_task_is_described():
    labels = [t["label"] for t in TASKS]
    assert len(labels) == len(set(labels))
    assert all(t.get("detail") for t in TASKS)


def test_every_referenced_input_exists_and_every_input_is_used():
    text = json.dumps(TASKS)
    used = set(re.findall(r"\$\{input:([^}]+)\}", text))
    assert used == set(INPUTS)


def test_python_tasks_use_the_project_venv_on_every_platform():
    for task in TASKS:
        if "python" not in task["command"]:
            continue
        assert task["command"] == "${workspaceFolder}/.venv/bin/python", task["label"]
        assert task["windows"]["command"] == "${workspaceFolder}\\.venv\\Scripts\\python.exe"
        assert (ROOT / task["args"][0]).is_file(), f"{task['label']}: {task['args'][0]} missing"
        assert task["options"]["cwd"] == "${workspaceFolder}"


def test_release_subcommands_used_by_tasks_exist():
    subcommands = {t["args"][1] for t in TASKS if t["args"][:1] == ["scripts/release.py"]}
    assert {"check", "bump", "notes", "preflight", "verify", "publish"} <= subcommands
    for name in subcommands:
        done = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "release.py"), name, "--help"],
            capture_output=True,
            text=True,
        )
        assert done.returncode == 0, f"release.py {name} is not a real subcommand"


class TestPublishTaskIsSafe:
    publish = next(t for t in TASKS if "PUBLISH" in t["label"])

    def test_confirmation_defaults_to_no_and_is_passed_through(self):
        assert self.publish["args"] == [
            "scripts/release.py",
            "publish",
            "--confirm",
            "${input:confirmPublish}",
        ]
        confirm = INPUTS["confirmPublish"]
        assert confirm["default"] == "no"
        assert [o["value"] for o in confirm["options"]] == ["no", "yes"]
        assert "PyPI" in confirm["description"]

    def test_no_other_task_can_commit_tag_or_push(self):
        dangerous = {"commit", "tag", "push", "publish"}
        for task in TASKS:
            if task is self.publish:
                continue
            assert not dangerous & set(task["args"]), f"{task['label']} could change git state"

    def test_smoke_tests_never_touch_git_or_pypi(self):
        for task in TASKS:
            if "smoke" in task["label"].lower():
                assert task["args"][0] == "scripts/smoke_test.py"


@pytest.mark.parametrize("pattern", ["!.vscode/tasks.json"])
def test_gitignore_keeps_tasks_json_but_not_other_editor_files(pattern):
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".vscode/*" in lines and pattern in lines
