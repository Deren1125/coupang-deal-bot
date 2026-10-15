# 채널 대시보드

`https://<공개 도메인>/dashboard` 에서 텔레그램·인스타그램·스레드·네이버 블로그 3개·카카오 오픈채팅·쿠팡 파트너스·핫딜 봇 운영 숫자를 한 화면에 본다 (주인 요청 10/14).
맨 위(요약 줄 위)에는 `지금 할 일` — 두 봇에서 주인이 지금 해야 할 일을 한 목록으로 (주인 요청 10/15, 아래 `지금 할 일`).
카드 중에서는 주인이 할 일이 있는 카드 — `링크 기다리는 딜`(링크를 바로 넣는 칸까지)과 `발행 대기 글`(블로그 세 개) — 이 먼저 온다 (주인 요청 10/15).
카드마다 상태 칩(조건 충족 / 조건 미충족 / 판단 불가), `다시 읽기`, 읽은 시각, 숫자 칸 두 개(블로그 카드는 자기 통계가 있으면 네 개),
최근 28일 추이, 목표 줄이 있다.
휴대폰(아이폰·아이패드·갤럭시) 바탕화면에 `Sns-board` 앱처럼 붙일 수 있다 (주인 요청 10/15, 아래 `휴대폰 바탕화면에 추가`).

## 켜기

```bash
bash deploy/lightsail/setenv.sh DASHBOARD_USER       # 아이디
bash deploy/lightsail/setenv.sh DASHBOARD_PASSWORD   # 12자 이상, 영문·숫자·-_. 만 (예: plum-river-cactus-42)
sudo systemctl restart dealbot
```

- 비밀번호는 **12자 이상, 영문·숫자·`-` `_` `.` 만**. 공개 주소라 짧으면 맞혀 볼 수 있고, 따옴표·역슬래시·`$` 같은 기호는
  `.env`·systemd 를 거치며 바뀌어 브라우저에 넣은 값과 달라질 수 있다 (그러면 늘 401). `setenv.sh` 가 이 규칙에 안 맞으면 저장하지 않고,
  `.env` 를 직접 고쳐 규칙에 안 맞으면 시작 자기 점검에 `⚠️ … 비밀번호를 바꿔 주세요` 가 뜬다.
- 틀린 비밀번호는 한 곳에서 10분에 10번까지 (기본 인증·로그인 화면 합쳐서). 넘으면 그곳은 10분 동안 `429`(잠시 막힘)이고,
  풀린 뒤 또 10번 틀리면 20분·40분… 으로 두 배씩 길어진다 (최대 하루, 맞는 비밀번호로 들어오거나 하루 동안 조용하면 처음부터).
  모두 합쳐 10분에 100번 틀려도 그 10분 창이 지날 때까지 모두 막는다. 막힌 동안은 맞는 비밀번호도 확인하지 않는다
  (이미 로그인해 둔 기기는 그대로 열린다). 로그인 버튼은 한 곳에서 1분에 5번까지(맞든 틀리든).
  Caddy 뒤에서는 Caddy 가 붙인 `X-Forwarded-For` 의 실제 주소로 센다. 실패 로그는 1분에 한 줄(횟수만, 넣은 값은 남기지 않음).
- `/dashboard` 아래 경로는 **모두** 로그인을 거친다 (경로 앞부분에 건다). 나중에 이 아래에 경로를 더 붙여도 따로 인증을 달 필요가 없고,
  아이디·비밀번호가 없으면 이 아래는 전부 404. 로그인 없이 열리는 건 정확히 이 경로들뿐이다:
  `/dashboard/login`, `POST /dashboard/logout`, `/dashboard/manifest.webmanifest`, `/dashboard/static/` 의 아이콘 6개
  (휴대폰이 바탕화면 아이콘을 로그인 없이 받아 가야 해서).

