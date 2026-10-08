#!/usr/bin/env bash
# 자동 업데이트 (타이머가 1분마다 실행): GitHub main 에 새 코드가 있으면 받아서 점검한 뒤 재시작. 알림은 오류일 때만.
# - 봇은 시작할 때 자기 커밋을 var/running_version 에 적는다. 받은 코드가 아직 안 돌고 있으면(≠ HEAD) 될 때까지 다시 시도한다
#   (한 번 실패해도 다음 push 까지 옛 코드로 남지 않음. 서버에서 손으로 git pull 만 한 것도 다음 확인 때 재시작)
# - 재시작 전에 새 코드로 설정·템플릿을 점검(selftest.py)하고, 점검·재시작이 실패하면 돌고 있는 코드로 되돌린다
# - 봇이 발행하는 중(var/busy)이면 1분 뒤 다시. 같은 실패 알림은 커밋마다 한 번, 다시 시도는 10분마다
# 켜기: bash deploy/lightsail/auto-update.sh enable · 끄기: ... disable
# 지금 한 번 (기다리는 중이어도 바로 다시 시도): bash deploy/lightsail/auto-update.sh now
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

notify() {  # 관리자 챗으로 알림 (토큰은 .env 에서 읽기만, 출력하지 않음)
  local tok chat
  tok="$(grep -E '^TELEGRAM_BOT_TOKEN=' .env | tail -1 | cut -d= -f2-)"
  chat="$(grep -E '^TELEGRAM_ADMIN_CHAT_ID=' .env | tail -1 | cut -d= -f2-)"
  [ -n "$tok" ] && [ -n "$chat" ] || return 0
  curl -s -o /dev/null --max-time 10 "https://api.telegram.org/bot$tok/sendMessage" \
    --data-urlencode "chat_id=$chat" --data-urlencode "text=$1" || true
}

mkdir -p var
exec 9>var/auto-update.lock
if command -v flock >/dev/null; then flock -n 9 || exit 0; fi  # 타이머와 손으로 돌린 것이 겹치지 않게
MANUAL=""
{ [ "${1:-}" = "now" ] || [ -t 1 ]; } && MANUAL=1  # 사람이 직접 돌리면 기다림·실패 표식을 무시하고 바로 시도
info() { [ -z "$MANUAL" ] || echo "$*"; }

busy() { [ -n "$(find var/busy -type f -mmin -10 2>/dev/null | head -1)" ]; }  # 봇이 발행하는 중 (10분 넘은 표식은 찌꺼기)
recent() { [ -n "$(find var -maxdepth 1 -name "$1" -mmin "-$2" 2>/dev/null | head -1)" ]; }
same() { [ -n "$1" ] && [ -n "$2" ] && { [ "${1#"$2"}" != "$1" ] || [ "${2#"$1"}" != "$2" ]; }; }  # 짧은·긴 커밋 비교
pyproject_hash() { sha256sum pyproject.toml 2>/dev/null | cut -d' ' -f1; }

git fetch -q origin main || exit 0
REMOTE="$(git rev-parse origin/main)"
R_SHORT="$(git rev-parse --short "$REMOTE")"
if [ -z "$MANUAL" ]; then
  # 점검에 떨어졌거나 켜자마자 멈춘 커밋은 새 커밋이 올 때까지 다시 받지 않음
  [ -f "var/update_bad_$R_SHORT" ] && exit 0
  # 일시적인 실패(받기·설치·재시작)는 10분 뒤 다시
  recent "update_failed_$R_SHORT" 10 && exit 0
fi

OLD="$(git rev-parse HEAD)"
RUNNING="$(tr -d '[:space:]' < var/running_version 2>/dev/null || true)"  # 봇이 시작할 때 적는 자기 커밋
if [ "$OLD" = "$REMOTE" ]; then
  same "$RUNNING" "$OLD" && { info "이미 최신 코드로 돌고 있어요 ($R_SHORT)"; exit 0; }
  # 봇이 버전을 못 읽는 상태('?')면 이 코드로 한 번만 재시작
  [ "$RUNNING" = "?" ] && [ -f "var/restarted_unknown_$R_SHORT" ] && exit 0
  # 일부러 꺼 둔 봇(처음 설치 중이거나 sudo systemctl stop)은 새 커밋이 없으면 켜지 않음
  [ "$(systemctl is-active dealbot 2>/dev/null)" = inactive ] && exit 0
  # (running_version 이 없으면 = 이 기능 전 버전이 도는 중 → 한 번 재시작해서 적게 함)
