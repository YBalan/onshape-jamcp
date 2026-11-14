import pytest

from onshape_jamcp.config import Settings
from onshape_jamcp.errors import ConfigError, InputError
from onshape_jamcp.units import IMPERIAL, METRIC, parse_unit_system


class TestUnits:
    def test_metric_is_default(self):
        assert Settings.from_env({}).units is METRIC

    @pytest.mark.parametrize("raw", ["imperial", "IMPERIAL", "inches", "in", " us "])
    def test_imperial_aliases(self, raw):
        assert parse_unit_system(raw) is IMPERIAL

    def test_unknown_unit_system(self):
        with pytest.raises(InputError, match="Unknown unit system"):
            parse_unit_system("cubits")

    def test_from_meters(self):
        assert METRIC.from_meters(0.0254) == 25.4
        assert IMPERIAL.from_meters(0.0254) == 1.0

    def test_area_and_volume(self):
        assert METRIC.area_from_m2(0.0008) == 800.0
        assert METRIC.volume_from_m3(8e-6) == 8000.0
        assert IMPERIAL.area_from_m2(0.0254**2) == 1.0

    def test_length_to_meters_numbers_use_configured_unit(self):
        assert METRIC.length_to_meters(10) == pytest.approx(0.01)
        assert IMPERIAL.length_to_meters(10) == pytest.approx(0.254)

    def test_length_to_meters_explicit_units_override_system(self):
        assert METRIC.length_to_meters("1 in") == pytest.approx(0.0254)
        assert IMPERIAL.length_to_meters("25.4mm") == pytest.approx(0.0254)
        assert METRIC.length_to_meters("12") == pytest.approx(0.012)

    @pytest.mark.parametrize("bad", ["#width", "ten", "5 parsecs", True, float("nan")])
    def test_length_to_meters_rejects(self, bad):
        with pytest.raises(InputError):
            METRIC.length_to_meters(bad)

    def test_length_expression(self):
        assert METRIC.length_expression(10) == "10 mm"
        assert METRIC.length_expression(2.5) == "2.5 mm"
        assert IMPERIAL.length_expression(0.5) == "0.5 in"
        assert METRIC.length_expression("1 in") == "1 in"
        assert METRIC.length_expression("7") == "7 mm"
        assert METRIC.length_expression("#width") == "#width"
        assert METRIC.length_expression("#width / 2") == "#width / 2"

    @pytest.mark.parametrize("bad", ["", "  ", "10; drop", True, None])
    def test_length_expression_rejects(self, bad):
        with pytest.raises(InputError):
            METRIC.length_expression(bad)

    def test_angle_expression(self):
        assert METRIC.angle_expression(90) == "90 deg"
        assert METRIC.angle_expression("45") == "45 deg"
        assert METRIC.angle_expression("#a") == "#a"


class TestSettings:
    def test_defaults(self):
        s = Settings.from_env({})
        assert (s.units.length_unit, s.screenshot_review, s.read_only) == ("mm", True, False)
        assert s.base_url == "https://cad.onshape.com"
        assert not s.has_credentials

    def test_toggles(self):
        s = Settings.from_env(
            {
                "ONSHAPE_UNITS": "imperial",
                "ONSHAPE_SCREENSHOT_REVIEW": "off",
                "ONSHAPE_READ_ONLY": "1",
            }
        )
        assert s.units is IMPERIAL
        assert s.screenshot_review is False
        assert s.read_only is True

    @pytest.mark.parametrize(
        "env",
        [
            {"ONSHAPE_UNITS": "parsecs"},
            {"ONSHAPE_SCREENSHOT_REVIEW": "maybe"},
            {"ONSHAPE_SCREENSHOT_WIDTH": "10"},
            {"ONSHAPE_SCREENSHOT_WIDTH": "wide"},
            {"ONSHAPE_AUTH_MODE": "magic"},
            {"ONSHAPE_API_VERSION": "17"},
            {"ONSHAPE_TIMEOUT": "0"},
            {"ONSHAPE_BASE_URL": "http://cad.onshape.com"},
            {"ONSHAPE_BASE_URL": "https://cad.onshape.com/some/path"},
        ],
    )
    def test_invalid_values_are_rejected(self, env):
        with pytest.raises(ConfigError):
            Settings.from_env(env)

    def test_base_url_normalization(self):
        assert Settings.from_env({"ONSHAPE_BASE_URL": "https://acme.onshape.com/"}).base_url == (
            "https://acme.onshape.com"
        )
        assert Settings.from_env({"ONSHAPE_BASE_URL": "https://acme.onshape.com/api"}).base_url == (
            "https://acme.onshape.com"
        )
        assert Settings.from_env({"ONSHAPE_BASE_URL": "http://localhost:8080"}).base_url == (
            "http://localhost:8080"
        )

    def test_public_dict_never_contains_secrets(self):
        s = Settings.from_env({"ONSHAPE_ACCESS_KEY": "AK123", "ONSHAPE_SECRET_KEY": "SK456"})
        dumped = str(s.public_dict()) + repr(s)
        assert "AK123" not in dumped and "SK456" not in dumped
        assert s.public_dict()["credentials_configured"] is True

    def test_require_credentials_message(self):
        with pytest.raises(ConfigError, match="ONSHAPE_ACCESS_KEY"):
            Settings.from_env({}).require_credentials()
