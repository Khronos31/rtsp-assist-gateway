FROM python:3.11-slim-bookworm

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /tmp/requirements.txt
RUN python3 -m pip install --no-cache-dir -r /tmp/requirements.txt
RUN python3 -c "from pysilero_vad import SileroVoiceActivityDetector; SileroVoiceActivityDetector()"

WORKDIR /app
COPY gateway /app/gateway
COPY run.sh /app/run.sh
RUN chmod 0755 /app/run.sh

CMD ["/app/run.sh"]
