FROM python:3.11-slim AS builder
WORKDIR /build
COPY pyproject.toml .
COPY app/ app/
RUN pip install --no-cache-dir -e .

FROM python:3.11-slim
WORKDIR /app

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY app/ app/
COPY agent_tracker/ agent_tracker/
COPY agent_results/ agent_results/
COPY config.yaml config.yaml

# Default: API/UI server. Cloud providers such as Render set PORT.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