- 둘 중 하나라도 없으면 `/dashboard` 는 404 이고, 시작 자기 점검에 `채널 대시보드 페이지: 꺼짐` 이 뜬다. 숫자 모으기는 `dashboard.enabled` 면 계속한다.
- Caddy 가 HTTPS 를 붙여 준다 (`deploy/lightsail/https.sh`). 로그인 쿠키는 `Secure` 라 HTTPS 에서만 생긴다.
- 경로: `/dashboard`(화면), `/dashboard/refresh?src=키`(그 카드를 지금 다시 읽음), `/dashboard/input?src=키&지표=값&date=YYYY-MM-DD`(직접 입력), `/dashboard/data.json`(같은 내용 JSON — `todo` 에 지금 할 일 목록),
  `POST /dashboard/link`(링크 넣기 — 아래), `/dashboard/login`(로그인), `POST /dashboard/logout`(로그아웃).
  refresh·input·link·logout 은 화면이 넣어 주는 폼 토큰(`t`)이 있어야 한다 (data.json 에는 토큰이 없다).

### 로그인 (주인 10/15 — 바탕화면 앱으로 쓰려고)

아이폰·아이패드 바탕화면 앱은 브라우저가 묻는 아이디·비밀번호 창(HTTP 기본 인증)을 잘 못 다뤄 열 때마다 묻거나 아예 안 열린다.
그래서 로그인 화면을 따로 둔다.

- 로그인 없이 화면(`/dashboard`·refresh·input)을 열면 로그인 화면(`/dashboard/login`)으로 간다 (302).
  `data.json`·POST·그 밖의 경로는 그대로 `401`. 기본 인증을 넣었는데 틀린 요청도 `401`(브라우저가 다시 묻게).
- 로그인 화면에서 같은 `DASHBOARD_USER`·`DASHBOARD_PASSWORD` 를 넣으면 그 기기에 30일짜리 로그인 쿠키가 생긴다.
  쿠키: `dash_session`, 32바이트 난수, `Secure; HttpOnly; SameSite=Strict; Path=/dashboard`.
  DB(kv `dash:session:<쿠키 값의 SHA-256>`)에는 만료 시각과 아이디·비밀번호 지문(scrypt + 무작위 소금)만 둔다 — 쿠키 값·비밀번호는 저장하지 않는다.
- 서버에서 `DASHBOARD_PASSWORD`(또는 `DASHBOARD_USER`)를 바꾸고 재시작하면 지문이 달라져 **모든 기기가 로그아웃**된다.
  같은 값으로 재시작만 하면 그대로 로그인돼 있다.
- 화면 맨 위 오른쪽 `로그아웃` 은 그 기기의 세션만 지운다 (DB 에서 지우고 쿠키도 지움).
- 기본 인증(`Authorization: Basic`)도 그대로 받는다 — 예전 즐겨찾기, 아이디·비밀번호를 기억해 둔 브라우저, `curl -u`.
- 로그인 폼에는 따로 CSRF 토큰이 있다 (화면을 열 때 주는 난수 쿠키 `dash_login` + 그 난수를 서버 키로 서명한 값, 2시간 안).
  다른 사이트에서 보낸 폼은 `403`.
- 카카오톡·텔레그램 같은 다른 앱에서 대시보드 링크를 누르면 `SameSite=Strict` 라 로그인 쿠키가 안 실려 로그인 화면이 뜰 수 있다.
  그때는 로그인 화면의 `대시보드 열기` 를 누르면 된다 (같은 사이트 안에서 다시 열면 쿠키가 실림).

## 휴대폰 바탕화면에 추가 (주인 10/15 — "삼성 갤럭시 휴대폰이랑 아이패드, 아이폰에서도 앱처럼 바탕화면에 저 대시보드 추가할 수 있나?")

바탕화면에 `Sns-board` 라는 이름과 아이콘으로 붙고, 누르면 주소창 없이 앱처럼 열린다.
바탕화면 앱은 브라우저와 로그인을 따로 기억하므로 **추가한 뒤 처음 열 때 한 번 더 로그인**한다 (그 뒤로 30일 동안 그대로).
카카오톡·텔레그램 안에서 열린 화면에서는 추가할 수 없으니, 먼저 Safari·삼성 인터넷·Chrome 으로 연다.

**아이폰·아이패드 (Safari)**
1. Safari 로 `https://<공개 도메인>/dashboard` 를 열고 로그인한다.
2. **공유** 버튼(네모에 위쪽 화살표 — 아이폰은 화면 아래, 아이패드는 주소창 오른쪽)을 누른다.
3. 목록을 내려 **홈 화면에 추가**를 누른다. 안 보이면 맨 아래 `작업 편집…` 에서 켠다.
4. 이름이 `Sns-board` 인지 보고 오른쪽 위 **추가**. (`웹 앱으로 열기` 가 보이면 켜 둔 채로)
5. 바탕화면의 Sns-board 아이콘으로 열고, 처음 한 번 로그인한다.

