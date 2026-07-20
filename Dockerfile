# Container image for the Claude Code usage-telemetry receiver.
# Stdlib-only — no pip install needed.
FROM python:3.12-slim

WORKDIR /app
COPY billing/ ./billing/

# Persist the store + request log on a mounted volume, not in the image.
ENV OTEL_DB=/data/otel.db \
    RECEIVER_LOG=/data/receiver.log \
    PYTHONUNBUFFERED=1

VOLUME ["/data"]
EXPOSE 4318

# Bind to all interfaces inside the container (the host/proxy controls exposure).
CMD ["python", "-m", "billing.otel.receiver", "--host", "0.0.0.0", "--port", "4318"]
