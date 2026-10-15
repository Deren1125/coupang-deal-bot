# 채널 대시보드

`https://<공개 도메인>/dashboard` 에서 텔레그램·인스타그램·스레드·네이버 블로그 3개·카카오 오픈채팅·쿠팡 파트너스·핫딜 봇 운영 숫자를 한 화면에 본다 (주인 요청 10/14).
맨 위에는 주인이 할 일이 있는 카드 — `링크 기다리는 딜`(링크를 바로 넣는 칸까지)과 `발행 대기 글`(블로그 세 개) — 이 온다 (주인 요청 10/15).
카드마다 상태 칩(조건 충족 / 조건 미충족 / 판단 불가), `다시 읽기`, 읽은 시각, 숫자 칸 두 개, 최근 28일 추이, 목표 줄이 있다.

## 켜기

```bash
bash deploy/lightsail/setenv.sh DASHBOARD_USER       # 아이디
bash deploy/lightsail/setenv.sh DASHBOARD_PASSWORD   # 12자 이상, 영문·숫자·-_. 만 (예: plum-river-cactus-42)
sudo systemctl restart dealbot
```

- 비밀번호는 **12자 이상, 영문·숫자·`-` `_` `.` 만**. 공개 주소라 짧으면 맞혀 볼 수 있고, 따옴표·역슬래시·`$` 같은 기호는
  `.env`·systemd 를 거치며 바뀌어 브라우저에 넣은 값과 달라질 수 있다 (그러면 늘 401). `setenv.sh` 가 이 규칙에 안 맞으면 저장하지 않고,
  `.env` 를 직접 고쳐 규칙에 안 맞으면 시작 자기 점검에 `⚠️ … 비밀번호를 바꿔 주세요` 가 뜬다.
- 틀린 비밀번호는 한 곳에서 10분에 10번, 모두 합쳐 10분에 100번까지. 넘으면 그 10분 창이 지날 때까지 `429`(잠시 막힘)이고,
  그동안은 맞는 비밀번호도 확인하지 않는다. Caddy 뒤에서는 Caddy 가 붙인 `X-Forwarded-For` 의 실제 주소로 센다.
  실패 로그는 1분에 한 줄(횟수만, 넣은 값은 남기지 않음).
- `/dashboard` 아래 경로는 **모두** 인증을 거친다 (경로 앞부분에 건다). 나중에 이 아래에 경로를 더 붙여도 따로 인증을 달 필요가 없고,
  아이디·비밀번호가 없으면 이 아래는 전부 404.

- 둘 중 하나라도 없으면 `/dashboard` 는 404 이고, 시작 자기 점검에 `채널 대시보드 페이지: 꺼짐` 이 뜬다. 숫자 모으기는 `dashboard.enabled` 면 계속한다.
- 브라우저가 아이디·비밀번호를 물으면(HTTP 기본 인증) 넣는다. Caddy 가 HTTPS 를 붙여 준다 (`deploy/lightsail/https.sh`).
- 경로: `/dashboard`(화면), `/dashboard/refresh?src=키`(그 카드를 지금 다시 읽음), `/dashboard/input?src=키&지표=값&date=YYYY-MM-DD`(직접 입력), `/dashboard/data.json`(같은 내용 JSON),
  `POST /dashboard/link`(링크 넣기 — 아래).
  refresh·input·link 는 화면이 넣어 주는 폼 토큰(`t`)이 있어야 한다 (data.json 에는 토큰이 없다).

## 카드와 숫자 출처

| 카드 (type) | 숫자 | 출처 |
|---|---|---|
| 텔레그램 (`telegram`) | 구독자, 채널 글 오늘·7일 | Bot API `getChatMemberCount`, 봇 DB |
| 인스타그램 (`instagram`) | 팔로워, 게시물 수, 조회수·도달 28일 | Instagram Graph API (`/instatoken` 토큰). 조회수는 인사이트 권한이 있을 때만 — 없으면 `권한 필요` |
| 스레드 (`threads`) | 팔로워, 프로필 조회 28일 | Threads API `threads_insights` (`/threadstoken` 토큰, `threads_manage_insights` 권한 필요). 사용자 단위 `views` 는 **프로필 조회수**라 스레드 앱의 글 조회수보다 훨씬 작다 (글 조회수는 글마다 따로 물어야 해서 안 읽음) |
| 쿠팡 파트너스 (`coupang_partners`) | 클릭·주문·수익 (날짜별, **어제까지**) | 파트너스 리포트 API. 한 번에 3회(리포트마다 재시도 없이 1번), `min_interval_hours`(8)에 한 번·하루 `max_calls_per_day`(6)회까지, 딥링크 예약 몫은 안 씀. 첫 리포트가 실패하면 나머지는 안 부름 (4xx → 판단 불가, 429·5xx·네트워크 → 다음 시각에) |
| 링크 기다리는 딜 (`dealbot_links`) | 내 링크를 기다리는 딜 목록(새것부터 10개)·모두 몇 개·가장 오래 기다린 시간 + `링크 넣기` | 봇 DB (`awaiting_link`) |
| 발행 대기 글 (`blog_waiting`) | 블로그마다 임시저장 대기 글 수·인기 예상 수·점수 높은 글 3개 | Blog-Auto 기록 파일의 `waiting` |
| 핫딜 봇 운영 (`dealbot`) | 올린 딜 오늘·7일, 링크 기다림, 대기열 | 봇 DB |
| 네이버 블로그 (`naver_blog`) | 이웃 수·어제 방문자·게시글 수 (직접 입력), 발행·임시저장·서로이웃 신청·댓글 (자동) | 카드 입력 칸 + Blog-Auto 기록 파일 |
| 카카오 오픈채팅 (`kakao_openchat`) | 참여자 수 | 카드 입력 칸 |

