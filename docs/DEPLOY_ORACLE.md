# 오라클 클라우드 무료 티어 배포 (추천)

Oracle Cloud **Always Free** 는 기간 제한 없이 무료인 VM 을 제공합니다. 이 봇 하나 돌리는 데는 가장 작은 인스턴스로도 충분합니다.

- `VM.Standard.A1.Flex` (ARM, 최대 4 OCPU / 24GB — 인기가 많아 "Out of capacity" 가 자주 뜸)
- `VM.Standard.E2.1.Micro` (AMD, 1 OCPU / 1GB — 보통 바로 생성됨, 이 봇에 충분)

이미지는 두 아키텍처 모두 빌드됩니다 (GitHub Actions → `ghcr.io/deren1125/coupang-deal-bot`, `linux/amd64` + `linux/arm64`).

## 1. 계정 & 인스턴스

1. https://cloud.oracle.com 가입 (신용카드 등록 필요, 과금은 되지 않음). 홈 리전은 **Seoul (ap-seoul-1)** 또는 **Chuncheon** 권장.
2. 콘솔 → **Compute → Instances → Create instance**
   - Image: **Ubuntu 22.04** 또는 24.04 (Canonical Ubuntu, Always Free 표시 확인)
   - Shape: `VM.Standard.E2.1.Micro` (Always Free) 또는 `VM.Standard.A1.Flex` (1 OCPU / 6GB 면 충분)
   - Networking: 기본 VCN 생성, **Assign a public IPv4 address** 체크
   - SSH keys: 공개키 업로드 또는 "Generate a key pair" 로 받아 저장
3. 생성 후 **Public IP** 를 확인.

방화벽은 열 필요 없습니다 (봇은 아웃바운드만 사용).

## 2. 서버 준비

```bash
ssh ubuntu@<PUBLIC_IP>
curl -fsSL https://raw.githubusercontent.com/deren1125/coupang-deal-bot/main/deploy/oracle/bootstrap.sh | bash
```

`bootstrap.sh` 는 Docker 설치 → 저장소 clone (`~/coupang-deal-bot`) → `.env` 템플릿 생성까지 합니다. 끝나면 로그아웃 후 다시 로그인하세요 (docker 그룹 반영).

## 3. 설정 & 실행

```bash
cd ~/coupang-deal-bot
nano .env            # 키/토큰 입력 (docs/SETUP_KEYS.md)
docker compose pull  # GHCR 이미지 받기 (또는 docker compose build 로 직접 빌드)
docker compose run --rm dealbot check      # 연결 점검
docker compose up -d
docker compose logs -f --tail=100
```

`restart: unless-stopped` 라 VM 이 재부팅돼도 자동으로 다시 뜹니다. 가격 이력 DB 는 도커 볼륨 `dealbot-data` 에 저장됩니다.

## 4. 업데이트

```bash
cd ~/coupang-deal-bot && ./deploy/oracle/update.sh
```
(= `git pull` → `docker compose pull` 또는 `build` → `up -d`)

`config.yaml` / `templates/` 만 바꿀 때 이미지를 다시 안 받고 싶으면 `docker-compose.yml` 의 주석 처리된 볼륨 마운트 2줄을 켜고 `docker compose up -d` 하세요.

## 5. 운영 팁

- 상태 확인은 폰에서 `/status`. VM 에 들어갈 일은 업데이트 때뿐입니다.
- 로그 파일: `docker compose exec dealbot cat /data/logs/dealbot.log` 또는 `docker compose logs`.
- DB 백업: `docker compose cp dealbot:/data/dealbot.db ./backup-$(date +%F).db`
- 메모리 1GB 인스턴스라면 swap 을 1GB 정도 잡아 두면 안전합니다 (`bootstrap.sh` 가 자동으로 만듭니다).
- 오라클은 **Idle 인스턴스 회수 정책**이 있습니다 (7일간 CPU 사용률 극히 낮으면 회수될 수 있음). 이 봇은 주기적으로 네트워크/CPU 를 쓰므로 보통 문제없지만, 회수 안내 메일이 오면 "Upgrade to Pay As You Go"(과금 없이 정책만 해제)로 전환하는 방법이 있습니다.
- Ubuntu 자동 보안 업데이트로 재부팅될 수 있는데, 컨테이너는 자동 재시작됩니다.

## 6. 문제 해결

| 증상 | 조치 |
|---|---|
| `Out of capacity` (A1) | E2.1.Micro 로 만들거나, 다른 AD(가용 도메인)로 재시도 |
| `docker: permission denied` | 로그아웃 후 재로그인 (docker 그룹) |
| 관리자 챗에 시작 알림이 안 옴 | 봇에게 /start 를 보냈는지, `TELEGRAM_ADMIN_CHAT_ID` 가 맞는지 `docker compose run --rm dealbot check` |
| 채널에 안 올라감 | 봇이 채널 관리자인지, `TELEGRAM_CHANNEL_ID` 가 `@아이디` 또는 `-100...` 인지 |
| 뽐뿌 수집 0건 경고 | 사이트 구조 변경 → `config.yaml` 의 `list_row_selector` / `title_selector` 조정 |
