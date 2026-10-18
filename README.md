# coupang-deal-bot

쿠팡·토스쇼핑·네이버 등 여러 쇼핑몰의 **핫딜 / 특가 / 쿠폰 / 이벤트**를 자동으로 모아, 가능한 곳은 **내 제휴(수익) 링크**로 바꿔 **텔레그램 채널**에 올리고, 상태는 **내 개인 텔레그램 챗**으로 보고하는 봇입니다. 클라우드 서버에서 24시간 무인 구동을 전제로 만들었습니다.

```
 수집 (플러그인)                판정                          발행
┌───────────────────────┐  ┌────────────────────────┐  ┌────────────────────────────────┐
│ 쿠팡 골드박스/베스트 API │  │ SQLite 가격 이력 저장    │  │ 대기열 → 속도 제한/중복 확인       │
│ 뽐뿌 핫딜 (모든 쇼핑몰)  │─▶│ (a) 표시 할인율 ≥30%    │─▶│ → 링크: 자동(쿠팡·링크프라이스)     │
│ 애드픽 핫딜 API         │  │ (b) 30일 평균가 -15%    │  │          수동(토스·네이버 → 답장)  │
│ 관리자 /post 직접 입력   │  │ (c) 커뮤니티 추천 ≥5    │  │ → 채널 발행 → 관리자 알림          │
└───────────────────────┘  └────────────────────────┘  └────────────────────────────────┘
                                                              ▲ /status /pending /link /post ...
                                                              └── 관리자 텔레그램 챗
```

## 무엇을 어디서 모아서 어디에 올리나

| | 내용 |
|---|---|
| 수집원 | 쿠팡파트너스 API(골드박스, 카테고리 베스트), **뽐뿌 핫딜 게시판(쿠팡·토스·네이버·11번가·G마켓·알리·올리브영·컬리… 태그 전부)**, **루리웹 업체 핫딜 게시판(토스 딜, 카톡방보다 하루 빠름)**, 알구몬 통합 핫딜(선택), 애드픽 핫딜 API(선택), 관리자 직접 입력 |
| 판정 | **관심도 게이트**(추천/댓글/조회수/순위)를 넘은 딜만 대상으로, **(d) 쿠팡 검색으로 찾은 같은 상품보다 20%↓** 를 기본 규칙으로 판정. 대조 결과가 있는데 20% 미만이면 탈락. 대조 결과가 없을 때만 (b) 30일 평균가 대비 15%↓, (c) 추천 5↑, (a) 표시 할인율 50%↑(서브) 로 판정. 가격 없는 쿠폰/이벤트/공지 글은 기본 설정에서 올리지 않음 (`deal.accept_coupons_and_events`) |
| 링크 | 쿠팡: 파트너스 딥링크 API 자동. 11번가/G마켓/옥션/SSG/롯데온/알리/오늘의집: 링크프라이스 API 자동. 네이버: 브라우저 자동화(실험적) → 실패 시 수동. 토스/올리브영/컬리/무신사: API 가 없어 **봇이 알려주면 내가 앱에서 만든 링크를 답장** (반자동). 그 외: 원본 링크 |
| 보강 | 토스 등 상품 페이지의 OG/JSON-LD 를 읽어 이미지·가격·정상가·별점·리뷰 수를 자동으로 채움 |
| 가격 대조 | 토스·네이버 딜은 제목 가격을 상품 페이지 가격·옵션과 맞춰 봄 (`deal.page_check`, ±10%). 다른 수량 옵션 값이거나 맞는 값이 없으면 **보류** — 링크 요청 없이 관리자 챗에 "⏸ #번호 보류: …" 한 줄. 페이지를 못 읽으면 지금처럼 진행 (절대 보류 안 함). 토스 웹 페이지는 가격을 보여 주지 않아 토스는 지금은 늘 '확인 못 함'. 네이버 스토어는 일반 요청이 429 면 사진용 크롬(위장 없는 빈 창)으로 읽음 — 한 판 `browser_per_run`·한 시간 `browser_per_hour` 번까지, 한 번 `browser_timeout_seconds` 초까지, 크롬도 막히면 30→60분 쉼. 읽은 가격·옵션은 상품마다 `cache_minutes` 기억, '확인 못 함' 로그는 상품마다 하루 한 번 |
| 발행처 | **자동**: 텔레그램 채널 + 스레드(Threads API) + 인스타그램(Instagram API — 채널에 올린 딜이 인스타 대기열에 줄 서서 하루 3개까지 07~23시·2시간 간격으로 가격 정보 카드, 점심 TOP3 릴스, 저녁 '오늘 가격 내려간 딜 TOP5' 캐러셀, 화·금 생필품 1개당 가격 캐러셀. 올리기 직전 품절·가격 다시 확인, 본문 첫 줄·모든 장에 광고 고지). 봇의 공개 도메인에 최근 딜 페이지(`/`, 인스타 프로필 링크용)와 딜별 바로가기(`/d/번호`). **반자동**: 카카오 오픈채팅·네이버 블로그용 완성 문구를 관리자 챗으로 보내면 복사해서 붙여넣기. 양식은 플랫폼별 템플릿(`deal_post.j2` / `deal_threads.j2` / `deal_kakao.j2` / `deal_blog.j2`)으로 각각 수정 |
| 품절 처리 | 게시판에 품절·종료 표시가 붙으면 기다리는 글을 내리고, 보냈던 "링크 필요" 메시지를 "품절되어 취소"로 고치고, 올린 지 24시간 안의 채널 글에는 "⛔ 품절" 표시. 토스·네이버는 링크 요청 전·발행 전에 상품 페이지 재고를 확인하고 링크를 기다리는 동안 5분마다 재확인 |
| 모니터링 | 관리자 챗에 발행 성공/실패, 링크 요청, 에러, 일일 요약, 조용할 때는 30분마다 "정상 가동 중" 보고. `/status` 등 명령. 링크 요청은 **휴대폰 푸시(ntfy 무료/Pushover)** 로도 따로 알림 |

