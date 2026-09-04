# Railway 배포

Railway 는 GitHub 저장소를 연결하면 Dockerfile 로 자동 빌드·배포해 주는 PaaS 입니다. 가장 손쉽지만 **무료 티어가 아닙니다** — 가입 시 1회성 크레딧($5 상당 트라이얼)만 제공되고 이후는 Hobby 플랜(월 $5, 사용량 포함)이 필요합니다. 이 봇은 리소스를 거의 안 쓰므로 월 $5 안에서 충분히 돌아가지만, 완전 무료를 원하면 [오라클 클라우드](DEPLOY_ORACLE.md) 를 쓰세요.

저장소에 `railway.json` (Dockerfile 빌더, 재시작 정책 ALWAYS) 이 포함되어 있습니다.

## 아이패드/휴대폰만 있어도 됩니다

Railway 는 브라우저만으로 배포·설정·로그 확인이 다 되므로 컴퓨터가 없어도 운영할 수 있습니다. 터미널이 필요한 단계는 없습니다.
- 코드: GitHub 저장소를 브라우저에서 연결 (수정은 GitHub 웹 편집기나 Claude Code 로)
- 설정: Railway Variables 화면에 키 입력
- 확인: 봇이 켜지면 텔레그램 관리자 챗으로 자기 점검 결과가 오고, `/status` `/test` 로 확인

## 권장: 미리 빌드된 이미지로 배포 (Railway 빌드 우회)

Railway 빌더가 이미지를 못 만드는 경우가 있습니다(빌드가 몇 초 만에 "Failed to build an image" 로 끝남).
이 저장소는 **GitHub Actions 가 push 마다 이미지를 빌드해 GHCR 에 올리므로**, Railway 는 빌드 없이 그 이미지를
받아서 실행만 하면 됩니다. 더 빠르고, 빌드 실패 가능성이 사라집니다.

이미지: `ghcr.io/deren1125/coupang-deal-bot:latest` (공개, 인증 불필요, amd64/arm64)

### 이미 GitHub 저장소로 서비스를 만들었다면

1. 서비스 → **Settings** → **Source** → **Disconnect** (GitHub 저장소 연결 해제)
2. 같은 자리에 이미지 입력란이 나오면 `ghcr.io/deren1125/coupang-deal-bot:latest` 입력
3. **Deploy**

입력란이 안 보이면 서비스를 새로 만드는 게 빠릅니다.

### 새 서비스로 만들 때

1. 프로젝트 화면에서 **+ Create** (또는 New) → **Docker Image**
2. `ghcr.io/deren1125/coupang-deal-bot:latest` 입력
3. **Variables** 와 **Volume(`/data`)** 을 아래 절차대로 다시 설정
4. Start command 는 비워 두세요 (이미지에 이미 들어 있습니다)

### 코드가 바뀌었을 때

`main` 에 push → GitHub Actions 가 3~4분 안에 새 이미지를 올림 → Railway 서비스에서 **Deploy**(또는 ⋮ → Redeploy)
를 누르면 새 이미지를 받습니다. 자동 배포를 원하면 Railway 의 이미지 감시 기능을 켜거나, 아래 GitHub 저장소 방식을
쓰세요.

---

## GitHub 저장소에서 직접 빌드하는 방식

## 절차

1. https://railway.app 가입 (GitHub 로그인 권장).
2. **New Project → Deploy from GitHub repo** → `deren1125/coupang-deal-bot` 선택 → 브랜치 선택.
3. **Settings → Source → Branch** 를 `claude/coupang-hotdeal-bot-l0gr26` 로 바꿉니다 (기본은 main 이라 코드가 없습니다).
4. 서비스가 만들어지면 **Variables** 탭에서 아래를 추가:
   ```
   TELEGRAM_BOT_TOKEN=123456789:AAF...
   TELEGRAM_CHANNEL_ID=@hot_deal_and_info
   TELEGRAM_ADMIN_CHAT_ID=123456789
   NTFY_TOPIC=내토픽이름
   TZ=Asia/Seoul
   DEALBOT_DATA_DIR=/data
   DEALBOT_DRY_RUN=true
   ```
   승인 후 추가할 것: `COUPANG_ACCESS_KEY`, `COUPANG_SECRET_KEY`, `LINKPRICE_AFFILIATE_ID`, `THREADS_APP_ID`, `THREADS_APP_SECRET`.
   네이버 브라우저 자동화를 켤 때만 `WITH_BROWSER=1` 도 추가(이미지에 크로미움 포함).
5. **Volume 추가 (중요)**: 서비스 → Settings → Volumes → **Add Volume** → Mount path `/data`.
   볼륨이 없으면 재배포 때마다 SQLite 가격 이력이 사라집니다.
6. Deploy. 로그 탭에서 `DealBot started` 가 보이면 정상. 관리자 챗에 시작 알림이 옵니다.
7. 설정(`config.yaml`)이나 템플릿을 바꾸면 git push → 자동 재배포.

## 확인

- Railway 로그: `Deployments → View logs`
- 텔레그램: `/status`

## 참고

- Railway 는 아웃바운드 IP 가 고정되지 않습니다. 쿠팡 API 는 IP 제한이 없지만, 뽐뿌가 특정 IP 대역을 차단하면 수집이 실패할 수 있습니다(관리자 챗에 에러 알림).
- Hobby 플랜에서 sleep 정책이 걸리지 않도록 서비스 설정의 "App Sleeping" 이 꺼져 있는지 확인하세요.
