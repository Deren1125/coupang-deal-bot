# 키 발급 가이드

봇이 필요로 하는 값은 5개입니다. 발급 후 `.env` 에 넣으세요.

| 환경변수 | 어디서 |
|---|---|
| `COUPANG_ACCESS_KEY`, `COUPANG_SECRET_KEY` | 쿠팡파트너스 → 링크 생성 → API |
| `TELEGRAM_BOT_TOKEN` | 텔레그램 @BotFather |
| `TELEGRAM_CHANNEL_ID` | 핫딜을 올릴 채널 |
| `TELEGRAM_ADMIN_CHAT_ID` | 내 개인 챗 (상태 보고용) |

---

## 1. 쿠팡파트너스 Open API 키

1. https://partners.coupang.com 접속 → 쿠팡 계정으로 로그인 → **파트너스 가입**.
   - 가입 시 활동 채널(블로그/SNS/사이트 등)을 등록해야 합니다. 텔레그램 채널 주소(`https://t.me/채널아이디`)를 등록하면 됩니다.
   - 가입 직후에는 "임시 승인" 상태입니다. 링크 생성은 바로 가능하지만, **최종 승인**은 실제 실적(구매)이 발생한 뒤 심사로 이루어집니다.
2. 상단 메뉴 **링크 생성 → API** (또는 "추가 기능 > Open API") 로 이동.
   - 최종 승인 전에는 API 메뉴가 비활성화되어 있거나 "API 키 발급 불가" 로 표시될 수 있습니다. 이 경우 먼저 수동으로 링크를 만들어 채널에 올려 실적을 만들고 최종 승인을 받아야 합니다. 정확한 조건은 파트너스 사이트 공지를 확인하세요.
3. **API 키 발급** 버튼 → `Access Key` 와 `Secret Key` 가 표시됩니다. Secret Key 는 발급 시 한 번만 보이므로 바로 복사해서 `.env` 에 넣으세요.
4. (선택) 채널별 성과를 나눠 보고 싶으면 `COUPANG_SUB_ID` 에 임의의 식별자(영문/숫자, 예: `tgdeal`)를 넣습니다. 모든 링크에 `subId` 로 붙습니다.
5. 확인: `python -m dealbot check` → `[OK] coupang api` 가 나오면 성공.

주의
- API 호출 횟수 제한이 있습니다(문서 기준으로 확인). `config.yaml` 의 `collectors[].interval_minutes` 로 조절하세요.
- 4xx 에러가 나면 `src/dealbot/coupang/client.py` 상단의 `PATH_*` 를 파트너스 API 문서의 최신 경로와 대조하세요.
- 파트너스 게시물에는 수수료 고지 문구가 필수입니다(템플릿 기본 포함).

## 2. 텔레그램 봇 토큰

1. 텔레그램에서 **@BotFather** 검색 → `/newbot`.
2. 봇 이름(표시용) → 봇 사용자명(`..._bot` 으로 끝나야 함) 입력.
3. `123456789:AAF...` 형식의 토큰을 받습니다 → `TELEGRAM_BOT_TOKEN`.
4. (선택) `/setprivacy` 는 건드릴 필요 없습니다. 봇은 채널에 글을 쓰고 개인 챗에서 명령만 받습니다.

## 3. 채널 만들기 + 봇을 관리자로 추가

1. 텔레그램 → 새 채널 만들기. 공개 채널이면 `@채널아이디` 를 정합니다 (비공개도 가능).
2. 채널 → 관리자 → 관리자 추가 → 방금 만든 봇 검색 → **메시지 게시(Post messages)** 권한 켜기.
3. `TELEGRAM_CHANNEL_ID` 값:
   - 공개 채널: `@채널아이디`
   - 비공개 채널: `-100` 으로 시작하는 숫자 ID. 아래 4단계의 `chat-id` 명령으로 확인하거나, 채널 글을 @userinfobot / @getidsbot 에게 전달해서 확인.

## 4. 관리자 챗 ID (내 개인 챗)

1. 텔레그램에서 내 봇을 검색해 **/start** 를 보냅니다 (봇은 먼저 말을 걸 수 없어서 이 단계가 꼭 필요합니다).
2. 비공개 채널 ID 도 같이 알고 싶으면 채널에 아무 글이나 하나 올립니다.
3. 로컬에서 실행:
   ```bash
   python -m dealbot chat-id
   ```
   출력 예:
   ```
   private    id=123456789       홍길동
   channel    id=-1001234567890  내 핫딜 채널
   ```
   → `TELEGRAM_ADMIN_CHAT_ID=123456789`, `TELEGRAM_CHANNEL_ID=-1001234567890`
   (봇이 `run` 중이면 업데이트를 가져올 수 없으니 잠시 중지하고 실행)

## 5. 최종 확인

```bash
python -m dealbot check         # 모든 항목 [OK] 인지
python -m dealbot test-post     # 관리자 챗에 샘플 발행 → 양식 확인
python -m dealbot test-post --to channel   # 채널에 샘플 발행 (실제로 올라가니 확인 후 삭제)
python -m dealbot once --dry-run           # 실제 수집 + 판정, 발행은 로그만
```

`.env` 는 절대 git 에 올리지 마세요 (`.gitignore` 에 포함되어 있습니다).