쇼핑몰별 수수료·자동화 가능 여부 정리는 [docs/SHOPS.md](docs/SHOPS.md) 를 보세요.

## 빠른 시작 (로컬)

```bash
git clone https://github.com/deren1125/coupang-deal-bot.git && cd coupang-deal-bot
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

cp .env.example .env               # 키 발급 후 채우기 → docs/SETUP_KEYS.md
python -m dealbot render           # 메시지 양식 미리보기
python -m dealbot once --dry-run   # 수집 1회 + 발행 로그만
python -m dealbot check            # 키/토큰/채널 권한/쇼핑몰 처리 방식 점검
python -m dealbot run              # 24시간 상시 실행
```

컴퓨터 없이 아이패드/휴대폰만으로도 운영 가능합니다 (Railway 웹 배포 + 텔레그램 관리자 챗). 텔레그램 값만 있어도 뽐뿌 수집 + 수동 발행이 됩니다. 쿠팡 키가 없으면 쿠팡 수집기와 딥링크가 꺼지고, 링크프라이스 ID 가 없으면 해당 몰은 원본 링크로 발행됩니다(`publish.allow_raw_links: false` 면 건너뜀).

## 발행 메시지 예

```
[토스쇼핑 첫 구매 시 3,000원 추가 할인]

상품: 애슐리 크리스피 핫도그 4종, 80g, 8개입, 2세트
가격: 14,890원
https://toss.im/_m/P4Qr1ope

이 포스팅은 토스쇼핑 쉐어링크 활동의 일환으로, 이에 따른 일정액의 수수료를 제공받습니다.
```

고지 문구는 쇼핑몰별로 자동으로 바뀝니다(`src/dealbot/shops.py` 기본값, `config.yaml` 의 `shops` 로 수정).

## 관리자 명령어 (개인 챗)

