# 주의: "# syntax=..." 지시문은 넣지 마세요. 빌더가 외부 프론트엔드 이미지를 받아오려다
# 네트워크가 제한된 환경(Railway 등)에서 즉시 실패합니다. 이 파일은 표준 문법만 씁니다.
FROM python:3.11-slim

# WITH_BROWSER=1 이면 네이버 쇼핑커넥트 브라우저 자동화용 크로미움을 포함 (이미지 +~500MB, 빌드 몇 분 추가).
# 기본은 0 — 네이버 자동화를 쓸 때만 Railway Variables 에 WITH_BROWSER=1 을 넣고 재배포하세요.
ARG WITH_BROWSER=0

# 시간대는 tzdata 파이썬 패키지(requirements.txt)로 처리하므로 apt 설치가 필요 없습니다.
# 로그 시각도 app.timezone 설정을 따릅니다(logging_setup.py). apt 단계를 없애 빌드를 가볍고 안정적으로.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Seoul \
    DEALBOT_DATA_DIR=/data \
    DEALBOT_CONFIG=/app/config.yaml \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

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
