ARG BUILD_FROM
FROM $BUILD_FROM

# Installiere Python und pip
RUN apk add --no-cache python3 py3-pip

# Erstelle ein virtuelles Environment
RUN python3 -m venv /venv

# Setze das Environment aktiv
ENV PATH="/venv/bin:$PATH"

WORKDIR /app

# Kopiere requirements.txt
COPY grobro/requirements.txt ./requirements.txt

# Installiere die Anforderungen in der virtuellen Umgebung
RUN pip install --no-cache-dir -r requirements.txt

WORKDIR /app/grobro
COPY grobro/ /app/grobro/
COPY run.sh /
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
