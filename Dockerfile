# -----------------------------
# Stage 1: Build environment
# -----------------------------
FROM python:3.11-alpine as builder

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
FROM python:3.11-alpine

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Copy project and prebuilt wheels
COPY --from=builder /wheels /wheels
COPY . /app

# Install Python packages from wheel cache only
RUN pip install --no-cache-dir --no-index --find-links=/wheels grobro

# Set default command
CMD ["python", "-m", "grobro.ha_bridge"]