| 명령 | 설명 |
|---|---|
| `/status` | 수집기·쇼핑몰 링크 처리·발행 현황·에러 |
| `/pending` | 내가 링크를 만들어 줘야 하는 항목 목록 |
| `/link 번호 링크` 또는 요청 메시지에 답장 | 만든 제휴 링크 붙이기 → 바로 발행. 네이버 딜은 상품 사진의 설명 칸에 적어 보내면(또는 사진을 답장하며 설명 칸에 링크) 그 사진이 상품 사진 |
| `/skip 번호` | 항목 건너뛰기 |
| `/post` + 여러 줄 | 직접 딜 올리기 (`[머리글]`, `상품:`, `가격:`, 링크). 링크가 포함된 일반 메시지도 같은 동작 |
| `/test` | 샘플 딜을 관리자 챗에 보내 양식 확인 |
| `/pushtest` | 휴대폰 푸시(ntfy/Pushover) 연결 확인 |
| `/threadsauth` `/threadscode 코드` | 스레드 계정 연결 (최초 1회, 토큰은 자동 갱신) |
| `/threadstest` `/threadstest 번호` | 스레드에 샘플 글(또는 채널에 올렸던 그 글)을 실제로 올려 양식 확인 |
| `/instaauth` `/instacode 코드` `/instatest` | 인스타그램 연결 (최초 1회) · 샘플 글을 실제로 올려 카드·본문 확인 |
| `/copy [번호]` | 카카오·블로그 복붙 문구 다시 받기 |
| `/ppstats` `/hot [N]` `/find 키워드` | 커뮤니티 글 추천·조회·댓글 분포 / 추천 N개 이상 글 목록 / 상품이 어느 소스에 언제 올라왔는지 검색 |
| `/naverlogin` `/naverlink URL` `/shot URL` | 네이버 브라우저 자동화: QR 로그인 / 링크 생성 테스트 / 스크린샷 |
| `/html URL` | 페이지 원문을 파일로 받기 (알구몬·뽐뿌 구조가 바뀌었을 때 셀렉터 조정용) |
| `/queue` `/recent` `/errors` | 대기열 / 최근 발행 / 최근 에러 |
| `/run [수집기]` `/pause` `/resume` | 즉시 수집 / 일시정지 / 재개 |

## CLI

| 명령 | 설명 |
|---|---|
| `dealbot run` | 스케줄러 + 발행 워커 + 관리자 봇 상시 실행 |
| `dealbot once [--collector 이름] [--no-publish] [--dry-run]` | 수집 1회 → 대기열 발행 → 종료 |
| `dealbot check` | 설정, 쿠팡 API, 봇 토큰, 채널 권한, 관리자 챗, 쇼핑몰별 링크 처리 점검 |
| `dealbot chat-id` | 개인 챗 ID / 채널 ID 확인 |
| `dealbot render` | 샘플 딜로 템플릿 미리보기 |
| `dealbot test-post [--to admin\|channel]` | 샘플 딜 실제 전송 |
| `dealbot status` / `dealbot healthcheck` | 상태 출력 / 컨테이너 헬스체크 |

## 설정

- **비밀값** → `.env` (`.env.example` 참고)
- **동작 설정** → `config.yaml` (주석 참고). 자주 만지는 것:
  - `shops:` 쇼핑몰별 `link_mode`(api/manual/raw/skip), `enabled`
  - `deal.min_discount_rate` / `min_below_average_pct` / `history_days` / `community_min_recommend`
  - `publish.max_per_hour` / `max_per_day` / `min_interval_seconds` / `dedup_days` / `allow_raw_links`
  - `collectors[].interval_minutes`, 뽐뿌 `options.shops`(특정 몰만), `options.unknown_shop`
  - `monitoring.daily_summary_time`
- **메시지 양식** → `templates/deal_post.j2`

## 수집기 플러그인 만들기

```python
from dealbot.collectors import BaseCollector
from dealbot.models import Product
from dealbot.shops import ShopRegistry

class RuliwebCollector(BaseCollector):
    async def collect(self) -> list[Product]:
        ...
        return [Product(source=self.name, shop="toss",
                        product_id=ShopRegistry.product_key("toss", url),
                        name="...", price=14890, url=url, recommend_count=7)]
```

```yaml
collectors:
  - { name: ruliweb, type: my_plugins.ruliweb:RuliwebCollector, interval_minutes: 30 }
```

`shop` 은 `config.shops` 의 key, `product_id` 는 `ShopRegistry.product_key()` 로 만들면 가격 이력과 중복 방지가 쇼핑몰·상품 단위로 묶입니다. `affiliate_url` 을 비워 두면 발행 시 쇼핑몰 설정대로 자동/수동 변환됩니다.

