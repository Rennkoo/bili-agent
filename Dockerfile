FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src

ARG BILI_AGENT_EXTRAS=asr,vision
RUN python -m pip install --upgrade pip \
    && if [ -n "$BILI_AGENT_EXTRAS" ]; then python -m pip install ".[${BILI_AGENT_EXTRAS}]"; else python -m pip install .; fi

EXPOSE 8765
VOLUME ["/data"]

CMD ["bili-agent", "web", "--host", "0.0.0.0", "--port", "8765"]