**갤럭시 — 삼성 인터넷**
1. 삼성 인터넷으로 `https://<공개 도메인>/dashboard` 를 열고 로그인한다.
2. 아래쪽 **메뉴**(가로줄 세 개) → **현재 페이지 추가** → **홈 화면**.
3. 이름 `Sns-board` 를 확인하고 **추가**. (주소창에 설치 아이콘이 보이면 그걸 눌러 `설치` 해도 같다.)

**갤럭시 — Chrome**
1. Chrome 으로 `https://<공개 도메인>/dashboard` 를 열고 로그인한다.
2. 오른쪽 위 **⋮** → **홈 화면에 추가** (버전에 따라 **앱 설치**) → **설치**(또는 **추가**).
3. 바탕화면(앱 목록)에 생긴 Sns-board 아이콘으로 연다.

알아 둘 것
- 30일이 지나거나 서버 비밀번호를 바꾸면 앱을 열 때 로그인 화면이 뜬다 — 다시 넣으면 된다.
- 만든 것: `/dashboard/manifest.webmanifest`(이름 `Sns-board`, 시작 주소·범위 `/dashboard`, `standalone`, 바탕·테마 색 `#f4f5f7`),
  아이콘 `src/dealbot/dashboard/static/`(주인이 고른 파일 그대로 — 192·512 모서리 투명, 512 maskable 가장자리까지 꽉 참,
  아이폰용 180, 탭용 64·SVG), `<head>` 의 `apple-touch-icon`·`apple-mobile-web-app-*`·`theme-color`(밝은 모드 `#f4f5f7`, 어두운 모드 `#151a2d`).
  화면 `<title>` 도 `Sns-board` (화면 맨 위 제목은 설정의 `dashboard.title` 그대로).
- 아이콘 주소에는 파일 내용의 짧은 해시(`?v=`)가 붙어 1년 캐시된다. 아이콘을 바꾸면 주소가 바뀌어 새로 받지만,
  이미 바탕화면에 추가한 아이콘은 아이폰에서는 지우고 다시 추가해야 바뀐다 (안드로이드는 며칠 안에 저절로).
- 보안 헤더(CSP)는 `default-src 'none'` 그대로이고 `img-src 'self'`·`manifest-src 'self'` 만 더했다 (스크립트는 여전히 없음).

## 카드와 숫자 출처

| 카드 (type) | 숫자 | 출처 |
|---|---|---|
| 텔레그램 (`telegram`) | 구독자, 채널 글 오늘·7일, (가입 기록이 있으면) 새 구독 경로 7일 | Bot API `getChatMemberCount`, 봇 DB. 새 구독 경로는 `/invitelinks` 로 만든 이름 붙은 초대 링크별 가입 수 (chat_member 업데이트 — 공개 주소로 들어오면 '출처 모름') |
| 인스타그램 (`instagram`) | 팔로워, 게시물 수, 조회수·도달 28일 | Instagram Graph API (`/instatoken` 토큰). 조회수는 인사이트 권한이 있을 때만 — 없으면 `권한 필요` |
| 스레드 (`threads`) | 팔로워, 프로필 조회 28일 | Threads API `threads_insights` (`/threadstoken` 토큰, `threads_manage_insights` 권한 필요 — 없다고 한 번 안 토큰으로는 다시 부르지 않고 '권한 필요'로, 다시 연결하면 다시 읽음. docs/SETUP_KEYS.md 7-7). 사용자 단위 `views` 는 **프로필 조회수**라 스레드 앱의 글 조회수보다 훨씬 작다 (글 조회수는 글마다 따로 물어야 해서 안 읽음) |
| 쿠팡 파트너스 (`coupang_partners`) | 클릭·주문·수익 (날짜별, **어제까지**) | 파트너스 리포트 API. 한 번에 3회(리포트마다 재시도 없이 1번), `min_interval_hours`(8)에 한 번·하루 `max_calls_per_day`(6)회까지, 딥링크 예약 몫은 안 씀. 첫 리포트가 실패하면 나머지는 안 부름 (4xx → 판단 불가, 429·5xx·네트워크 → 다음 시각에) |
| 링크 기다리는 딜 (`dealbot_links`) | 내 링크를 기다리는 딜 목록(새것부터 10개)·모두 몇 개·가장 오래 기다린 시간 + `링크 넣기` | 봇 DB (`awaiting_link`) |
| 발행 대기 글 (`blog_waiting`) | 블로그마다 임시저장 대기 글 수·인기 예상 수·점수 높은 글 3개 | Blog-Auto 기록 파일의 `waiting` |
| 핫딜 봇 운영 (`dealbot`) | 올린 딜 오늘·7일, 링크 기다림, 대기열 | 봇 DB |
| 네이버 블로그 (`naver_blog`) | 이웃 수·방문자 오늘/전체·조회수 오늘/어제·공감·댓글·받은 서로이웃 신청 (Blog-Auto 가 하루 한 번 읽은 자기 통계, 없으면 직접 입력), 게시글 수 (직접 입력), 발행·임시저장·서로이웃 신청·댓글 (자동) | Blog-Auto 기록 파일의 `own_stats`·`blogs` + 카드 입력 칸 |
| 카카오 오픈채팅 (`kakao_openchat`) | 참여자 수 | 카드 입력 칸 |

