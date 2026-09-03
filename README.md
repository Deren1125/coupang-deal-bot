# coupang-deal-bot

쿠팡파트너스 핫딜 자동 발행 봇. 시중가보다 저렴한 특가 상품을 자동으로 찾아 **내 파트너스 트래킹 링크**로 바꾼 뒤 **텔레그램 채널**에 올리고, 상태는 **내 개인 텔레그램 챗**으로 보고합니다. 클라우드 서버에서 24시간 무인 구동을 전제로 만들었습니다.

```
 수집기(플러그인)             가격 검증                     발행
┌──────────────────┐   ┌──────────────────────┐   ┌──────────────────────────┐
│ 쿠팡 골드박스 API │──▶│ SQLite 가격 이력 저장 │──▶│ 대기열 → 속도 제한/중복 확인 │
│ 쿠팡 카테고리 베스트│   │ (a) 표시 할인율 ≥30%  │   │ → 딥링크 변환 → 채널 발행   │
│ 뽐뿌 핫딜 크롤러   │   │ (b) 30일 평균가 -15%  │   │ → 관리자 알림              │
└──────────────────┘   └──────────────────────┘   └──────────────────────────┘
                                                        ▲ /status /queue /pause ...
                                                        └── 관리자 텔레그램 챗
```

## 주요 기능

| 요구사항 | 구현 |
|---|---|
| 수집 | `collectors/` 플러그인 구조. 내장: `coupang_goldbox`, `coupang_category_best`, `ppomppu`. config 의 `type` 에 `패키지.모듈:클래스` 를 적으면 외부 수집기도 로드 |
| 가격 검증 | 수집한 모든 상품 가격을 `price_history` 에 저장. (a) 표시 할인율 ≥ `min_discount_rate` 또는 (b) 최근 `history_days` 일 평균가 대비 `min_below_average_pct` % 이상 저렴하면 특가. 임계값은 `config.yaml` 의 `deal:` |
| 링크 변환 | 파트너스 API `deeplink` 로 변환. 타인의 `link.coupang.com/a/...` 링크는 원본 상품 URL 로 풀어서 내 링크로 재생성. API 키는 환경변수 |
| 발행 | 텔레그램 Bot API 로 채널 포스팅. 메시지 양식은 `templates/deal_post.j2` (Jinja2). 발행한 상품은 `dedup_days` 동안 재발행 금지 |
| 모니터링 | 관리자 챗으로 발행 성공/실패·에러·일일 요약 전송. `/status` `/queue` `/recent` `/errors` `/run` `/pause` `/resume` 명령 |
| 안전장치 | 시간당/일당 발행 상한, 최소 발행 간격, 크롤링 간격, 지수 백오프 재시도, 회전 로그 파일, 대기열 TTL, 헬스체크 |
| 배포 | Dockerfile + docker-compose, Railway 설정, 오라클 클라우드 부트스트랩 스크립트, GitHub Actions (테스트 + GHCR 이미지 빌드) |

## 빠른 시작 (로컬)

```bash
git clone https://github.com/deren1125/coupang-deal-bot.git && cd coupang-deal-bot
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env      # 키 발급 후 채우기 → docs/SETUP_KEYS.md
python -m dealbot render  # 메시지 양식 미리보기 (키 없이 가능)
python -m dealbot once --dry-run   # 수집 1회 + 발행 로그만 (키 없이 뽐뿌 수집기만 동작)
python -m dealbot check   # 키/토큰/채널 권한 점검
python -m dealbot run     # 24시간 상시 실행
```

키가 없어도 뽐뿌 수집기와 가격 이력 DB 는 바로 동작합니다. 쿠팡 API 키가 없으면 쿠팡 수집기와 딥링크 변환은 자동으로 꺼지고, 텔레그램 토큰이 없으면 발행은 dry-run 으로 로그에만 남습니다.

## CLI

| 명령 | 설명 |
|---|---|
| `dealbot run` | 스케줄러 + 발행 워커 + 관리자 봇 상시 실행 |
| `dealbot once [--collector 이름] [--no-publish] [--dry-run]` | 수집 1회 → 대기열 발행 → 종료 (크론/테스트용) |
| `dealbot check` | 설정, 쿠팡 API, 봇 토큰, 채널 관리자 권한, 관리자 챗 점검 |
| `dealbot chat-id` | 내 개인 챗 ID / 채널 ID 확인 도우미 |
| `dealbot render` | 샘플 딜로 템플릿 렌더링 결과 출력 |
| `dealbot test-post [--to admin|channel]` | 샘플 딜 실제 전송 (기본: 관리자 챗) |
| `dealbot status` | 현재 상태 콘솔 출력 |
| `dealbot healthcheck` | Docker HEALTHCHECK (heartbeat 확인) |

`python -m dealbot ...` 또는 설치 후 `dealbot ...` 둘 다 됩니다.

## 설정

- **비밀값** → `.env` (또는 환경변수). 항목은 `.env.example` 참고.
- **동작 설정** → `config.yaml`. 주석으로 각 항목을 설명해 두었습니다. 자주 만지는 것:
  - `deal.min_discount_rate` / `deal.min_below_average_pct` / `deal.history_days` / `deal.min_history_samples`
  - `publish.max_per_hour` / `publish.max_per_day` / `publish.min_interval_seconds` / `publish.dedup_days`
  - `collectors[].interval_minutes` / `collectors[].options`
  - `monitoring.daily_summary_time`
- **메시지 양식** → `templates/deal_post.j2`. 사용 가능한 변수는 파일 상단 주석 참고. 상태/일일 요약 양식은 `status.j2`, `daily_summary.j2`.