- API 카드는 `dashboard.collect_times`(09:00·13:00·18:00·22:00)에 읽는다. 재시작해도 같은 시각을 두 번 읽지 않는다. `다시 읽기` 는 카드마다 10분에 한 번.
- 봇 DB·Blog-Auto 기록 카드는 화면을 열 때마다 다시 읽고, 한 시간마다 저장한다.
- 숫자는 DB `metrics` 표에 (카드 키, 지표, 날짜) 하나씩 쌓인다 (같은 날 다시 읽으면 나중 값). 증감 = 오늘 − 어제, 그래프 = 최근 28일.
  무한·NaN 같은 이상한 값은 그 줄만 버린다. 카드 하나를 그리다 터져도 그 카드만 `카드를 그리지 못함` 이 되고 페이지는 뜬다.
- 그래프: 쌓이는 수(구독자·팔로워·이웃)는 참고 화면처럼 계단 선. 날마다 오르내리는 수는 부드럽게 — 올린 딜·쿠팡 수익은 **최근 7일 합계**,
  블로그 방문자는 **7일 평균**(애드포스트 기준과 같은 셈). 기록이 짧으면(배포 첫 주) 첫 기록부터 오늘까지(최소 7일 폭)로 가로를 채운다.
- 봇 운영 카드는 딜 기록이 남아 있는 날(`app.prune_events_days` 안)만 다시 센다. 그보다 오래된 날은 저장해 둔 값을 그대로 둔다.
- `저장`·`다시 읽기` 결과 안내(저장했어요·방금 읽었어요 등)는 그 카드 안 머리 밑에 뜬다 (돌아가면 그 카드로 바로 내려감).
- 네이버 블로그 통계·방문자·이웃 목록 페이지는 robots.txt 가 막고 있어 읽지 않는다. 이웃 수·방문자는 직접 넣는다. instagram.com 페이지도 읽지 않는다 (공식 API 만).

### 네이버 통계 열기 (주인 10/15)

이웃 수·방문자는 봇이 자동으로 읽을 수 없어서(robots.txt), 블로그 카드 숫자 칸 바로 밑에 `네이버 통계 열기 ↗` 버튼과
작은 `내 블로그 ↗` 링크를 둔다. 누르면 **주인이 네이버에 로그인한 그 브라우저**에서 새 탭으로 그 블로그의 통계 페이지
(`https://admin.blog.naver.com/<blog_id>/stat/today`)·모바일 블로그(`https://m.blog.naver.com/<blog_id>`)가 열린다 —
지금 이웃 수·조회수를 그때그때 바로 확인. 서버(봇)는 이 주소를 한 번도 요청하지 않는다 (`naver_blog` 모듈에는 HTTP 클라이언트가 없음).
- 링크는 `rel="noopener noreferrer"` + 페이지의 `Referrer-Policy: no-referrer` 라 대시보드 주소가 네이버로 넘어가지 않는다.
  CSP(`default-src 'none'`)는 페이지가 읽어 오는 것만 막고 주인이 누르는 링크 이동은 막지 않는다.
- 주소는 카드의 `options.blog_id`(영문·숫자·`_`·`-` 만)로 만든다. 네이버가 통계 주소를 바꾸면
  `options.stats_url: "https://admin.blog.naver.com/{blog_id}/stat/today"` 처럼 틀을 적는다 (`{blog_id}` 자리가 채워짐, https 주소만).
  blog_id·stats_url 이 이상하면 링크를 숨기고 카드에 안내 한 줄.
