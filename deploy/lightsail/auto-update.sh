#!/usr/bin/env bash
# 자동 업데이트 (타이머가 1분마다 실행): GitHub main 에 새 코드가 있으면 받아서 재시작. 알림은 오류일 때만.
# 켜기: bash deploy/lightsail/auto-update.sh enable · 끄기: ... disable · 지금 한 번: bash deploy/lightsail/auto-update.sh
set -uo pipefail
cd "$(dirname "$0")/../.."
APP_DIR="$(pwd)"
RUN_USER="$(id -un)"

if [ "${1:-}" = "enable" ]; then
  sudo tee /etc/systemd/system/dealbot-update.service >/dev/null <<UNIT
[Unit]
Description=coupang-deal-bot 자동 업데이트
[Service]
Type=oneshot
User=$RUN_USER
WorkingDirectory=$APP_DIR
ExecStart=/usr/bin/bash $APP_DIR/deploy/lightsail/auto-update.sh
UNIT
  sudo tee /etc/systemd/system/dealbot-update.timer >/dev/null <<UNIT
[Unit]
Description=coupang-deal-bot 자동 업데이트 1분마다
[Timer]
OnBootSec=2min
OnUnitActiveSec=1min
AccuracySec=10s
[Install]
WantedBy=timers.target
UNIT
  echo "$RUN_USER ALL=(root) NOPASSWD: /usr/bin/systemctl restart dealbot" | sudo tee /etc/sudoers.d/dealbot-update >/dev/null
  sudo chmod 440 /etc/sudoers.d/dealbot-update
  sudo systemctl daemon-reload
  sudo systemctl enable --now dealbot-update.timer
  echo "✅ 자동 업데이트 켬 (1분마다)"
  exit 0
fi
if [ "${1:-}" = "disable" ]; then
  sudo systemctl disable --now dealbot-update.timer
  echo "⏸ 자동 업데이트 끔"
  exit 0
fi

notify() {  # 관리자 챗으로 오류 알림 (토큰은 .env 에서 읽기만, 출력하지 않음)
  local tok chat
  tok="$(grep -E '^TELEGRAM_BOT_TOKEN=' .env | tail -1 | cut -d= -f2-)"
  chat="$(grep -E '^TELEGRAM_ADMIN_CHAT_ID=' .env | tail -1 | cut -d= -f2-)"
  [ -n "$tok" ] && [ -n "$chat" ] || return 0
  curl -s -o /dev/null --max-time 10 "https://api.telegram.org/bot$tok/sendMessage" \
    --data-urlencode "chat_id=$chat" --data-urlencode "text=⚠️ 핫딜 봇 자동 업데이트: $1" || true
}

git fetch -q origin main || exit 0
[ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] && exit 0
R_SHORT="$(git rev-parse --short origin/main)"
[ -f "var/update_failed_$R_SHORT" ] && exit 0  # 같은 버전 실패는 한 번만 알림
fail() { mkdir -p var && touch "var/update_failed_$R_SHORT"; notify "$1"; exit 1; }

git pull -q --ff-only origin main || fail "서버에서 코드가 직접 바뀌어 있어 새 코드를 못 받았어요 (git status 확인)"
.venv/bin/pip install -q -e . || fail "패키지 설치 실패"
.venv/bin/python -c "import dealbot.app" 2>/tmp/dealbot_update_err.txt \
  || fail "새 코드가 시작되지 않아요: $(tail -1 /tmp/dealbot_update_err.txt)"
sudo systemctl restart dealbot || fail "재시작 실패 (sudo systemctl status dealbot)"
sleep 20
systemctl is-active --quiet dealbot || fail "재시작 뒤 멈췄어요 (journalctl -u dealbot -n 50)"
