import pytest

from fake_onshape import ACCESS_KEY, SECRET_KEY, FakeOnshape
from onshape_jamcp.config import Settings
from onshape_jamcp.server import build_server


@pytest.fixture
def fake() -> FakeOnshape:
    return FakeOnshape()


@pytest.fixture
def make_server(fake, tmp_path):
    """Build a server wired to the fake Onshape, with optional settings overrides."""

    def factory(**env: str):
        base = {
            "ONSHAPE_ACCESS_KEY": ACCESS_KEY,
            "ONSHAPE_SECRET_KEY": SECRET_KEY,
            "ONSHAPE_OUTPUT_DIR": str(tmp_path / "out"),
        }
        settings = Settings.from_env({**base, **env})
        return build_server(settings, transport=fake.transport())

    return factory
