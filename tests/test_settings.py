import pytest

from grobro.settings import env_bool, env_int, parse_max_bat


@pytest.mark.parametrize("raw, expected", [("true", True), ("True", True), (" TRUE ", True), ("false", False), ("False", False), ("", False), ("yes", False)])
def test_env_bool(monkeypatch, raw, expected):
    monkeypatch.setenv("GROBRO_TEST_BOOL", raw)
    assert env_bool("GROBRO_TEST_BOOL") is expected


def test_env_bool_default(monkeypatch):
    monkeypatch.delenv("GROBRO_TEST_BOOL", raising=False)
    assert env_bool("GROBRO_TEST_BOOL", True) is True


@pytest.mark.parametrize("raw, expected", [("300", 300), ("0", 0), ("", 5), ("null", 5), ("abc", 5)])
def test_env_int(monkeypatch, raw, expected):
    monkeypatch.setenv("GROBRO_TEST_INT", raw)
    assert env_int("GROBRO_TEST_INT", 5) == expected


@pytest.mark.parametrize("raw, expected", [("auto", "auto"), ("2", 2), ("0", 0)])
def test_parse_max_bat(raw, expected):
    assert parse_max_bat(raw) == expected
