import pytest


@pytest.fixture(autouse=True)
def _tmp_config_dir(tmp_path, monkeypatch):
    """Redirect GroBro's persistent /config storage into a per-test tmp dir."""
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    monkeypatch.setattr("grobro.ha.client.CONFIG_DIR", str(config_dir))
    return config_dir
