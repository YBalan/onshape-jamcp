"""The Claude Desktop installer (.mcpb) is built from the code, so these tests keep it honest."""

import asyncio
import importlib.util
import json
import re
import struct
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
REQUIRED = ("manifest_version", "name", "version", "description", "author", "server")


@pytest.fixture(scope="module")
def builder():
    spec = importlib.util.spec_from_file_location("build_mcpb", ROOT / "scripts" / "build_mcpb.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def tools(builder):
    return asyncio.run(builder.list_tools())


@pytest.fixture(scope="module")
def manifest(builder, tools):
    return builder.render_manifest(builder.project_version(), tools)


def test_manifest_has_the_required_fields_and_the_project_version(builder, manifest):
    assert all(key in manifest for key in REQUIRED)
    assert manifest["version"] == builder.project_version()
    assert re.fullmatch(r"\d+\.\d+\.\d+", manifest["version"])
    assert manifest["author"]["name"]
    assert manifest["license"] == "MIT"


def test_it_is_a_uv_bundle_whose_entry_point_ships_in_the_bundle(builder, manifest, tools):
    server = manifest["server"]
    assert manifest["manifest_version"] == "0.4"  # the first version that knows the uv type
    assert server["type"] == "uv"
    names = [name for name, _ in builder.bundle_files(manifest["version"], tools)]
    assert server["entry_point"] in names
    assert manifest["icon"] in names
    assert "pyproject.toml" in names
    assert not any(n.startswith(("server/lib", "server/venv")) for n in names)  # uv forbids these


def test_the_bundle_pins_exactly_this_version_of_the_package(builder, tools):
    files = dict(builder.bundle_files("9.8.7", tools))
    pyproject = files["pyproject.toml"].decode()
    assert 'dependencies = ["onshape-jamcp==9.8.7"]' in pyproject
    assert 'version = "9.8.7"' in pyproject
    assert "{{" not in pyproject and b"{{" not in files["manifest.json"]


def test_every_setting_the_launcher_passes_is_asked_for_in_user_config(manifest):
    env = manifest["server"]["mcp_config"]["env"]
    asked = manifest["user_config"]
    used = set()
    for name, value in env.items():
        assert name.startswith("ONSHAPE_"), name
        match = re.fullmatch(r"\$\{user_config\.(\w+)\}", value)
        assert match, f"{name} should come from a user_config field, got {value!r}"
        used.add(match.group(1))
    assert used == set(asked)  # no unused field, no field without an environment variable


def test_the_environment_variables_are_real_settings(manifest):
    known = set(
        re.findall(r"ONSHAPE_[A-Z_]+", (ROOT / "docs" / "configuration.md").read_text("utf-8"))
    )
    assert set(manifest["server"]["mcp_config"]["env"]) <= known


def test_the_keys_are_required_and_hidden_everything_else_has_a_default(manifest):
    asked = manifest["user_config"]
    for key in ("access_key", "secret_key"):
        assert asked[key]["required"] is True and asked[key]["sensitive"] is True
        assert "default" not in asked[key]  # never ship a key
    for key, spec in asked.items():
        if key not in ("access_key", "secret_key"):
            assert "default" in spec, key
    assert asked["base_url"]["default"] == "https://cad.onshape.com"
    assert asked["units"]["default"] == "metric"
    assert asked["screenshot_review"]["default"] is True and asked["read_only"]["default"] is False


def test_the_tool_list_is_the_servers_own(manifest, tools):
    listed = manifest["tools"]
    assert [t["name"] for t in listed] == [t["name"] for t in tools]
    assert len(listed) >= 37
    assert all(t["description"] and len(t["description"]) <= 160 for t in listed)
    assert manifest["tools_generated"] is False


def test_first_sentence_keeps_it_short(builder):
    assert builder.first_sentence("Make a part.  Then more text.") == "Make a part."
    assert builder.first_sentence(None) == ""
    long = builder.first_sentence("x" * 400)
    assert len(long) <= builder.DESCRIPTION_LIMIT and long.endswith("…")


def test_the_icon_is_a_square_png():
    data = (ROOT / "mcpb" / "icon.png").read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    width, height = struct.unpack(">II", data[16:24])
    assert width == height and width >= 256


def test_the_zip_is_complete_and_reproducible(builder, tools, tmp_path):
    version = builder.project_version()
    files = builder.bundle_files(version, tools)
    first, second = tmp_path / "a" / "x.mcpb", tmp_path / "b" / "x.mcpb"
    builder.write_bundle(first, files)
    builder.write_bundle(second, files)
    assert first.read_bytes() == second.read_bytes()
    with zipfile.ZipFile(first) as bundle:
        assert set(bundle.namelist()) == {
            "manifest.json",
            "pyproject.toml",
            "src/server.py",
            "icon.png",
            "LICENSE",
        }
        assert json.loads(bundle.read("manifest.json"))["version"] == version
        assert bundle.testzip() is None


def test_the_launcher_calls_the_real_entry_point():
    text = (ROOT / "mcpb" / "src" / "server.py").read_text("utf-8")
    assert "from onshape_jamcp.__main__ import main" in text and "main()" in text
    from onshape_jamcp.__main__ import main  # noqa: F401  (the import it relies on exists)


def test_the_build_script_is_in_the_source_distribution():
    pyproject = (ROOT / "pyproject.toml").read_text("utf-8")
    assert '"mcpb"' in pyproject
