ARG BUILD_FROM=python:3.11-alpine
FROM $BUILD_FROM

RUN apk add --no-cache python3 jq

COPY requirements.txt /tmp
ENV PATH="/venv/bin:$PATH"
RUN python3 -m venv /venv && \
    pip install --no-cache-dir -r /tmp/requirements.txt

WORKDIR /app
COPY . /app
RUN chmod +x ./run.sh

CMD [ "./run.sh" ]
