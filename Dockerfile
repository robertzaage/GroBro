ARG BUILD_FROM

# -----------------------------
# Stage 1: Build environment
# -----------------------------
FROM $BUILD_FROM AS builder

# Set environment
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install build dependencies
RUN apk add --no-cache \
    python3-dev \
    py3-pip

RUN python3 -m venv /venv

ENV PATH="/venv/bin:$PATH"
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

WORKDIR /app

# Copy project and prebuilt wheels
COPY --from=builder /wheels /wheels
COPY . /app

RUN apk add --no-cache \
    py3-pip jq
RUN python3 -m venv /venv
ENV PATH="/venv/bin:$PATH"
# Install Python packages from wheel cache only
RUN pip install --no-cache-dir --no-index --find-links=/wheels grobro

RUN chmod +x /app/entrypoint.sh
ENTRYPOINT [ "/app/entrypoint.sh" ]
# Set default command
CMD ["python", "-m", "grobro.ha_bridge"]

