FROM python:3.11-slim AS builder

ENV UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install --yes --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && mv /root/.local/bin/uv /usr/local/bin/uv

WORKDIR /app

COPY pyproject.toml README.md ./
COPY acta ./acta

RUN uv venv /opt/venv \
    && uv pip install --python /opt/venv/bin/python .


FROM python:3.11-slim

ENV PATH="/opt/venv/bin:${PATH}" \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ACTA_DEFAULT_PROVIDER=mock \
    ACTA_ENV=dev \
    ACTA_DATA_DIR=/.acta

WORKDIR /app

COPY --from=builder /opt/venv /opt/venv

EXPOSE 8765

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import json,sys,urllib.request; r=urllib.request.urlopen('http://127.0.0.1:8765/api/ready', timeout=3); d=json.load(r); sys.exit(0 if r.status == 200 and d.get('status') == 'ready' else 1)"

ENTRYPOINT ["acta"]
