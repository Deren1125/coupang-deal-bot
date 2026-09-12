# 키 발급 · 계정 준비 가이드

봇이 쓰는 값과 어디서 받는지입니다. 발급 후 `.env` 에 넣으세요. 텔레그램 3개만 있으면 봇은 돌아가고(뽐뿌 수집 + 수동 발행), 나머지는 붙이는 만큼 자동화 범위가 넓어집니다.

| 환경변수 | 어디서 | 필수 |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | @BotFather | ✅ |
| `TELEGRAM_CHANNEL_ID` | 핫딜 채널 | ✅ |
| `TELEGRAM_ADMIN_CHAT_ID` | 내 개인 챗 | ✅ (수동 링크 입력용) |
| `COUPANG_ACCESS_KEY`, `COUPANG_SECRET_KEY` | 쿠팡파트너스 → 링크 생성 → API | 쿠팡 자동화 |
| `LINKPRICE_AFFILIATE_ID` | 링크프라이스 어필리에이트 센터 | 11번가/G마켓/옥션/SSG/롯데온/알리 자동화 |
| `ANTHROPIC_API_KEY` | console.anthropic.com → API Keys | 정보 글(상품 링크 없는 게시판 글)을 채널 양식으로 요약해 자동 발행 (없으면 확인 요청으로 옴) |
| `ADPICK_AFFID` | 애드픽 | 애드픽 핫딜 API 수집 |
| `NTFY_TOPIC` 또는 `PUSHOVER_USER_KEY`+`PUSHOVER_APP_TOKEN` | ntfy 앱 / pushover.net | 휴대폰 푸시 알림 (선택) |
| (계정만) 토스 쉐어링크, 네이버 쇼핑커넥트, 올리브영/컬리/무신사 큐레이터 | 각 앱/사이트 | 반자동 발행 |

---

## 1. 텔레그램 (필수)

