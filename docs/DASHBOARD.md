# 채널 대시보드

`https://<공개 도메인>/dashboard` 에서 텔레그램·인스타그램·스레드·네이버 블로그 3개·카카오 오픈채팅·쿠팡 파트너스·핫딜 봇 운영 숫자를 한 화면에 본다 (주인 요청 10/14).
카드마다 상태 칩(조건 충족 / 조건 미충족 / 판단 불가), `다시 읽기`, 읽은 시각, 숫자 칸 두 개, 최근 28일 추이, 목표 줄이 있다.

## 켜기

```bash
bash deploy/lightsail/setenv.sh DASHBOARD_USER       # 아이디
bash deploy/lightsail/setenv.sh DASHBOARD_PASSWORD   # 긴 비밀번호
sudo systemctl restart dealbot
```

- 둘 중 하나라도 없으면 `/dashboard` 는 404 이고, 시작 자기 점검에 `채널 대시보드 페이지: 꺼짐` 이 뜬다. 숫자 모으기는 `dashboard.enabled` 면 계속한다.
- 브라우저가 아이디·비밀번호를 물으면(HTTP 기본 인증) 넣는다. Caddy 가 HTTPS 를 붙여 준다 (`deploy/lightsail/https.sh`).
- 경로: `/dashboard`(화면), `/dashboard/refresh?src=키`(그 카드를 지금 다시 읽음), `/dashboard/input?src=키&지표=값&date=YYYY-MM-DD`(직접 입력), `/dashboard/data.json`(같은 내용 JSON).
  refresh·input 은 화면이 넣어 주는 폼 토큰(`t`)이 있어야 한다.

## 카드와 숫자 출처

| 카드 (type) | 숫자 | 출처 |
|---|---|---|
| 텔레그램 (`telegram`) | 구독자, 채널 글 오늘·7일 | Bot API `getChatMemberCount`, 봇 DB |
| 인스타그램 (`instagram`) | 팔로워, 게시물 수, 조회수·도달 28일 | Instagram Graph API (`/instatoken` 토큰). 조회수는 인사이트 권한이 있을 때만 — 없으면 `권한 필요` |
| 스레드 (`threads`) | 팔로워, 조회수 28일 | Threads API `threads_insights` (`/threadstoken` 토큰, `threads_manage_insights` 권한 필요) |
| 쿠팡 파트너스 (`coupang_partners`) | 클릭·주문·수익 (날짜별) | 파트너스 리포트 API. 한 번에 3회, `min_interval_hours`(8)에 한 번·하루 `max_calls_per_day`(6)회까지, 딥링크 예약 몫은 안 씀 |
| 핫딜 봇 운영 (`dealbot`) | 올린 딜 오늘·7일, 링크 기다림, 대기열 | 봇 DB |
| 네이버 블로그 (`naver_blog`) | 이웃 수·어제 방문자 (직접 입력), 발행·임시저장·서로이웃 신청·댓글 (자동) | 카드 입력 칸 + Blog-Auto 기록 파일 |
| 카카오 오픈채팅 (`kakao_openchat`) | 참여자 수 | 카드 입력 칸 |

- API 카드는 `dashboard.collect_times`(09:00·13:00·18:00·22:00)에 읽는다. 재시작해도 같은 시각을 두 번 읽지 않는다. `다시 읽기` 는 카드마다 10분에 한 번.
- 봇 DB·Blog-Auto 기록 카드는 화면을 열 때마다 다시 읽고, 한 시간마다 저장한다.
- 숫자는 DB `metrics` 표에 (카드 키, 지표, 날짜) 하나씩 쌓인다 (같은 날 다시 읽으면 나중 값). 증감 = 오늘 − 어제, 그래프 = 최근 28일.
- 네이버 블로그 통계·방문자·이웃 목록 페이지는 robots.txt 가 막고 있어 읽지 않는다. 이웃 수·방문자는 직접 넣는다. instagram.com 페이지도 읽지 않는다 (공식 API 만).

### 직접 입력

블로그 카드 아래 칸에 `이웃 수`, `어제 방문자` 를 넣고 `저장`. 숫자만(1,234 처럼 쉼표 가능), 날짜는 오늘부터 60일 전까지.
`어제 방문자` 는 고른 날짜의 전날 값으로 저장된다. 이틀치 이웃 수가 쌓이면 옆에 `+N 오늘` 이 보인다.
애드포스트 줄의 `일평균 방문자` 는 최근 7일(어제까지) 넣은 방문자의 평균, `개설 기간` 은 config 의 `open_date` 부터 센다.