fi

# 실패하면 디스크를 지금 돌고 있는 코드로 되돌린다 (옛 봇이 새 템플릿 파일을 읽지 않게)
BACK="$OLD"
if [ -n "$RUNNING" ] && [ "$RUNNING" != "?" ] && git cat-file -e "${RUNNING}^{commit}" 2>/dev/null; then
  BACK="$(git rev-parse "${RUNNING}^{commit}")"
fi
PIP_RAN=""
RESTARTED=""

fail() {  # $1 알림 내용, $2 retry(10분 뒤 다시) | bad(새 커밋이 올 때까지 안 받음). 같은 알림은 커밋마다 한 번
  local marker="var/update_failed_$R_SHORT"
  [ "${2:-retry}" = bad ] && marker="var/update_bad_$R_SHORT"
  local first=""
  [ -f "$marker" ] || first=1
  touch "$marker"
  echo "⚠️ $1"
  [ -n "$first" ] && notify "⚠️ 핫딜 봇 자동 업데이트: $1"
  exit 1
}
rollback() {  # fail 과 같지만 먼저 돌고 있던 코드로 되돌리고, 이미 재시작했으면 그 버전으로 다시 켠다
  local msg="$2"
  if [ "$(git rev-parse HEAD)" != "$BACK" ]; then
    if git reset -q --keep "$BACK"; then  # --keep: 서버에서 직접 고친 파일은 건드리지 않음
      if [ -n "$PIP_RAN" ]; then
        if .venv/bin/pip install -q -e . >/dev/null 2>&1; then pyproject_hash > var/installed_pyproject; else rm -f var/installed_pyproject; fi
      fi
    else
      msg="$msg
(이전 코드로 되돌리지 못했어요 — 서버에서 git status 확인)"
    fi
  fi
  [ -n "$RESTARTED" ] && { sudo systemctl restart dealbot || true; }
  fail "$msg" "$1"
}

# 여기부터 파일을 바꾸거나 재시작한다 → 발행 중이면 1분 뒤 다시
busy && { info "봇이 글을 올리는 중이라 1분 뒤 다시 할게요"; exit 0; }
touch var/updating  # 돌고 있는 봇이 새 글 발행을 잠깐 쉼 (재시작 뒤 새로 켜진 봇은 이 표식을 무시)
trap 'rm -f var/updating' EXIT
busy && exit 0  # 표식을 놓는 사이에 발행이 시작됐으면 다음에

if [ "$OLD" != "$REMOTE" ]; then
  if ! LC_ALL=C git merge -q --ff-only "$REMOTE" 2>var/update_err.txt; then
    # git 이 겹친다고 한 파일만 (탭으로 시작하는 줄)
    EDITED="$(grep $'^\t' var/update_err.txt | tr -d '\t' | head -5 | tr '\n' ' ' | sed 's/ *$//')"
    if [ -n "$EDITED" ] && grep -q "untracked working tree files" var/update_err.txt; then
      fail "서버에만 있는 파일($EDITED)이 새 코드와 겹쳐서 새 코드를 못 받고 있어요.
필요 없는 파일이면 서버에서 'rm $EDITED' 한 줄이면 돼요. 10분마다 다시 받아 봐요."
    elif [ -n "$EDITED" ]; then
      fail "서버에서 직접 고친 파일($EDITED) 때문에 새 코드를 못 받고 있어요.
그 수정이 필요 없으면 서버에서 'git checkout -- $EDITED' 한 줄이면 돼요. 필요한 설정이면 GitHub 쪽 파일에 같은 내용을 넣어 주세요.
10분마다 다시 받아 봐요."
    elif grep -q "fast-forward" var/update_err.txt; then
      fail "서버에만 있는 커밋이 있어서 새 코드를 그대로 받을 수 없어요. 서버에서 git log origin/main..HEAD 로 확인해 주세요. 10분마다 다시 받아 봐요."
    fi
    fail "새 코드를 못 받았어요 ($(grep -E '^(fatal|error):' var/update_err.txt | head -1)). 서버에서 git status 를 확인해 주세요. 10분마다 다시 받아 봐요."
  fi
  same "$RUNNING" "$(git rev-parse HEAD)" && exit 0
