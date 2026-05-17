from unittest.mock import patch, MagicMock
import pytest


class TestSignalHandler:
    @patch("grobro.ha_bridge.signal.signal")
    def test_init(self, mock_signal):
        from grobro.ha_bridge import SignalHandler
        sh = SignalHandler()
        assert sh.caught is True

    def test_handle_signal(self):
        from grobro.ha_bridge import SignalHandler
        sh = SignalHandler()
        sh._running = True
        sh._handle(None, None)
        assert sh._running is False
        assert sh.caught is False


class TestModule:
    def test_has_configs(self):
        import grobro.ha_bridge
        assert hasattr(grobro.ha_bridge, "GROBRO_MQTT_CONFIG")
        assert hasattr(grobro.ha_bridge, "HA_MQTT_CONFIG")
        assert hasattr(grobro.ha_bridge, "FORWARD_MQTT_CONFIG")