- API 카드는 `dashboard.collect_times`(09:00·13:00·18:00·22:00)에 읽는다. 재시작해도 같은 시각을 두 번 읽지 않는다. `다시 읽기` 는 카드마다 10분에 한 번.
- 봇 DB·Blog-Auto 기록 카드는 화면을 열 때마다 다시 읽고, 한 시간마다 저장한다.
- 숫자는 DB `metrics` 표에 (카드 키, 지표, 날짜) 하나씩 쌓인다 (같은 날 다시 읽으면 나중 값). 증감 = 오늘 − 어제, 그래프 = 최근 28일.
  무한·NaN 같은 이상한 값은 그 줄만 버린다. 카드 하나를 그리다 터져도 그 카드만 `카드를 그리지 못함` 이 되고 페이지는 뜬다.
- 그래프: 쌓이는 수(구독자·팔로워·이웃)는 참고 화면처럼 계단 선. 날마다 오르내리는 수는 부드럽게 — 올린 딜·쿠팡 수익은 **최근 7일 합계**,
  블로그 방문자는 **7일 평균**(애드포스트 기준과 같은 셈). 기록이 짧으면(배포 첫 주) 첫 기록부터 오늘까지(최소 7일 폭)로 가로를 채운다.
- 봇 운영 카드는 딜 기록이 남아 있는 날(`app.prune_events_days` 안)만 다시 센다. 그보다 오래된 날은 저장해 둔 값을 그대로 둔다.
- `저장`·`다시 읽기` 결과 안내(저장했어요·방금 읽었어요 등)는 그 카드 안 머리 밑에 뜬다 (돌아가면 그 카드로 바로 내려감).
- 딜봇(서버)은 네이버 블로그 페이지를 한 번도 읽지 않는다. 블로그 자기 통계는 Blog-Auto 가 주인 결정(10/15)대로 읽어 기록 파일에 넣은 값만 본다
  (아래 `블로그 자기 통계`). instagram.com 페이지도 읽지 않는다 (공식 API 만).

### 네이버 통계 열기 (주인 10/15)

블로그 카드 숫자 칸 바로 밑에 `네이버 통계 열기 ↗` 버튼과 작은 `내 블로그 ↗` 링크를 둔다 (자기 통계를 하루 한 번만 읽으므로
지금 숫자는 여기서). 누르면 **주인이 네이버에 로그인한 그 브라우저**에서 새 탭으로 그 블로그의 통계 페이지
(`https://admin.blog.naver.com/<blog_id>/stat/today`)·모바일 블로그(`https://m.blog.naver.com/<blog_id>`)가 열린다 —
지금 이웃 수·조회수를 그때그때 바로 확인. 서버(봇)는 이 주소를 한 번도 요청하지 않는다 (`naver_blog` 모듈에는 HTTP 클라이언트가 없음).
- 링크는 `rel="noopener noreferrer"` + 페이지의 `Referrer-Policy: no-referrer` 라 대시보드 주소가 네이버로 넘어가지 않는다.
  CSP(`default-src 'none'`)는 페이지가 읽어 오는 것만 막고 주인이 누르는 링크 이동은 막지 않는다.
