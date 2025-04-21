#!/usr/bin/with-contenv bashio

# Liest die Optionen aus
SOURCE_MQTT_HOST=$(bashio::config 'SOURCE_MQTT_HOST')
SOURCE_MQTT_PORT=$(bashio::config 'SOURCE_MQTT_PORT')
SOURCE_MQTT_TLS=$(bashio::config 'SOURCE_MQTT_TLS')
SOURCE_MQTT_USER=$(bashio::config 'SOURCE_MQTT_USER')
SOURCE_MQTT_PASS=$(bashio::config 'SOURCE_MQTT_PASS')
TARGET_MQTT_HOST=$(bashio::config 'TARGET_MQTT_HOST')
TARGET_MQTT_PORT=$(bashio::config 'TARGET_MQTT_PORT')
TARGET_MQTT_TLS=$(bashio::config 'TARGET_MQTT_TLS')
TARGET_MQTT_USER=$(bashio::config 'TARGET_MQTT_USER')
TARGET_MQTT_PASS=$(bashio::config 'TARGET_MQTT_PASS')
HA_BASE_TOPIC=$(bashio::config 'HA_BASE_TOPIC')
REGISTER_FILTER=$(bashio::config 'REGISTER_FILTER')
ACTIVATE_COMMUNICATION_GROWATT_SERVER=$(bashio::config 'ACTIVATE_COMMUNICATION_GROWATT_SERVER')
LOG_LEVEL=$(bashio::config 'LOG_LEVEL')

# Exportiere die Umgebungsvariablen
export SOURCE_MQTT_HOST
export SOURCE_MQTT_PORT
export SOURCE_MQTT_TLS
export SOURCE_MQTT_USER
export SOURCE_MQTT_PASS
export TARGET_MQTT_HOST
export TARGET_MQTT_PORT
export TARGET_MQTT_TLS
export TARGET_MQTT_USER
export TARGET_MQTT_PASS
export HA_BASE_TOPIC
export REGISTER_FILTER
export ACTIVATE_COMMUNICATION_GROWATT_SERVER
export LOG_LEVEL

# Jetzt den Python-Befehl ausführen
exec python3 ha_bridge.py
