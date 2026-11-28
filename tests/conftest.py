import pytest


@pytest.fixture(autouse=True)
def _reset_bat_count_cache():
    from grobro.ha.client import _MAX_BAT_CACHE
    _MAX_BAT_CACHE.clear()
    yield
    _MAX_BAT_CACHE.clear()
