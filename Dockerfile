FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PUID=1026 \
    PGID=100 \
    TZ=Europe/Warsaw \
    SCANMAIL_CONFIG=/config/config.yaml \
    SCANMAIL_STATE_DIR=/state

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY scanmail/ ./scanmail/

ENTRYPOINT ["python", "-m", "scanmail.docker_entrypoint"]
