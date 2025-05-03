ARG BUILD_FROM="python:3.11-alpine"

# -----------------------------
# Stage 1: Build environment
# -----------------------------
FROM python:3.11-alpine AS builder

# Set environment
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install build dependencies
RUN apk add --no-cache \
    python3-dev \
    py3-pip

# upgrade pip an install build deps
RUN pip install --upgrade pip setuptools wheel

# Copy project
COPY . /app

# Build wheels
RUN pip wheel . --wheel-dir=/wheels

# -----------------------------
# Stage 2: Final minimal image
# -----------------------------
FROM $BUILD_FROM

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

ENV SOURCE_MQTT_HOST="$(bashio::config 'SOURCE_MQTT_HOST')" \
    SOURCE_MQTT_PORT="$(bashio::config 'SOURCE_MQTT_PORT')" \
    SOURCE_MQTT_TLS="$(bashio::config 'SOURCE_MQTT_TLS')" \
    SOURCE_MQTT_USER="$(bashio::config 'SOURCE_MQTT_USER')" \
    SOURCE_MQTT_PASS="$(bashio::config 'SOURCE_MQTT_PASS')" \
    TARGET_MQTT_HOST="$(bashio::config 'TARGET_MQTT_HOST')" \
    TARGET_MQTT_PORT="$(bashio::config 'TARGET_MQTT_PORT')" \
    TARGET_MQTT_TLS="$(bashio::config 'TARGET_MQTT_TLS')" \
    TARGET_MQTT_USER="$(bashio::config 'TARGET_MQTT_USER')" \
    TARGET_MQTT_PASS="$(bashio::config 'TARGET_MQTT_PASS')" \
    HA_BASE_TOPIC="$(bashio::config 'HA_BASE_TOPIC')" \
    REGISTER_FILTER="$(bashio::config 'REGISTER_FILTER')" \
    ACTIVATE_COMMUNICATION_GROWATT_SERVER="$(bashio::config 'ACTIVATE_COMMUNICATION_GROWATT_SERVER')" \
    LOG_LEVEL="$(bashio::config 'LOG_LEVEL')" \
    DUMP_DIR="$(bashio::config 'DUMP_DIR')" \
    DUMP_MESSAGES="$(bashio::config 'DUMP_MESSAGES')"

WORKDIR /app

# Copy project and prebuilt wheels
COPY --from=builder /wheels /wheels
COPY . /app

# Install Python packages from wheel cache only
RUN pip install --no-cache-dir --no-index --find-links=/wheels grobro

# Set default command
CMD ["python", "-m", "grobro.ha_bridge"]

