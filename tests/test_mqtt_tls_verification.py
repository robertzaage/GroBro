import ssl
from unittest.mock import MagicMock, patch

import pytest

from grobro.model.mqtt_config import MQTTConfig
from grobro.grobro.client import Client as SourceClient
from grobro.ha.client import Client as TargetClient


def config(**kwargs):
    return MQTTConfig(host="mqtt.example.com", port=7006, **kwargs)


def test_plain_mqtt_unchanged():
    client = MagicMock()
    config().configure_tls(client)
    client.tls_set.assert_not_called()
    client.tls_set_context.assert_not_called()
    client.tls_insecure_set.assert_not_called()


def test_legacy_tls_unchanged():
    client = MagicMock()
    config(use_tls=True).configure_tls(client)
    client.tls_set.assert_called_once_with(cert_reqs=ssl.CERT_NONE)
    client.tls_insecure_set.assert_called_once_with(True)
    client.tls_set_context.assert_not_called()


def test_verified_tls_uses_secure_context():
    client = MagicMock()
    config(use_tls=True, tls_verify=True).configure_tls(client)
    context = client.tls_set_context.call_args.args[0]
    assert context.verify_mode == ssl.CERT_REQUIRED
    assert context.check_hostname is True
    client.tls_insecure_set.assert_called_once_with(False)
    client.tls_set.assert_not_called()


def test_custom_ca_is_passed_to_ssl():
    client = MagicMock()
    with patch('grobro.model.mqtt_config.ssl.create_default_context') as create:
        config(use_tls=True, tls_verify=True, tls_ca_file='/certs/ca.pem').configure_tls(client)
    create.assert_called_once_with(cafile='/certs/ca.pem')
    client.tls_set_context.assert_called_once_with(create.return_value)


def test_invalid_ca_never_falls_back(tmp_path):
    client = MagicMock()
    with pytest.raises(OSError):
        config(use_tls=True, tls_verify=True, tls_ca_file=str(tmp_path / 'missing.pem')).configure_tls(client)
    client.tls_set.assert_not_called()
    client.tls_insecure_set.assert_not_called()
    client.tls_set_context.assert_not_called()


@pytest.mark.parametrize('kwargs', [dict(tls_verify=True), dict(use_tls=True, tls_ca_file='/ca.pem')])
def test_inconsistent_settings_rejected(kwargs):
    with pytest.raises(ValueError):
        config(**kwargs).configure_tls(MagicMock())


def test_environment_is_broker_specific(monkeypatch):
    for prefix in ('SOURCE', 'TARGET'):
        for suffix in ('TLS_VERIFY', 'CA_FILE', 'TLS'):
            monkeypatch.delenv(f'{prefix}_MQTT_{suffix}', raising=False)
    monkeypatch.setenv('SOURCE_MQTT_TLS_VERIFY', 'true')
    monkeypatch.setenv('SOURCE_MQTT_CA_FILE', '/source.pem')
    monkeypatch.setenv('SOURCE_MQTT_TLS', 'true')
    source = MQTTConfig.from_env('SOURCE', config())
    target = MQTTConfig.from_env('TARGET', source)
    assert source.tls_verify is True
    assert source.tls_ca_file == '/source.pem'
    assert target.tls_verify is False
    assert target.tls_ca_file is None
    monkeypatch.setenv('TARGET_MQTT_TLS_VERIFY', 'TRUE')
    monkeypatch.setenv('TARGET_MQTT_CA_FILE', '/target.pem')
    target = MQTTConfig.from_env('TARGET', source)
    assert target.tls_verify is True
    assert target.tls_ca_file == '/target.pem'


def test_invalid_verification_value_rejected(monkeypatch):
    monkeypatch.setenv('SOURCE_MQTT_TLS_VERIFY', 'treu')
    with pytest.raises(ValueError, match='SOURCE_MQTT_TLS_VERIFY'):
        MQTTConfig.from_env('SOURCE', config())


@pytest.mark.parametrize('kind', ['source', 'target'])
def test_clients_apply_verified_tls_before_connect(kind):
    cfg = config(use_tls=True, tls_verify=True)
    if kind == 'source':
        with patch('grobro.grobro.client.mqtt.Client') as factory:
            SourceClient(cfg, config())
    else:
        with patch('grobro.ha.client.mqtt.Client') as factory:
            with patch('grobro.ha.client.os.listdir', return_value=[]):
                TargetClient(cfg)
    client = factory.return_value
    context = client.tls_set_context.call_args.args[0]
    assert context.check_hostname
    assert context.verify_mode == ssl.CERT_REQUIRED
    client.connect.assert_called_once_with(cfg.host, cfg.port, 60)
    calls = [call[0] for call in client.method_calls]
    assert calls.index('tls_set_context') < calls.index('connect')
    client.tls_insecure_set.assert_called_once_with(False)


def test_relative_ca_file_resolves_against_config_dir(monkeypatch):
    monkeypatch.setenv('SOURCE_MQTT_CA_FILE', 'certs/ca.pem')
    monkeypatch.setenv('CONFIG_DIR', '/data/grobro')
    source = MQTTConfig.from_env('SOURCE', config())
    assert source.tls_ca_file == '/data/grobro/certs/ca.pem'


def test_relative_ca_file_defaults_to_config(monkeypatch):
    monkeypatch.setenv('TARGET_MQTT_CA_FILE', 'ca.pem')
    monkeypatch.delenv('CONFIG_DIR', raising=False)
    target = MQTTConfig.from_env('TARGET', config())
    assert target.tls_ca_file == '/config/ca.pem'