fi
HEAD_SHORT="$(git rev-parse --short HEAD)"

# 의존성(pyproject.toml)이 바뀐 때만 설치 — 코드·템플릿만 바뀐 업데이트는 PyPI 없이 끝남 (설치는 src 를 바로 씀)
if [ "$(pyproject_hash)" != "$(cat var/installed_pyproject 2>/dev/null)" ]; then
  PIP_RAN=1
  .venv/bin/pip install -q -e . 2>var/update_err.txt \
    || rollback retry "패키지 설치가 안 돼서 이번 업데이트는 미뤘어요 ($(tail -1 var/update_err.txt)). 10분마다 다시 시도해요."
  pyproject_hash > var/installed_pyproject
fi
.venv/bin/python deploy/lightsail/selftest.py >/dev/null 2>var/update_err.txt \
  || rollback bad "새 코드($HEAD_SHORT)가 점검에서 걸려서 적용하지 않았어요. 지금 버전으로 계속 돌아가요.
이유: $(tail -1 var/update_err.txt)
고친 커밋을 올리면 다시 받아요."

UNDO=""  # 켜자마자 멈추면 돌고 있던 버전으로 되돌려 켠다
[ "$BACK" != "$(git rev-parse HEAD)" ] && UNDO="이전 버전($(git rev-parse --short "$BACK"))으로 되돌려 켰어요. "
T0="$(date +%s)"
sudo systemctl restart dealbot \
  || rollback retry "재시작이 안 돼서 이번 업데이트는 미뤘어요 (sudo systemctl status dealbot). 10분마다 다시 시도해요."
RESTARTED=1
up=""
NOW_RUNNING=""
for _ in $(seq 1 30); do  # 새 봇이 시작하면서 자기 커밋을 적을 때까지 (최대 1분)
  if [ -f var/running_version ] && [ "$(stat -c %Y var/running_version)" -ge "$T0" ]; then
    NOW_RUNNING="$(tr -d '[:space:]' < var/running_version)"
    { same "$NOW_RUNNING" "$HEAD_SHORT" || [ "$NOW_RUNNING" = "?" ]; } && up=1
    break
  fi
  sleep 2
done
N1="$(systemctl show -p NRestarts --value dealbot 2>/dev/null || true)"
sleep 15
if ! systemctl is-active --quiet dealbot || [ "$(systemctl show -p NRestarts --value dealbot 2>/dev/null || true)" != "$N1" ]; then
  rollback bad "새 코드($HEAD_SHORT)로 켰더니 바로 멈췄어요. ${UNDO}이유는 서버에서 journalctl -u dealbot -n 50 로 볼 수 있어요. 고친 커밋을 올리면 다시 받아요."
fi
[ -n "$up" ] || rollback bad "새 코드($HEAD_SHORT)로 켰는데 1분이 지나도 시작 기록(var/running_version)이 안 생겨요. ${UNDO}서버에서 journalctl -u dealbot -n 50 로 확인해 주세요."
# 앞서 실패 알림을 보냈으면 해결됐다고 한 번 알려 줌 (그냥 성공은 조용히 — 봇 시작 알림에 코드 버전이 나옴)
if [ -n "$(find var -maxdepth 1 \( -name 'update_failed_*' -o -name 'update_bad_*' \) 2>/dev/null | head -1)" ]; then
  notify "✅ 핫딜 봇 자동 업데이트: 다시 시도해서 새 코드($HEAD_SHORT)로 바꿨어요."
fi
rm -f var/update_failed_* var/update_bad_* var/restarted_unknown_*
[ "$NOW_RUNNING" = "?" ] && touch "var/restarted_unknown_$HEAD_SHORT"
echo "✅ 업데이트 완료: $HEAD_SHORT $(git log -1 --format=%s | cut -c1-60)"
exit 0
