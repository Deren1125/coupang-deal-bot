#!/usr/bin/env bash
# 대화형 설정: 값을 하나씩 물어보고 .env 를 만든 뒤 봇을 띄운다.
# 터미널 편집기(nano/vi)를 쓸 필요가 없어 아이패드/휴대폰 SSH 에서도 편하다.
#   사용: cd ~/coupang-deal-bot && ./deploy/oracle/setup.sh
set -euo pipefail
cd "$(dirname "$0")/../.."

ENV_FILE=".env"
BOLD=$'\033[1m'; DIM=$'\033[2m'; GREEN=$'\033[32m'; YELLOW=$'\033[33m'; RESET=$'\033[0m'

echo "${BOLD}핫딜 봇 설정${RESET}"
echo "${DIM}값을 붙여넣고 엔터. 건너뛰려면 그냥 엔터를 누르세요.${RESET}"
echo

# 기존 값 읽기 (다시 실행할 때 현재 값을 기본값으로)
declare -A CURRENT=()
if [ -f "$ENV_FILE" ]; then
  while IFS='=' read -r k v; do
    [[ "$k" =~ ^[A-Z_]+$ ]] && CURRENT["$k"]="$v"
  done < <(grep -E '^[A-Z_]+=' "$ENV_FILE" || true)
fi

declare -A VALUES=()

ask() {  # ask KEY "설명" [required]
  local key="$1" desc="$2" required="${3:-}" cur="${CURRENT[$1]:-}" shown="" input=""
  if [ -n "$cur" ]; then
    if [[ "$key" == *TOKEN* || "$key" == *SECRET* || "$key" == *KEY* ]]; then
      shown=" ${DIM}(현재: ${cur:0:6}...)${RESET}"
    else
      shown=" ${DIM}(현재: $cur)${RESET}"
    fi
  fi
  printf '%s%s\n' "${BOLD}$desc${RESET}" "$shown"
  read -r -p "> " input || true
  input="${input:-$cur}"
  if [ -z "$input" ] && [ -n "$required" ]; then
    echo "${YELLOW}필수 항목입니다. 다시 입력해 주세요.${RESET}"
    ask "$@"
    return
  fi
  VALUES["$key"]="$input"
  echo
}

echo "${BOLD}[1/3] 텔레그램 (필수)${RESET}"
ask TELEGRAM_BOT_TOKEN "봇 토큰 (BotFather 가 준 123456789:AAF... 형식)" required
ask TELEGRAM_CHANNEL_ID "핫딜을 올릴 채널 (@채널아이디 또는 -100으로 시작하는 숫자)" required
ask TELEGRAM_ADMIN_CHAT_ID "내 개인 챗 ID (@userinfobot 이 알려준 숫자)" required

echo "${BOLD}[2/3] 휴대폰 푸시 (선택, 엔터로 건너뛰기)${RESET}"
ask NTFY_TOPIC "ntfy 토픽 이름 (앱에서 구독한 그 이름)"

echo "${BOLD}[3/3] 제휴 키 (승인 후 넣으면 됩니다. 지금은 엔터로 건너뛰기)${RESET}"
ask COUPANG_ACCESS_KEY "쿠팡파트너스 Access Key"
ask COUPANG_SECRET_KEY "쿠팡파트너스 Secret Key"
ask LINKPRICE_AFFILIATE_ID "링크프라이스 어필리에이트 ID (A100... 형식)"
ask THREADS_APP_ID "스레드 앱 ID"
ask THREADS_APP_SECRET "스레드 앱 시크릿"

DEFAULT_DRY="${CURRENT[DEALBOT_DRY_RUN]:-true}"
echo "${BOLD}연습 모드로 시작할까요?${RESET} ${DIM}(y = 채널에 올리지 않고 관리자 챗에만 미리보기, 현재: $DEFAULT_DRY)${RESET}"
read -r -p "> [Y/n] " dry || true
case "${dry:-}" in
  [nN]*) VALUES[DEALBOT_DRY_RUN]="false" ;;
  "")    VALUES[DEALBOT_DRY_RUN]="$DEFAULT_DRY" ;;
  *)     VALUES[DEALBOT_DRY_RUN]="true" ;;
esac
echo

if [ -f "$ENV_FILE" ]; then
  cp "$ENV_FILE" "$ENV_FILE.bak.$(date +%s)"
  echo "${DIM}기존 .env 를 백업했습니다.${RESET}"
fi

{
  echo "# coupang-deal-bot 설정 — $(date '+%Y-%m-%d %H:%M')"
  echo "# 이 파일은 setup.sh 가 만듭니다. 다시 실행하면 값을 바꿀 수 있습니다."
  for key in TELEGRAM_BOT_TOKEN TELEGRAM_CHANNEL_ID TELEGRAM_ADMIN_CHAT_ID \
             NTFY_TOPIC COUPANG_ACCESS_KEY COUPANG_SECRET_KEY LINKPRICE_AFFILIATE_ID \
             THREADS_APP_ID THREADS_APP_SECRET DEALBOT_DRY_RUN; do
    echo "$key=${VALUES[$key]:-}"
  done
  echo "THREADS_REDIRECT_URI=${CURRENT[THREADS_REDIRECT_URI]:-https://localhost/callback}"
  echo "TZ=Asia/Seoul"
  echo "DEALBOT_DATA_DIR=/data"
} > "$ENV_FILE"
chmod 600 "$ENV_FILE"
echo "${GREEN}설정 저장 완료${RESET} ($ENV_FILE)"
echo

echo "${BOLD}연결 점검 중...${RESET}"
if docker compose run --rm dealbot check; then
  echo
else
  echo "${YELLOW}점검에서 문제가 나왔습니다. 위 [FAIL] 항목을 확인하세요. 그래도 계속 진행합니다.${RESET}"
  echo
fi

echo "${BOLD}봇을 시작합니다...${RESET}"
docker compose up -d
echo
docker compose ps
echo
echo "${GREEN}완료!${RESET} 관리자 챗으로 시작 메시지가 갈 겁니다."
echo
echo "자주 쓰는 명령:"
echo "  로그 보기      docker compose logs -f --tail=50"
echo "  설정 다시      ./deploy/oracle/setup.sh"
echo "  최신 코드로    ./deploy/oracle/update.sh"
echo "  중지           docker compose down"
