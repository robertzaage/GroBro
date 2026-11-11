import pytest


@pytest.fixture(autouse=True)
def _tmp_config_dir(tmp_path, monkeypatch):
    """Redirect GroBro's persistent /config storage into a per-test tmp dir."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setattr("grobro.ha.client.CONFIG_DIR", str(config_dir))
    return config_dir


@pytest.fixture(autouse=True)
def _reset_bat_count_cache():
    from grobro.ha.client import _MAX_BAT_CACHE
    _MAX_BAT_CACHE.clear()
    yield
    _MAX_BAT_CACHE.clear()
