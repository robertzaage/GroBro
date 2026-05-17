from unittest.mock import patch


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

    def test_logger_fallback_on_bad_level(self):
        import importlib
        with patch.dict("os.environ", {"LOG_LEVEL": "INVALID_LEVEL_THAT_IS_WAY_TOO_LONG"}):
            with patch("grobro.ha_bridge.logging.basicConfig") as mock_basic_config:
                mock_basic_config.side_effect = [ValueError("bad level"), None]
                import grobro.ha_bridge as mod
                importlib.reload(mod)
                assert mock_basic_config.call_count == 2

    def test_logger_fallback_prints_error(self, capsys):
        import importlib
        with patch.dict("os.environ", {"LOG_LEVEL": "INVALID"}):
            with patch("grobro.ha_bridge.logging.basicConfig") as mock_basic_config:
                mock_basic_config.side_effect = [ValueError("bad level"), None]
                import grobro.ha_bridge as mod
                importlib.reload(mod)
                captured = capsys.readouterr()
                assert "Failed to setup logger" in captured.out

    def test_config_from_env_source_prefix(self):
        import importlib
        with patch.dict("os.environ", {
            "SOURCE_MQTT_HOST": "source.local",
            "SOURCE_MQTT_PORT": "1883",
            "TARGET_MQTT_HOST": "target.local",
            "TARGET_MQTT_PORT": "2883",
        }):
            with patch("grobro.ha_bridge.signal.signal"):
                import grobro.ha_bridge as mod
                importlib.reload(mod)
                assert mod.GROBRO_MQTT_CONFIG.host == "source.local"
                assert mod.GROBRO_MQTT_CONFIG.port == 1883
                assert mod.HA_MQTT_CONFIG.host == "target.local"
                assert mod.HA_MQTT_CONFIG.port == 2883