- 주소는 카드의 `options.blog_id`(영문·숫자·`_`·`-` 만)로 만든다. 네이버가 통계 주소를 바꾸면
  `options.stats_url: "https://admin.blog.naver.com/{blog_id}/stat/today"` 처럼 틀을 적는다 (`{blog_id}` 자리가 채워짐, https 주소만).
  blog_id·stats_url 이 이상하면 링크를 숨기고 카드에 안내 한 줄.
- 자기 통계가 없을 때(예전 Blog-Auto·아직 한 번도 못 읽음)는 예전 그대로: 숫자 칸 두 개(직접 넣은 이웃 수·어제 방문자)와
  회색 `이웃·방문자는 직접 넣은 값이에요 — 지금 숫자는 통계 열기로 확인`. 증감(`+N 오늘`)은 전날 값을 넣어 둔 날만 전날 대비,
  며칠 건너뛰었으면 `10. 12. 대비` 처럼 비교한 날을 적는다.

### 블로그 자기 통계 (주인 결정 10/15)

Blog-Auto 가 주인 블로그 세 개(`derenlog`·`derenpick`·`demiyum`)만, 블로그마다 자기 로그인 브라우저로 **하루 한 번, 날마다 다른 시각**에
자기 통계·이웃 수를 읽어 기록 파일의 `own_stats` 에 넣는다 (읽는 쪽 규칙은 Blog-Auto). 딜봇은 그 파일만 읽는다.
값이 있으면 블로그 카드 숫자 칸이 네 개가 된다 (두 칸씩 두 줄):

| 칸 | 값 | 작은 줄 |
|---|---|---|
| 이웃 수 | `neighbors` + 증감 `+1 어제 대비` (바로 앞에 쌓인 값이 어제가 아니면 `+3 10. 12. 대비`) | `받은 서로이웃 신청 2` (있을 때), 주인 값이면 `직접 입력` |
| 방문자 · 오늘 | `visitors_today` | `전체 2,751명` |
| 조회수 · 오늘 | `views_today` (하루 중간에 읽은 값) | `14:23 기준 · 공감 5 · 댓글 2` |
| 조회수 · 어제 | `yesterday.views` (있을 때만) | `공감 3 · 댓글 1 · 이웃 +1` |

- 회색 안내 줄은 `자동 · 10. 15. 14:23 읽음` (읽은 시각을 모르면 날짜만). 아침에 오늘 것을 아직 안 읽었으면 어제 읽은 값이
  `방문자 · 어제`·`조회수 · 어제` 처럼 그 날짜 이름으로 보인다 (경고 아님 — 날마다 다른 시각에 읽으니까).
- **36시간 넘게 못 읽었거나 어제를 통째로 건너뛰었으면** 숫자를 회색으로 + 주황 `어제 못 읽음`(`10. 12.부터 못 읽음`, `오늘 아직 못 읽음`).
  오래된 값은 맨 위 `오늘 늘어난 팔로워` 에 넣지 않는다.
- 이웃 수는 직접 입력과 같은 지표(`neighbors`)에 날짜별로 쌓인다 (`prev` 는 그날 값이 비어 있을 때만 채움). **그날 자동으로 읽은 뒤에**
  주인이 이웃 수를 넣었으면 주인 값(`직접 입력`)이 보이고 다시 읽어도 덮지 않는다. 읽기 전에 넣은 값은 그날 읽은 값이 덮는다.
  이웃 수만 못 읽은 날은 가장 최근 값을 `10. 14. 값` 으로.
- 맨 위 `오늘 늘어난 팔로워` 에 블로그 이웃 증감도 더한다: 오늘·어제 이웃 수가 다 있으면 그 차이, 어제 값이 없으면 통계 `일간 현황` 의
  오늘 이웃증감수(`neighbor_change_today`).
- 함께 쌓아 두는 값(그래프·나중 비교용): 전체 방문자(`own_visitors_total`), 전날 하루 조회수·공감·댓글(`own_views`·`own_likes`·`own_comments`,
  그 전날 날짜로). 하루 중간 값(오늘 조회수 등)은 쌓지 않는다.