## 목표 바꾸기

`config.yaml` 의 `dashboard.cards[].goals`. 이름은 카드마다:

- telegram `subscribers` · instagram `followers`, `posts` · threads `followers`
- naver_blog `open_days`, `posts`, `avg_visitors` (애드포스트 권장 90일·50개·100명)
- kakao_openchat `members` · coupang_partners `commission_month`, `orders_month` · dealbot `deals_per_day`, `links_waiting_max`(이하가 충족)

목표 이름을 빼면 그 줄이 없어진다. 블로그 개설일은 `options.open_date: 2026-09-01` 처럼 적는다.

## Blog-Auto 기록 파일

Blog-Auto 가 한 시간마다 자기 DB 만 보고 `~/Blog-Auto/var/metrics/blogauto.json` 을 통째로 바꿔 쓴다 (원자적 쓰기, 네이버 요청 없음).
딜봇은 `dashboard.blogauto_metrics_path` 에서 읽고, 없거나 `blogauto_stale_hours`(3시간)보다 오래됐으면 블로그 활동을 `판단 불가` 로 둔다.

```json
{
  "written_at": "2026-10-14T18:00:05+09:00",
  "blogs": {
    "finance": {
      "posts_published_today": 1, "posts_published_total": 23, "drafts_saved_today": 2,
      "neighbor_requests_today": 10, "neighbor_requests_7d": 55,
      "comments_today": 3, "replies_today": 2
    },
    "issue": {"...": 0},
    "hotdeal": {"...": 0}
  }
}
```

블로그 키는 Blog-Auto 의 블로그 이름(`finance`·`issue`·`hotdeal`) 또는 `blog_id`. 중첩(`{"posts": {"published_today": 1}}`)이나
목록(`[{"name": "finance", ...}]`)도 읽힌다 (`src/dealbot/dashboard/blogauto.py` 의 `FIELD_ALIASES`). 숫자가 아닌 값은 무시한다.

## 새 SNS 붙이기 (모듈 하나 + 설정 한 줄)

1. `src/dealbot/dashboard/sources/<이름>.py` — 폴더 안 모듈은 자동으로 읽힌다.

```python
from dealbot.dashboard import icons
from dealbot.dashboard.model import ERROR, OK, CardView, FetchResult, GoalRow, Stat, safe_error
from dealbot.dashboard.sources.base import CardData, Source, register


@register
class YouTubeSource(Source):
    type = "youtube"              # config 의 type
    kind = "api"                  # api | export | internal | manual
    default_title = "YouTube"
    icon = icons.GENERIC          # 또는 브랜드 색 SVG 를 icons.py 에
    program = "YouTube 파트너 프로그램"
    default_goals = {"subscribers": 1000}

    async def fetch(self) -> FetchResult:   # 공식 API 만, 예외 대신 상태로
        try:
            n = ...                          # self.ctx 의 http 클라이언트·토큰
        except Exception as e:
            return FetchResult(ERROR, note=f"못 읽음 ({safe_error(e)})")
        return FetchResult(OK, values={"subscribers": n})

    def card(self, data: CardData) -> CardView:
        subs = self.stat_with_delta(data, "subscribers", "구독자", "명")
        return self.view(data, stats=[subs, Stat("조회수 · 최근 28일", data.latest_value("views_28d"), "회")],
                         spark=data.series("subscribers"), spark_label="구독자",
                         rows=[GoalRow("구독자 수", subs.value, self.goal("subscribers") or 1000, "명")])
```

2. `config.yaml` 의 `dashboard.cards` 에 `- { key: youtube, type: youtube, title: YouTube, goals: { subscribers: 1000 } }`.
3. 테스트: `tests/test_dashboard.py` 처럼 가짜 응답(httpx.MockTransport)으로 정상·권한 없음·네트워크 오류.

지킬 것: 공식 API 만 (스크래핑·브라우저 위장 없음), 토큰·비밀값은 화면·JSON·`note` 에 넣지 않기 (`safe_error` 가 토큰 모양을 가림),
바깥 호출은 `fetch()` 에서만 (정해진 시각·다시 읽기 때), `live()` 는 DB·로컬 파일처럼 싼 것만.