- 증감(`+N 오늘`)은 그대로 — 전날 값을 넣어 둔 날만 전날 대비로 나오고, 며칠 건너뛰었으면 `10. 12. 대비` 처럼 비교한 날을 적는다.
  봇이 네이버 숫자를 못 읽으니 증감도 자동으로는 못 만든다 (넣은 값끼리의 차이).

### 링크 기다리는 딜 · 링크 넣기 (주인 10/15)

토스·네이버처럼 내 제휴 링크를 직접 만들어야 하는 핫딜을 한 카드에 모은다. 목록은 링크 요청 카톡·텔레그램 링크 요청·
고정 공지(`/pending`)·저녁 요약과 **같은 묶음** — 봇 DB 대기열의 `awaiting_link` (`storage.db.LINK_WAITING`, `Database.awaiting_items()` 기본값).
새것부터 10개(넘으면 `나머지 N개는 텔레그램 /pending`), 줄마다 상품명·몰·가격·딜 점수·언제부터 몇 시간째인지, `상품 열기 ↗`(그 상품 페이지,
https 주소만, 새 탭·`rel="noopener noreferrer"`), 그리고 링크 칸 + `링크 넣기`.

`링크 넣기` 는 텔레그램 링크 요청에 링크로 답장한 것과 **같은 길**이다:
0. 링크를 붙일 글을 찾는다 (`DealBot.owner_link_target` = `attach_link` 와 같은 `_replaced_target`). 화면을 연 뒤 링크 요청 자리가 차서
   **더 좋은 딜에 자리를 내준 글**(주인 결정 10/15)도 텔레그램 답장처럼: 유효 시간(`manual_link_ttl_hours`) 안이면 받아서 다시 올리고,
   그사이 같은 상품이 새 번호로 다시 줄 섰으면 그 글에 넣는다 (`같은 상품이 새 번호로 다시 줄 서 있어 그 딜에 링크를 넣었어요`, 텔레그램에도 새 번호를 알림 —
   그 글이 확인(/ok)을 기다리는 글이면 넣지 않고 그렇게 알려 줌). 유효 시간이 지났으면 `자리를 내준 뒤 링크 유효 시간이 지나` 라고 알려 준다.
1. 링크 칸의 글에서 첫 주소를 찾는다 (`shops.find_urls` — 텔레그램 답장과 같은 함수. 몰 앱의 공유 글을 통째로 붙여도 됨)
2. `DealBot.owner_link_problem` = `attach_link` 가 붙이기 전에 보는 `_replaced_target` → `_link_problem` 과 같은 기준으로 거른다
   (http(s) 가 아니거나 텔레그램 주소(t.me)면 붙이지 않음) — 걸리면 아무것도 부르지 않는다
3. `attach_link(번호, 주소)` 를 **한 번** 부르고, 텔레그램 고정 공지도 답장했을 때처럼 고친다
4. 대시보드로 돌아가 카드 안에 결과 한 줄 (`#12 링크를 넣었어요` 등 정해진 문구만 — 넣은 글자는 다시 보여 주지 않음)

- 지금 `awaiting_link` 가 아닌 글(이미 넣었거나 내려간 딜 — 위의 자리를 내준 글 말고)이면 아무것도 하지 않고 그렇게 알려 준다
  (두 번 눌러도 한 번만 붙음 — 자리를 내준 글을 새 번호로 넣은 뒤 또 눌러도 그 글엔 이미 링크가 있어 그대로).
- 카드 목록은 진짜 기다리는 글(`awaiting_link`)만이다. 자리를 내준 글은 목록에서 빠지지만(텔레그램 `/pending` 과 같음), 이미 화면에 떠 있던
  그 글의 `링크 넣기` 칸은 위처럼 그대로 쓸 수 있다.
- `POST` 만, 폼(`application/x-www-form-urlencoded`)만, 본문 16KB·링크 칸 2,000자까지, 다른 사이트에서 보낸 폼(`Sec-Fetch-Site: cross-site`)은 403,
  폼 토큰이 없거나 틀리면 403, 글 번호가 숫자가 아니면 400. 이 프로세스 전체에서 1분에 10번까지 (넘으면 `잠깐만요` 안내).
  CSP 는 그대로 `default-src 'none'` + `form-action 'self'` (폼은 같은 서버로만).
- 폰(390px) 기준: 칸·버튼은 40px 높이, 링크 칸 글자는 16px (아이폰이 확대하지 않게), 가로로 넘치지 않음.

### 발행 대기 글 (주인 10/15)