- `data.json` 의 블로그 카드에는 `detail.own_stats`(읽은 값 그대로 + `stale`·`neighbors_from`)와 칸마다 `stale`, `stats_warn` 이 있다.
- 숫자가 하나도 없거나, 날짜(`day`)가 오늘보다 뒤면 자기 통계가 없는 것처럼(직접 입력) 그린다. 이상한 칸(숫자가 아닌 글자·음수·소수·10억 넘는 수)은 그 칸만 `-`.

### 지금 할 일 (주인 10/15 — "그거 기반으로 대시보드에 다 넣어줘 지금 해야할 일 목록 느낌으로")

페이지 맨 위(요약 줄 위) 카드. 제목에 줄 수(`지금 할 일 7`), 처음 6줄만 보이고 나머지는 `더 보기 (N개)` 를 눌러 펼친다
(스크립트 없이 `details`). 할 일이 없으면 초록 `지금 할 일 없음 ✓` 한 줄.
줄마다: 표시 칩(`⛔ 멈춤`·`🔥 핫`·`⚠️ 할 일`·`🔔 할 일`·`ℹ️ 참고`, 급하면 빨간 `급함`), 굵은 요약, `→ 어떻게`(두 줄까지),
회색 작은 줄(`블로그 이름 · 3시간 전`, 끝나는 일이면 주황 `14시간 남음`), 그리고 버튼 — https 주소면 새 탭(`rel="noopener noreferrer"`),
아니면 같은 페이지 카드로 내려가는 버튼(`링크 넣기 ↓` → `링크 기다리는 딜`, `대기 글 보기 ↓` → `발행 대기 글`).

어디서 오나:
- **Blog-Auto 기록 파일의 `todo`** (아래 모양) — Blog-Auto 알림 목록에서 아직 열린 주인 일 (보낼 목록에 있거나, 보냈는데 ttl 안에
  해결되지 않은 것 = Blog-Auto `notify.is_open` 과 같은 뜻. 딜봇이 맡긴 `link:`·`review:`·`unsure:`·`publish_broken`·`linkprice:`·`sns:` 도 여기)
  + Blog-Auto 가 셈한 상태 (블로그마다 발행 대기 글 수·인기 예상, 받은 서로이웃 신청, 내 통계 읽기 막힘(⚠️ 네이버 로그인 확인),
  콘텐츠 블로그가 7일째 새 글이 없음(ℹ️ + 걸러진 이유)). 글은 카톡 메모와 같은 주인용 문구.
- **봇 DB 지금 상태** (파일보다 새로움 — 페이지를 열 때마다 읽음):
  - 링크 기다리는 딜(`awaiting_link`) → **한 줄** `핫딜 링크 N건` (하나면 `핫딜 링크 #12` + 상품명), 가장 먼저 끝나는 것의 `N시간 남음`
    (`publish.manual_link_ttl_hours` — 대기열이 버리는 기준과 같음) + `링크 넣기 ↓`
  - 확인(/ok)을 기다리는 글(`awaiting_approval`) → `정품 확인 #N`(상품 페이지 https 면 `상품 열기 ↗`)·`정보 글 확인 #N`, 끝나는 시간도 같이
  - 채널에 올라갔는지 모르는 글(보낸 뒤 텔레그램 답 시간 초과, 최근 7일) → `채널 확인 #N` (`/posted N` · 없으면 `/link N`)
- 같은 키면 봇 DB 줄이 이긴다. 파일의 `link:`·`review:`·`unsure:` 줄은 그 글이 지금 열려 있지 않으면(이미 처리함) 버리고,
  `link:` 줄과 `link_request` 묶음은 열려 있어도 위의 한 줄에 들어 있으므로 버린다. 같은 키가 두 번이면 더 급한 것 하나.

순서: `⛔` > 급함·`🔥`·`⚠️` > 끝나는 시간이 가까운 것(링크·확인 기다림 — 먼저 끝나는 것부터) > `🔔` > `ℹ️`. 같은 칸은 오래 기다린 것부터.

- 파일이 없거나 못 읽으면 봇 DB 줄만 + 회색 `블로그 쪽 할 일을 못 읽음 — …`. 파일에 `todo` 가 없으면(예전 Blog-Auto) 같은 안내.
  `todo` 가 `blogauto_stale_hours` 보다 오래됐으면 그대로 보이고 `블로그 쪽 할 일은 N시간 전 것이에요`. 봇 DB 를 못 읽으면 파일 줄 그대로 + `딜봇 쪽 할 일을 못 읽음`.
