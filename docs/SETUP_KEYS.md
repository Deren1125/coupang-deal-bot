# 키 발급 · 계정 준비 가이드

봇이 쓰는 값과 어디서 받는지입니다. 발급 후 `.env` 에 넣으세요. 텔레그램 3개만 있으면 봇은 돌아가고(뽐뿌 수집 + 수동 발행), 나머지는 붙이는 만큼 자동화 범위가 넓어집니다.

| 환경변수 | 어디서 | 필수 |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | @BotFather | ✅ |
| `TELEGRAM_CHANNEL_ID` | 핫딜 채널 | ✅ |
| `TELEGRAM_ADMIN_CHAT_ID` | 내 개인 챗 | ✅ (수동 링크 입력용) |
| `COUPANG_ACCESS_KEY`, `COUPANG_SECRET_KEY` | 쿠팡파트너스 → 링크 생성 → API | 쿠팡 자동화 |
| `LINKPRICE_AFFILIATE_ID` | 링크프라이스 어필리에이트 센터 | 11번가/G마켓/옥션/SSG/롯데온/알리 자동화 |
| `ADPICK_AFFID` | 애드픽 | 애드픽 핫딜 API 수집 |
| `NTFY_TOPIC` 또는 `PUSHOVER_USER_KEY`+`PUSHOVER_APP_TOKEN` | ntfy 앱 / pushover.net | 휴대폰 푸시 알림 (선택) |
| (계정만) 토스 쉐어링크, 네이버 쇼핑커넥트, 올리브영/컬리/무신사 큐레이터 | 각 앱/사이트 | 반자동 발행 |

---

## 1. 텔레그램 (필수)

1. **봇 토큰**: 텔레그램에서 @BotFather → `/newbot` → 이름, 사용자명(`..._bot`) → `123456789:AAF...` 토큰.
2. **채널**: 새 채널 만들기 → 관리자 → 관리자 추가 → 봇 검색 → **메시지 게시** 권한.
   - 공개 채널: `TELEGRAM_CHANNEL_ID=@채널아이디`
   - 비공개 채널: `-100...` 숫자 ID (아래 4번으로 확인)
3. **관리자 챗**: 내 봇을 검색해 `/start` 전송 (봇은 먼저 말을 못 걸어서 필요).
4. **ID 확인** (컴퓨터 없이): 텔레그램에서 @userinfobot 에게 아무 메시지나 보내면 내 ID(숫자)를 알려줍니다 → `TELEGRAM_ADMIN_CHAT_ID`.
   채널 ID 는 채널의 글 하나를 @userinfobot 또는 @getidsbot 에게 전달(forward)하면 `-100...` 으로 알려줍니다 → `TELEGRAM_CHANNEL_ID`.
   공개 채널이면 그냥 `@채널아이디` 를 써도 됩니다.
   (컴퓨터가 있으면 `python -m dealbot chat-id` 로도 확인 가능)

## 1-1. 휴대폰 푸시 알림 (선택, 추천)

토스/네이버 딜처럼 내가 링크를 만들어 줘야 하는 항목이 생기면 텔레그램 외에 **폰 푸시**로 따로 알립니다. 텔레그램 알림이 많아 묻히는 걸 막기 위한 용도입니다.

- **ntfy (무료)**: App Store/Play 에서 `ntfy` 설치 → 앱에서 "구독" → 토픽 이름을 남이 못 맞출 긴 문자열로 (예: `dealbot-7f3a9c2e`) → `.env` 의 `NTFY_TOPIC` 에 같은 이름. 끝.
  기본 서버 ntfy.sh 는 무료이며, 알림을 누르면 봇 챗이 열립니다.
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

## 5. 링크프라이스 (11번가 · G마켓 · 옥션 · SSG · 롯데온 · 알리 · 오늘의집)

1. https://ac.linkprice.net/join 에서 어필리에이트(무료) 가입 → 채널(텔레그램 채널 URL) 등록 → 승인.
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

## 8. 최종 확인

컴퓨터가 없어도 됩니다. 봇이 켜지면 관리자 챗으로 **자기 점검 결과**(쿠팡 API 연결, 채널 관리자 권한, 푸시 설정 등)가 옵니다. 관리자 챗에서 `/test` 를 보내면 샘플 발행 양식을 볼 수 있고, `/status` 로 상태를 봅니다.

컴퓨터가 있으면:
```bash
python -m dealbot check                 # 위 자기 점검을 콘솔에서
python -m dealbot test-post             # 관리자 챗에 샘플 발행
python -m dealbot once --dry-run        # 수집 + 판정, 발행은 로그만
```

`.env` 는 절대 git 에 올리지 마세요.
