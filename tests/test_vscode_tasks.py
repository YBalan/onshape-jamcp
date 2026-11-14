"""Structure and safety checks for .vscode/tasks.json (VS Code itself is not available in CI)."""

import json
import re
import subprocess
import sys

from release_helpers import ROOT, load_release

TASKS_FILE = ROOT / ".vscode" / "tasks.json"
tasks_json = json.loads(TASKS_FILE.read_text(encoding="utf-8"))  # plain JSON: no comments allowed
TASKS = tasks_json["tasks"]
INPUTS = {i["id"]: i for i in tasks_json["inputs"]}


def test_there_is_exactly_one_release_task():
    assert [t["label"] for t in TASKS] == ["Release"]
    assert TASKS[0].get("detail")


def test_every_referenced_input_exists_and_every_input_is_used():
    used = set(re.findall(r"\$\{input:([^}]+)\}", json.dumps(TASKS)))
    assert used == set(INPUTS)


def test_the_task_uses_the_project_venv_on_every_platform():
    task = TASKS[0]
    assert task["command"] == "${workspaceFolder}/.venv/bin/python"
    assert task["windows"]["command"] == "${workspaceFolder}\\.venv\\Scripts\\python.exe"
    assert (ROOT / task["args"][0]).is_file()
    assert task["options"]["cwd"] == "${workspaceFolder}"


def test_the_task_runs_the_release_subcommand_with_all_three_inputs():
    args = TASKS[0]["args"]
    assert args[:2] == ["scripts/release.py", "release"]
    assert args[args.index("--bump") + 1] == "${input:releaseBump}"
    assert args[args.index("--notes") + 1] == "${input:releaseNotes}"
    assert args[args.index("--confirm") + 1] == "${input:releaseConfirm}"
    done = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "release.py"), "release", "--help"],
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0


class TestTheTaskIsSafe:
    def test_it_defaults_to_a_preview_that_changes_nothing(self):
        confirm = INPUTS["releaseConfirm"]
        assert confirm["default"] == "no"
        assert [o["value"] for o in confirm["options"]] == ["no", "yes"]
        assert "PyPI" in confirm["description"] and "never be replaced" in confirm["description"]

    def test_the_bump_choices_match_what_the_script_accepts(self):
        values = [o["value"] for o in INPUTS["releaseBump"]["options"]]
        assert values == list(load_release().BUMP_CHOICES)
        assert INPUTS["releaseBump"]["default"] == "patch"

    def test_notes_are_optional_free_text(self):
        notes = INPUTS["releaseNotes"]
        assert notes["type"] == "promptString" and "EMPTY" in notes["description"]


def test_gitignore_keeps_tasks_json_but_not_other_editor_files():
    lines = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert ".vscode/*" in lines and "!.vscode/tasks.json" in lines