- 버튼 주소는 https 만, 텔레그램 주소(`t.me` 등)는 그리지 않는다 (설정에 봇 이름이 없어서 — `→ 텔레그램 핫딜봇 …` 글로 어디서 할지 적음).
  따옴표·꺾쇠·`@` 가 든 주소도 버튼 없이 글만. 글은 모두 이스케이프하고, 토큰처럼 보이는 글자는 `***` 로 가린다.
- `data.json` 의 `todo`: `{title, count, items, hints, blog_generated_at, empty_text}`, 항목마다
  `key·marker·urgent·blog·blog_label·kind·short·text·how·since·age·expires_at·left·link·action·anchor·group·count·source(blogauto|dealbot)·ids`.
- 폰(390px): 카드 폭 358px 안에서 줄바꿈 (가로로 넘치지 않음), 버튼 36px·`더 보기` 40px 높이. 1100px 이상에서는 두 칸.

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
이틀치 이웃 수가 쌓이면 옆에 `+N 오늘` 이 보인다. 자기 통계가 들어오는 블로그는 그날 자동으로 읽은 **뒤에** 넣은 이웃 수만 그날 값으로 남는다
(`직접 입력` 표시 — 위 `블로그 자기 통계`).
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

블로그 카드의 자기 통계용으로 Blog-Auto 가 더하는 키 (없거나 어떤 블로그만 빠져도 나머지는 그대로 읽힌다):

```json
"own_stats": {
  "finance": {"day": "2026-10-15", "read_at": "2026-10-15T14:23:05+09:00", "neighbors": 183,
              "visitors_today": 2, "visitors_total": 2751, "views_today": 5, "likes_today": 5, "comments_today": 2,
              "neighbor_change_today": 0, "buddy_requests_pending": 2,
              "yesterday": {"day": "2026-10-14", "views": 16, "likes": 3, "comments": 1, "neighbor_change": 1},
              "prev": {"day": "2026-10-14", "neighbors": 182, "visitors_total": 2735}},
  "issue": {"...": 0}, "hotdeal": {"...": 0}}
```

- 어느 칸이든 null 이면 그 칸만 `-`. `yesterday`·`prev` 는 null 이면 없는 것, 있으면 `day` 가 읽은 날보다 앞일 때만 쓴다.
- `day` 가 없으면 `read_at` 의 날짜, 둘이 어긋나면 시각은 버리고 날짜만 믿는다. 숫자는 정수(`"2,751"` 글자도 받음), 이웃 증감(`neighbor_change_today`·`yesterday.neighbor_change`)만 음수 가능.
- 활동 기록(`written_at`)이 `blogauto_stale_hours` 보다 오래돼도 자기 통계는 자기 `read_at` 으로 따로 본다.

맨 위 `지금 할 일` 용으로 Blog-Auto 가 더하는 키 (없어도 나머지는 그대로 읽힌다):

```json
"todo": {"generated_at": "2026-10-15T14:30:05+09:00", "items": [
  {"key": "draft_ready:issue:31", "marker": "🔔[할 일]", "blog": "issue", "kind": "draft_ready", "short": "Pick #31 발행 대기",
   "text": "🔔[할 일] [Deren's Pick] #31 발행 대기 「…」\n→ 텔레그램 Deren's Pick 봇에서 🚀 발행 / 🗑 건너뛰기",
   "since": "2026-10-15T09:12:00+09:00", "urgent": false, "link": "", "group": "", "count": 1}]}
```

- `marker` 는 `⛔[멈춤]`·`🔥[핫]`·`⚠️[할 일]`·`🔔[할 일]`·`ℹ️` (모르는 값이면 글 첫머리, 그래도 없으면 🔔). `blog` 는 `finance`·`issue`·`hotdeal`·`dealbot`·빈칸.
- `short` 는 30자 안 요약, `text` 는 카톡 메모와 같은 `첫 줄 무엇\n→ 어떻게`(두 줄까지). `link` 는 https 주소 또는 빈칸 (아니면 버튼 없음).
- 비밀값은 넣지 않는다. 딜봇도 항목마다 한 번 더 걸러서 이상한 항목(글·요약이 없음)만 버린다. 키가 없으면 요약으로 키를 만든다.

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