## 운영

- 로그: stdout + `$DEALBOT_DATA_DIR/logs/dealbot.log` (회전). DB: `$DEALBOT_DATA_DIR/dealbot.db`.
- 재시도: HTTP 5xx/429/네트워크 오류 지수 백오프, 발행 실패 `max_publish_attempts` 회.
- 대기열: 자동 항목 `queue_ttl_hours`, 수동 링크 대기 항목 `manual_link_ttl_hours` 지나면 만료.
- 에러 알림은 종류별 `error_alert_cooldown_minutes` 에 1회.

## 배포

- Docker: `docker compose up -d --build`
- 오라클 클라우드 무료 티어(추천): [docs/DEPLOY_ORACLE.md](docs/DEPLOY_ORACLE.md)
- Railway: [docs/DEPLOY_RAILWAY.md](docs/DEPLOY_RAILWAY.md) — 빌드 없이 `ghcr.io/deren1125/coupang-deal-bot:latest` 이미지를 바로 쓰는 방법 포함
- 키 발급: [docs/SETUP_KEYS.md](docs/SETUP_KEYS.md)

## 개발

```bash
pip install -e ".[dev]" && ruff check src tests && pytest -q
```

## 프로젝트 구조

```
src/dealbot/
├── cli.py / app.py / scheduler.py     # CLI · 파이프라인 · 상시 루프
├── config.py / shops.py / models.py  # 설정 · 쇼핑몰 레지스트리 · 모델
├── links.py                          # 링크 라우터 (쿠팡 딥링크 · 링크프라이스 · 수동 · 원본)
├── manual.py                         # /post 파싱
├── collectors/                       # coupang_goldbox · coupang_category_best · ppomppu · adpick_hotdeal
├── coupang/ · pricing/ · publisher/ · monitoring/ · storage/ · utils/
templates/                            # deal_post(텔레그램) · deal_threads · deal_kakao · deal_blog · status · daily_summary
tests/ · docs/ · deploy/oracle/
```

## 사진 원칙

사진 원칙 (주인 10/18): 사진에는 주인공(인물·상품)만 남긴다 — 쇼핑몰·앱 화면 요소(화살표·썸네일 줄·버튼·상태바·검은/흰 띠·글자 띠·워터마크 띠)는 걷어내고, 주인공 둘레 배경은 조금(가로세로 8~12% 정도) 남겨 꽉 끼게 자르지 않는다. 주인공을 자르지 않는다. 확실하지 않으면 덜 자른다.

- 주인이 보낸 상품 사진(대시보드 올리기·텔레그램 답장)은 받을 때 `media/screenshot.py` `tidy` 가 다듬는다:
  휴대폰 화면 캡처면 상태바·아래 막대 → 사진 부분(쇼핑몰 사진 보기 화면이면 검은 띠·미리보기 줄·넘김 화살표까지 걷은 가운데 사진)
  → 주인공 둘레만 (여백 10%, 짧은 변은 600px 아래로 줄이지 않음). 화면 캡처가 아닌 사진도 배경이 고르면 주인공 둘레만.
- 복잡한 장면·가장자리까지 꽉 찬 사진·셔츠 위 로고처럼 애매한 것은 그대로 둔다. Blog-Auto `blogauto/media.py` 와 같은 코드 — 고칠 때는 두 곳을 같이.

## 주의

- 뽐뿌 크롤링은 사이트 구조가 바뀌면 멈춥니다. 행이 0개 매칭되면 경고가 남고 `config.yaml` 의 `*_selector` 만 고치면 됩니다.
- 쿠팡 파트너스 API 는 시간당 호출 제한이 있어(검색 기준 10회) 봇이 `coupang.max_calls_per_hour` 예산 안에서만 호출하고 딥링크 몫을 남겨 둡니다.
- 단시간 대량 발행은 제휴 계정 제재 위험이 있습니다. `publish.max_per_hour` 를 낮게 유지하세요.
- 제휴 링크가 포함된 게시물에는 고지 문구가 필요합니다. 템플릿의 `shop.disclosure` 를 지우지 마세요.