> 쿠팡파트너스 약관상 게시물에 **"이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다."** 문구가 필요합니다. 기본 템플릿에 들어 있으니 지우지 마세요.

## 특가 판정 로직

1. 수집기가 상품 목록을 가져오면 **모든 상품**의 가격을 `price_history` 에 기록합니다 (특가 여부와 무관).
2. 기록 직전에 최근 `history_days` 일간의 평균가를 계산합니다 (현재 관측 제외, `min_history_samples` 회 이상 관측된 경우만).
3. (a) 표시 할인율(또는 정가 대비 계산 할인율) ≥ `min_discount_rate`, (b) 평균가 대비 ≥ `min_below_average_pct` % 저렴 → 하나라도 맞으면 특가.
4. 특가는 `deal_queue` 에 점수(할인율/평균 대비 절감률 중 큰 값)순으로 쌓이고, 발행 워커가 속도 제한 안에서 하나씩 꺼내 발행합니다. `queue_ttl_hours` 안에 못 나가면 만료.

> 쿠팡 골드박스/베스트 API 응답에 표시 할인율 필드가 없는 경우가 있습니다. 그 경우 규칙 (b) 만 적용되며, 가격 이력이 며칠 쌓여야 판정이 시작됩니다. 뽐뿌 수집기는 제목의 가격만 알 수 있으므로 역시 (b) 위주로 동작합니다.

## 수집기 플러그인 만들기

```python
# my_plugins/naver.py
from dealbot.collectors import BaseCollector
from dealbot.models import Product

class NaverCollector(BaseCollector):
    requires_coupang = False          # 쿠팡 API 키 필요 여부

    async def collect(self) -> list[Product]:
        html = await self.ctx.http.get(...)   # self.ctx: settings / http / db / coupang
        return [Product(source=self.name, product_id="...", name="...", price=1000, url="https://www.coupang.com/vp/products/...")]
```

```yaml
collectors:
  - name: naver
    type: my_plugins.naver:NaverCollector
    interval_minutes: 30
    options: { foo: bar }     # self.opt("foo")
```

`product_id` 는 쿠팡 상품 ID 여야 가격 이력과 중복 방지가 상품 단위로 묶입니다. `affiliate_url` 을 비워 두면 발행 시 딥링크 API 로 변환합니다.

## 운영

- **로그**: stdout + `$DEALBOT_DATA_DIR/logs/dealbot.log` (5MB × 5 회전). Docker 에서는 `docker compose logs -f`.
- **DB**: `$DEALBOT_DATA_DIR/dealbot.db` (SQLite, WAL). 백업은 파일 복사로 충분.
- **상태 확인**: 관리자 챗에서 `/status`. 시작 시에도 상태를 보냅니다.
- **긴급 정지**: `/pause` (수집·발행 중단, 프로세스는 유지) → `/resume`.
- **재시도**: HTTP 5xx/429/네트워크 오류는 `http.max_retries` 회 지수 백오프. 발행 실패는 `publish.max_publish_attempts` 회.
- **에러 알림 폭주 방지**: 같은 종류 에러는 `error_alert_cooldown_minutes` 에 1번만.

## 배포

- Docker: `docker compose up -d --build`
- Railway: [docs/DEPLOY_RAILWAY.md](docs/DEPLOY_RAILWAY.md)
- 오라클 클라우드 무료 티어 (추천, 진짜 무료): [docs/DEPLOY_ORACLE.md](docs/DEPLOY_ORACLE.md)
- 키 발급: [docs/SETUP_KEYS.md](docs/SETUP_KEYS.md)

## 개발

```bash
pip install -e ".[dev]"
ruff check src tests
pytest -q
```

## 프로젝트 구조

```
src/dealbot/
├── cli.py               # run / once / check / chat-id / render / test-post / status / healthcheck
├── app.py               # DealBot: 파이프라인 오케스트레이터 (수집→저장→판정→대기열→발행→알림)
├── scheduler.py         # 수집 스케줄러 / 발행 워커 / 일일 요약 / 유지보수 루프
├── config.py            # config.yaml + 환경변수 → Settings (pydantic)
├── models.py            # Product / PriceStats / DealVerdict / Deal
├── links.py             # 상품 URL → 파트너스 링크 (deeplink)
├── collectors/          # 플러그인: base, registry, coupang_goldbox, coupang_category_best, ppomppu
├── coupang/             # Open API 클라이언트 + HMAC 서명
├── pricing/evaluator.py # 특가 판정 규칙
├── publisher/           # 템플릿 렌더링, 텔레그램 발행, 속도 제한
├── monitoring/          # 관리자 알림, /status 등 명령, 상태 객체
├── storage/db.py        # SQLite 스키마/저장소
└── utils/               # retry, urls, text, timeutil
templates/               # deal_post.j2 / status.j2 / daily_summary.j2
tests/                   # pytest (네트워크 없이 실행)
docs/                    # 키 발급, 배포 안내
deploy/oracle/           # 오라클 VM 부트스트랩/업데이트 스크립트
```

## 주의

- 뽐뿌 크롤링은 사이트 구조가 바뀌면 멈출 수 있습니다. 목록에서 행이 0개 매칭되면 경고 로그가 남고, `config.yaml` 의 `*_selector` 만 고치면 됩니다.
- 쿠팡파트너스 API 에는 호출 횟수 제한이 있습니다. 기본 주기(골드박스 60분, 카테고리 180분)는 보수적으로 잡았으니 문서를 확인하고 조정하세요.
- 단시간 대량 발행은 파트너스 계정 제재 위험이 있습니다. `publish.max_per_hour` 를 낮게 유지하는 것을 권장합니다.
