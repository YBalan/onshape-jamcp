"""Guards against documentation drifting away from the code."""

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def load_generator():
    spec = importlib.util.spec_from_file_location(
        "gen_tools_doc", ROOT / "scripts" / "gen_tools_doc.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_tool_reference_is_up_to_date():
    generated = await load_generator().generate()
    committed = (ROOT / "docs" / "tools.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert committed == generated, "Run: uv run python scripts/gen_tools_doc.py"


def test_every_environment_variable_in_code_is_documented():
    code = "\n".join(p.read_text(encoding="utf-8") for p in (ROOT / "src").rglob("*.py"))
    variables = set(re.findall(r"ONSHAPE_[A-Z_]+", code)) - {"ONSHAPE_MCP"}
    documented = (ROOT / "docs" / "configuration.md").read_text(encoding="utf-8")
    missing = sorted(v for v in variables if v not in documented)
    assert not missing, f"Undocumented settings: {missing}"


def test_version_is_consistent():
    from onshape_jamcp import __version__

    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert f'version = "{__version__}"' in pyproject
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"## [{__version__}]" in changelog


@pytest.mark.parametrize("name", ["glama.json", "Dockerfile", "LICENSE", "README.md"])
def test_publishing_files_exist(name):
    assert (ROOT / name).is_file()


def test_glama_json_is_valid():
    import json

    data = json.loads((ROOT / "glama.json").read_text(encoding="utf-8"))
    assert data["$schema"] == "https://glama.ai/mcp/schemas/server.json"
    assert isinstance(data["maintainers"], list) and data["maintainers"]
