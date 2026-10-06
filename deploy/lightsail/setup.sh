#!/usr/bin/env bash
# Lightsail(Ubuntu) 에 Docker 없이 설치: 파이썬 가상환경 + systemd 서비스 + 1분 자동 업데이트.
#   처음:      cd ~ && git clone https://github.com/Deren1125/coupang-deal-bot.git && cd coupang-deal-bot && bash deploy/lightsail/setup.sh
#   다시 실행: 코드·패키지만 갱신하고 서비스를 재시작한다 (.env 는 그대로).
# 키 입력은 bash deploy/lightsail/setenv.sh 이름  (화면에 안 보임, 아이패드에서도 됨)
set -euo pipefail
cd "$(dirname "$0")/../.."
APP_DIR="$(pwd)"
RUN_USER="$(id -un)"
DATA_DIR="${DEALBOT_DATA_DIR:-$HOME/dealbot-data}"

echo "▶ 1/5 패키지 설치"
PY=""
for c in python3.13 python3.12 python3.11; do command -v "$c" >/dev/null && PY="$c" && break; done
if [ -z "$PY" ]; then
  # Ubuntu 22.04 기본 파이썬(3.10)은 너무 낮음 → 3.11 설치
  sudo apt-get update -q
  sudo apt-get install -y software-properties-common
  sudo add-apt-repository -y ppa:deadsnakes/ppa
  sudo apt-get update -q
  sudo apt-get install -y python3.11 python3.11-venv
  PY=python3.11
fi
sudo apt-get install -y "${PY}-venv" git fonts-nanum >/dev/null 2>&1 || sudo apt-get install -y python3-venv git fonts-nanum

echo "▶ 2/5 가상환경 ($PY)"
[ -x .venv/bin/python ] || "$PY" -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -e .

echo "▶ 3/5 데이터 폴더 $DATA_DIR"
mkdir -p "$DATA_DIR"
touch .env && chmod 600 .env
if ! grep -q '^DEALBOT_DATA_DIR=' .env; then
  {
    echo "DEALBOT_DATA_DIR=$DATA_DIR"
    echo "TZ=Asia/Seoul"
    echo "DEALBOT_DRY_RUN=true"   # 처음엔 연습 모드 (채널에 안 올리고 관리자 챗에만 미리보기)
  } >> .env
fi

echo "▶ 4/5 서비스 등록 (dealbot)"
sudo tee /etc/systemd/system/dealbot.service >/dev/null <<UNIT
[Unit]
Description=coupang-deal-bot (핫딜 봇)
After=network-online.target
Wants=network-online.target
[Service]
User=$RUN_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$APP_DIR/.venv/bin/python -m dealbot run
Restart=always
RestartSec=15
[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable dealbot >/dev/null

echo "▶ 5/5 자동 업데이트 (1분마다 GitHub 확인)"
bash deploy/lightsail/auto-update.sh enable

missing=""
for k in TELEGRAM_BOT_TOKEN TELEGRAM_CHANNEL_ID TELEGRAM_ADMIN_CHAT_ID COUPANG_ACCESS_KEY COUPANG_SECRET_KEY; do
  grep -qE "^$k=.+" .env || missing="$missing $k"
done
if [ -n "$missing" ]; then
  echo
  echo "⚠️ 아직 없는 키:$missing"
  echo "   하나씩 넣기: bash deploy/lightsail/setenv.sh TELEGRAM_BOT_TOKEN   (값은 화면에 안 보임)"
  echo "   다 넣은 뒤:   sudo systemctl restart dealbot"
  exit 0
fi
sudo systemctl restart dealbot
sleep 5
systemctl is-active --quiet dealbot && echo "✅ 핫딜 봇 실행 중 (연습 모드면 관리자 챗으로 미리보기만 갑니다)" \
  || echo "⚠️ 시작 실패 — journalctl -u dealbot -n 50 확인"
echo "점검: .venv/bin/python -m dealbot check   · 로그: journalctl -u dealbot -f"
