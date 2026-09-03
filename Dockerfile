# syntax=docker/dockerfile:1
FROM python:3.11-slim AS base

# WITH_BROWSER=1 이면 네이버 쇼핑커넥트 브라우저 자동화용 크로미움을 포함 (이미지 +~500MB)
ARG WITH_BROWSER=1

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Seoul \
    DEALBOT_DATA_DIR=/data \
    DEALBOT_CONFIG=/app/config.yaml \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

WORKDIR /app

COPY requirements.txt ./
RUN pip install -r requirements.txt

RUN if [ "$WITH_BROWSER" = "1" ]; then \
      pip install "playwright>=1.45" \
      && playwright install --with-deps chromium \
      && rm -rf /var/lib/apt/lists/* ; \
    fi

COPY pyproject.toml README.md ./
COPY src ./src
COPY templates ./templates
COPY config.yaml ./config.yaml
RUN pip install --no-deps .

RUN useradd --create-home --uid 1000 dealbot \
    && mkdir -p /data && chown -R dealbot:dealbot /data /app \
    && ( [ -d /ms-playwright ] && chmod -R a+rX /ms-playwright || true )
USER dealbot
VOLUME ["/data"]

HEALTHCHECK --interval=5m --timeout=20s --start-period=1m --retries=3 \
    CMD python -m dealbot healthcheck || exit 1

ENTRYPOINT ["python", "-m", "dealbot"]
CMD ["run"]