1. **봇 토큰**: 텔레그램에서 @BotFather → `/newbot` → 이름, 사용자명(`..._bot`) → `123456789:AAF...` 토큰.
2. **채널**: 새 채널 만들기 → 관리자 → 관리자 추가 → 봇 검색 → **메시지 게시** 권한.
   - 공개 채널: `TELEGRAM_CHANNEL_ID=@hot_deal_and_info` (운영 채널: https://t.me/hot_deal_and_info)
   - 비공개 채널: `-100...` 숫자 ID (아래 4번으로 확인)
3. **관리자 챗**: 내 봇을 검색해 `/start` 전송 (봇은 먼저 말을 못 걸어서 필요).
4. **ID 확인** (컴퓨터 없이): 텔레그램에서 @userinfobot 에게 아무 메시지나 보내면 내 ID(숫자)를 알려줍니다 → `TELEGRAM_ADMIN_CHAT_ID`.
   채널 ID 는 채널의 글 하나를 @userinfobot 또는 @getidsbot 에게 전달(forward)하면 `-100...` 으로 알려줍니다 → `TELEGRAM_CHANNEL_ID`.
   공개 채널이면 그냥 `@채널아이디` 를 써도 됩니다.
   (컴퓨터가 있으면 `python -m dealbot chat-id` 로도 확인 가능)

## 1-1. 휴대폰 푸시 알림 (선택, 추천)

토스/네이버 딜처럼 내가 링크를 만들어 줘야 하는 항목이 생기면 텔레그램 외에 **폰 푸시**로 따로 알립니다. 텔레그램 알림이 많아 묻히는 걸 막기 위한 용도입니다.

## 지금 발급해 둘 키 (우선순위)

쿠팡 파트너스 승인만 나면 바로 완성되도록, 나머지 키는 미리 받아 둡니다. 모두 Railway Variables 에 넣고 Deploy 하면 봇이 자동으로 인식합니다.

| 순서 | 키 | 어디서 | 효과 |
|---|---|---|---|
| 1 | `LINKPRICE_AFFILIATE_ID` | linkprice.com 가입(무료) → 채널 심사 → 머천트별 제휴 신청 | 11번가·G마켓·옥션·SSG·롯데온·알리·오늘의집 링크가 API 로 자동 생성되고, 이 몰들이 자동으로 켜짐 |
| 2 | `THREADS_APP_ID`, `THREADS_APP_SECRET` | developers.facebook.com 앱 생성 → Threads API | 스레드 자동 게시 (`/threadsauth` 로 1회 연결) |
| 3 | 토스쇼핑 쉐어링크 API | 토스 파트너 신청 (승인 대기) | 토스 링크 자동 생성 (현재는 반자동) |
| 4 | `COUPANG_ACCESS_KEY`, `COUPANG_SECRET_KEY` | 파트너스 최종 승인 후 API 메뉴 | 쿠팡 골드박스 수집, 딥링크 자동 변환, 시중가 대조(20% 규칙) |

링크프라이스는 가입 후 **머천트마다 제휴 신청**을 따로 해야 하고, 승인이 자동인 곳과 며칠 걸리는 곳이 섞여 있습니다. 승인된 머천트의 링크만 실제로 변환됩니다.

- **ntfy (무료)**: App Store/Play 에서 `ntfy` 설치 → 앱에서 "구독" → 토픽 이름을 남이 못 맞출 긴 문자열로 (예: `dealbot-7f3a9c2e`) → `.env` 의 `NTFY_TOPIC` 에 같은 이름. 끝.
  기본 서버 ntfy.sh 는 무료이며, 알림을 누르면 봇 챗이 열립니다.
  연결 확인은 관리자 챗에서 `/pushtest`. 푸시는 기본 설정(`monitoring.push.events: [manual_link]`)에서 **내 링크가 필요할 때만** 오고, DRY-RUN 중에는 링크 요청 자체를 하지 않으므로 아무 푸시도 오지 않는 것이 정상입니다. 시작/에러/일일 요약도 받으려면 `startup`, `error`, `daily_summary` 를 추가하세요.
- **Pushover (유료, 1회 결제)**: pushover.net 가입 → User Key, Application 생성 → Token → `PUSHOVER_USER_KEY`, `PUSHOVER_APP_TOKEN`.
- 어떤 이벤트를 푸시로 받을지는 `config.yaml` 의 `monitoring.push.events` (기본: 링크 필요만).

## 2. 쿠팡파트너스 Open API

1. https://partners.coupang.com → 쿠팡 계정 로그인 → 파트너스 가입. 활동 채널에 텔레그램 채널 주소(`https://t.me/채널아이디`) 등록.
2. 가입 직후는 임시 승인. **최종 승인**은 실제 구매 실적이 생긴 뒤 심사. 최종 승인 전에는 API 메뉴가 잠겨 있을 수 있으니, 그동안은 사이트에서 수동으로 링크를 만들어 `/post` 로 올려 실적을 만드세요.
3. 상단 **링크 생성 → API** → API 키 발급 → Access Key / Secret Key (Secret 은 발급 때 한 번만 보임).
4. (선택) `COUPANG_SUB_ID` 에 채널 식별자(예: `tgdeal`).
5. 확인: `python -m dealbot check` → `[OK] coupang api`.

## 3. 토스쇼핑 쉐어링크 (앱에서만)

1. 토스 앱 → 쇼핑 탭 → 검색창에 "쉐어링크" 또는 https://sharelink.toss.im 안내대로 활동 신청 (채널 URL 등록).
2. 링크 만들기: 상품 페이지 → 공유 아이콘 → **쉐어링크 공유하기** (일반 "공유하기" 로 만든 링크는 수익 없음).
3. 봇과 연동: 봇이 `🔗 링크 필요 #12 [토스쇼핑] ...` 을 보내면 그 메시지에 **만든 링크를 답장**하거나 `/link 12 https://toss.im/_m/xxxx`.
   직접 올릴 때는 `/post` 뒤에 아래처럼:
   ```
   /post
   [토스쇼핑 첫 구매 시 3,000원 추가 할인]
   상품: 애슐리 크리스피 핫도그 4종, 80g, 8개입, 2세트
   가격: 14,890원
   https://toss.im/_m/P4Qr1ope
   ```
   수수료 고지 문구는 봇이 자동으로 붙입니다.

## 4. 네이버 쇼핑커넥트

1. https://connect.naver.com (쇼핑커넥트) 에서 활동 신청. 네이버 블로그/인플루언서 등 채널이 필요할 수 있습니다.
2. 링크 만들기: 쇼핑커넥트 사이트에서 상품 URL 붙여넣기 → 커넥트 링크 생성.
3. 봇 연동은 토스와 같습니다(답장 또는 `/link`). 귀찮으면 `config.yaml` 의 `shops` 에 `{ key: naver, link_mode: raw }` 를 넣으면 원본 링크로 자동 발행됩니다(수익 없음).

### 4-1. 브라우저 자동화로 링크 자동 생성 (실험적)

서버 안의 크로미움이 네이버에 로그인해 두고, 딜이 잡히면 쇼핑커넥트 화면에서 링크를 대신 만들어 봅니다. 실패하면 자동으로 수동 요청으로 넘어갑니다.

1. `config.yaml`: `browser.enabled: true`, `shops` 에 `- { key: naver, link_mode: api, provider: naver_connect, manual_fallback: true }` 추가 후 재시작 (Docker 이미지에 크로미움 포함).
2. 관리자 챗에서 `/naverlogin` → 봇이 네이버 로그인 QR 스크린샷을 보냄 → 네이버 앱 렌즈로 스캔 → "로그인 완료" 메시지.
3. `/naverlink https://smartstore.naver.com/.../products/123` 으로 테스트. 실패 메시지가 나오면 `/shot https://connect.naver.com/...` 스크린샷을 보고 `browser.naver_connect.create_url` 과 `selectors` 를 맞춥니다 (첫 시도는 거의 확실히 조정이 필요합니다. 스크린샷과 에러를 보내주시면 맞춰 드립니다).
4. 주의: 네이버가 자동화 접속을 감지하면 캡차나 계정 보호가 걸릴 수 있습니다. 문제가 생기면 `browser.enabled: false` 로 끄면 수동 흐름으로 돌아갑니다.

## 5. 링크프라이스 (11번가 · G마켓 · 옥션 · SSG · 롯데온 · 알리 · 오늘의집)

1. https://ac.linkprice.net/join 에서 어필리에이트 가입 → 채널(텔레그램 채널 URL) 등록 → 승인. **가입·이용은 무료**이고(광고주 쪽만 유료), 딥링크 API 는 어필리에이트에게 제공되는 기능이라 프로그램으로 호출해도 됩니다. 머천트별로 "커뮤니티/메신저 게시 금지" 같은 개별 규정이 있으니 제휴 신청 화면의 조건을 확인하세요.
2. 어필리에이트 센터에서 각 머천트(11번가, G마켓 …) **제휴 신청** → 승인된 머천트만 링크가 만들어집니다.
3. 내 **어필리에이트 ID(a_id, 예: A100xxxxx)** 를 `LINKPRICE_AFFILIATE_ID` 에 넣으면 봇이 딥링크 API 로 자동 변환합니다.
4. 첫 실행 후 관리자 챗 `/errors` 에 `linkprice` 에러가 있으면 응답 형식이 다른 것이니 알려주세요(응답 파싱은 계정 승인 후 검증 필요).

## 6. 애드픽 (선택)

1. https://adpick.co.kr 가입 → 쇼핑메이트 활동 → 내 `affid` 확인.
2. `ADPICK_AFFID` 에 넣고 `config.yaml` 의 `adpick` 수집기를 `enabled: true`.
3. 첫 실행 로그에 `adpick: N items but none parsed — first item keys: [...]` 가 나오면 그 키 목록을 알려주세요. `options.field_map` 으로 맞춥니다.

## 7. 올리브영 · 컬리 · 무신사 큐레이터 (선택, 앱에서만)

- 올리브영: 앱 → 큐레이터 활동 시작하기(심사 없음). 추천 상품 7%, 그 외 3%.
- 컬리: 앱 → 마이컬리 → 컬리 큐레이터 → 채널 URL 등록.
- 무신사: 앱 → 큐레이터 서비스.
- 셋 다 봇에서는 토스와 같은 "링크 필요" 반자동 흐름입니다.

## 7-1. 스레드(Threads) 자동 발행 (선택)

1. 스레드 계정 준비 (인스타그램 계정으로 로그인).
2. https://developers.facebook.com → 개발자 등록(무료) → **앱 만들기** → 사용 사례에서 **Threads API** 선택.
3. 앱 설정에서 **앱 ID**와 **앱 시크릿** 확인 → `.env` 의 `THREADS_APP_ID`, `THREADS_APP_SECRET`.
4. 앱의 Threads API 설정 → 리디렉션 URI 에 `https://localhost/callback` 추가 (`.env` 의 `THREADS_REDIRECT_URI` 와 같아야 함).
5. 봇 재시작 후 관리자 챗에서 `/threadsauth` → 나온 링크로 승인 → 이동한 주소창의 `code=` 뒤 값을 복사 → `/threadscode 값` 전송.
6. "연결 완료" 가 뜨면 끝. 토큰(60일)은 봇이 만료 전에 자동 갱신합니다.

## 7-3. 정보 글 (상품 링크 없는 게시판 글)

이벤트·결제 할인 공지처럼 본문에 상품 링크가 없는 게시판 글은 제휴 링크를 만들 수 없습니다. 이런 글은 `config.yaml` 의 `info_posts` 설정에 따라 **본문·사진만 정리한 정보 글**로 올립니다 (수익 링크 없음, 채널 콘텐츠용).

- 커뮤니티 추천이 `min_recommend` 이상인 글만, 하루 `max_per_day` 건까지.
- 발행 때 게시판 글을 다시 읽어 본문만 뽑고(메뉴·댓글 제외), 원문 사진이 있으면 봇이 받아서 같이 올립니다.
- **요약**: `ANTHROPIC_API_KEY` 를 넣어 두면 봇이 본문을 채널 양식(한 줄 요약 + `·` 항목 몇 줄, 담백한 안내체, `max_chars` 이내)으로 다시 써서 올립니다. 원문에 있는 사실·숫자만 쓰고, 작성자 잡담·게시판 메뉴는 뺍니다. 모델은 `info_posts.summarizer.model` 로 바꿀 수 있습니다.
- **확인(승인)**: `info_posts.review` 가 `auto`(기본)면 요약이 만들어진 글만 바로 올리고, 요약을 못 만들었거나(키 없음, API 오류) 본문 위치를 확실히 못 찾은 글은 관리자 챗에 "📝 정보 글 확인 #번호" 로 미리 보여 줍니다. `/ok 번호` 로 그대로 올리거나, 그 메시지에 **답장으로 본문을 새로 써 보내면 그 글로** 올라갑니다. `/skip 번호` 는 안 올림. `always` 면 모든 정보 글을 확인 후 올리고, `never` 면 확인 없이 올립니다. 확인을 기다리는 글은 `manual_link_ttl_hours` 가 지나면 자동으로 버립니다.
- **혜택 기준**: 브랜드 세일·이벤트 글은 할인율 `min_discount_rate`(기본 40%) 이상이거나 할인·적립 금액 `min_discount_amount`(기본 5,000원) 이상일 때만 올립니다. 요약기가 원문에서 가장 큰 혜택을 읽어 판단하고(결제 조건 금액은 제외), 요약기가 없으면 본문 숫자로 대략 판단합니다. 기준에 못 미치면 조용히 건너뛰고 `/errors` 가 아니라 로그(`info_skip`)에만 남습니다.
- 양식은 플랫폼별로 따로: 텔레그램 `info_post.j2`, 카톡 복붙 `info_kakao.j2`, 스레드 `info_threads.j2`.
- 관리자 챗에는 "내 링크 기다리는 글"과 "확인해 줘야 하는 정보 글" 목록이 고정 메시지로 붙어 있고, 요청이 생기거나 처리될 때마다 자동으로 갱신됩니다.

## 7-2. 카카오 오픈채팅 · 네이버 블로그 (복붙)

두 곳은 공식 API 가 없어 자동 발행이 불가능합니다. 대신 봇이 채널에 발행할 때마다 관리자 챗으로 **각 플랫폼 양식의 완성된 문구**를 보냅니다. 텔레그램에서 그 회색 박스를 길게 눌러 전체 복사한 뒤 오픈채팅방이나 블로그 글쓰기에 붙여넣으면 됩니다. `/copy` 로 최근 발행 건의 문구를 다시 받을 수 있고, 양식은 `templates/deal_kakao.j2`, `templates/deal_blog.j2` 에서 수정합니다. 블로그 문구는 첫 줄이 제목입니다.

**하루 정리 블로그 글.** 매일 밤 `blog_digest.time`(기본 21:30)에 그날 채널에 올라간 딜(정보 글 포함)을 모아 **블로그 글 한 편**을 만들어 관리자 챗으로 보냅니다. 첫 줄이 제목이고, 딜마다 가격·할인율·시중가 대비·리뷰·구매 링크가 들어가며, 마지막에 채널 안내와 쇼핑몰별 고지 문구가 붙습니다. 태그는 별도 상자로 옵니다. `/blog` 를 보내면 지금까지 모인 것으로 미리 만들어 볼 수 있습니다(이때는 기준 시각을 옮기지 않아 밤에 다시 전체가 옵니다). 양식은 `templates/blog_daily.j2`.

**내 채널끼리 연결하기.** `config.yaml` 의 `channels` 에 주소를 넣으면 글 하단에 서로 안내하는 줄이 붙습니다. 비워 두면 그 줄은 빠집니다.

| 키 | 어디에 쓰이나 |
|---|---|
| `kakao_openchat_url` | 텔레그램 채널 글 하단 "💬 카톡 오픈채팅: …", 스레드 답글(텔레그램 주소가 없을 때) |
| `telegram_url` | 카톡 복붙 문구 하단 "📲 실시간 전체 딜(텔레그램): …", 스레드 답글 |
| `threads_url` | 아직 템플릿에서 쓰지 않음 (프로필 소개용으로 보관) |

## 8. 최종 확인

컴퓨터가 없어도 됩니다. 봇이 켜지면 관리자 챗으로 **자기 점검 결과**(쿠팡 API 연결, 채널 관리자 권한, 푸시 설정 등)가 옵니다. 관리자 챗에서 `/test` 를 보내면 샘플 발행 양식을 볼 수 있고, `/status` 로 상태를 봅니다.

컴퓨터가 있으면:
```bash
python -m dealbot check                 # 위 자기 점검을 콘솔에서
python -m dealbot test-post             # 관리자 챗에 샘플 발행
python -m dealbot once --dry-run        # 수집 + 판정, 발행은 로그만
```

`.env` 는 절대 git 에 올리지 마세요.