블로그 세 개(줄 이름은 블로그 카드 제목 — `Deren의 아카이브`·`Deren's Pick`·`오늘의 핫딜 블로그`)마다
`대기 N개 · 인기 예상 M개` 와 점수가 높은 글 3개(제목 + `82점` 알약, 기준 이상이면 보라색). 대기 글이 없으면 `대기 글 없음`.
- 숫자는 Blog-Auto 기록 파일의 `waiting` 만 본다 (아래). 딜봇은 임시저장 글을 읽지도 고치지도 않는다.
- 파일에 `waiting` 이 없으면(예전 Blog-Auto) 세 줄 모두 `판단 불가` + 회색 안내 한 줄 (오류 아님), 어떤 블로그만 빠졌으면 그 줄만 `판단 불가`.
- 파일이 없거나 `blogauto_stale_hours` 보다 오래됐으면 다른 블로그 카드처럼 `판단 불가` + 몇 시간 전 것인지. 읽은 시각은 파일의 `written_at`.
- 줄을 바꾸려면 `options.blogs: [{blog: finance, title: 아카이브}, ...]`.

### 직접 입력

블로그 카드 맨 아래 `› 직접 넣기` 를 눌러 펼친 칸에 `이웃 수`, `어제 방문자`, `게시글 수` 를 넣고 `저장` (빈 칸은 그대로 둠).
참고 화면처럼 카드가 숫자·그래프·목표로 끝나 보이게 평소엔 접혀 있고(스크립트 없이 `details`), 넣은 값이 하나도 없을 때와
잘못 넣어 돌아왔을 때(`숫자만 넣어 주세요` 등)만 펼쳐져 있다. 오픈채팅 카드도 같다. 숫자만(1,234 처럼 쉼표 가능),
`입력하는 날` 은 오늘부터 60일 전까지. 칸은 비어 있고, 지난 값은 칸 아래 `지난 값 257 · 10. 13.` 으로 보인다.
`어제 방문자` 는 **입력하는 날의 전날** 값으로 저장된다 (자정 지나 어제 방문자를 넣을 때도 날짜는 그대로 오늘).
이틀치 이웃 수가 쌓이면 옆에 `+N 오늘` 이 보인다.
애드포스트 줄:
- `게시글 수` = 주인이 넣은 게시글 수 + 그날 뒤로 Blog-Auto 가 더 발행한 수. 넣은 적이 없으면 Blog-Auto 발행 합계인데,
  봇 없이 손으로 쓴 예전 글은 Blog-Auto 가 모르므로 블로그에 보이는 전체 글 수를 한 번 넣어 두면 정확해진다
  (네이버 글 목록 API 는 robots.txt 가 막고 있어 읽지 않음).
- `일평균 방문자` 는 최근 7일(어제까지) 넣은 방문자의 평균, `개설 기간` 은 config 의 `open_date` 부터 센다.
- 자정 직후(Blog-Auto 가 다음 기록을 쓰기 전) 활동 줄은 `어제 발행 …` 으로 보이고, 맨 위 `오늘 올린 글` 에는 더하지 않는다.

## 목표 바꾸기

`config.yaml` 의 `dashboard.cards[].goals`. 이름은 카드마다:

- telegram `subscribers` · instagram `followers`, `posts` · threads `followers`
- naver_blog `open_days`, `posts`, `avg_visitors` (애드포스트 권장 90일·50개·100명)
- 적을수록 좋은 줄(`links_waiting_max` 처럼 `최대 N`)은 막대가 늘 꽉 차 있고, 충족이면 초록·넘치면 주황
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

`발행 대기 글` 카드용으로 Blog-Auto 가 더하는 키 (없어도 나머지는 그대로 읽힌다 — `version: 1` 과 기존 키는 그대로):

```json
"waiting": {"threshold": 70, "blogs": {
  "finance": {"count": 4, "popular": 1, "top": [
    {"title": "…", "score": 82, "saved_at": "2026-10-15T09:12:00+09:00", "category": "MONEY"}]},
  "issue": {"...": 0}, "hotdeal": {"...": 0}}}
```

- `count` = 봇이 네이버 임시저장으로 올려 두고 아직 발행·버림·삭제되지 않은 글 (주인이 고친 임시저장 글도 셈)
- `score` = 0~100 인기 예상, `popular` = 그중 `score >= threshold` 인 글 수, `top` = 점수 높은 순(같으면 최근 저장 순) 최대 5개,
  제목 60자, `saved_at` 은 시간대가 붙은 ISO 또는 null, `category` 는 글자 또는 null
- 세지 못한 블로그는 `blogs` 에서 빠진다 (카드의 그 줄은 `판단 불가`). 딜봇도 한 번 더 걸러서, 이상한 값은 그 글·그 블로그만 버린다.

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
