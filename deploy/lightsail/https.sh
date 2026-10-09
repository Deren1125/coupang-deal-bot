#!/usr/bin/env bash
# 스레드/인스타 로그인(OAuth) 콜백용 HTTPS 주소 만들기: Caddy + sslip.io (도메인 구입 불필요)
#   bash deploy/lightsail/https.sh
# 미리 할 일: Lightsail 콘솔 → 인스턴스 → 네트워킹 → IPv4 방화벽에 HTTP(80), HTTPS(443) 추가
# 결과 주소 예: https://52-79-136-64.sslip.io/threads/callback  ← 스레드 앱 설정의 '리디렉션 URI' 에 넣는다
# 주소는 서버 IP 로 만들어지므로 Lightsail 고정 IP 를 붙여 두는 게 좋다 (안 붙이면 서버를 껐다 켤 때 IP·주소가 바뀜)
set -euo pipefail
cd "$(dirname "$0")/../.."
CADDY_DIR="${CADDY_DIR:-/etc/caddy}"  # (테스트에서만 바꿈)

echo "▶ 1/4 서버 주소 확인"
IP="$(curl -s --max-time 5 https://checkip.amazonaws.com | tr -d '[:space:]' || true)"
[ -n "$IP" ] || IP="$(curl -s --max-time 5 https://api.ipify.org || true)"
[ -n "$IP" ] || { echo "⛔ 공인 IP 를 못 읽었어요"; exit 1; }
DOMAIN="${IP//./-}.sslip.io"
PORT="$(grep -E '^PORT=' .env | tail -1 | cut -d= -f2- || true)"  # 없으면 기본 8080
PORT="${PORT:-8080}"
OLD_DOMAIN="$(grep -E '^PUBLIC_DOMAIN=' .env | tail -1 | cut -d= -f2- || true)"

echo "   $DOMAIN"
if [ -n "$OLD_DOMAIN" ] && [ "$OLD_DOMAIN" != "$DOMAIN" ]; then
  echo "⚠️ 서버 주소가 바뀌었어요: $OLD_DOMAIN → $DOMAIN"
  echo "   스레드·인스타 앱 설정의 리디렉션 URI 도 새 주소로 바꿔야 해요 (맨 아래에 나오는 주소)"
fi
echo "▶ 2/4 Caddy(HTTPS) 설치·설정"
if ! command -v caddy >/dev/null; then
  sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl gnupg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  sudo apt-get update -q
  sudo apt-get install -y caddy
fi

SITE="$DOMAIN {
	reverse_proxy 127.0.0.1:$PORT
}"
# 설치 직후 기본 Caddyfile 이거나 이 스크립트가 쓴 것이면 통째로 바꾸고,
# 다른 사이트 설정이 들어 있으면 그대로 두고 핫딜 봇 주소는 따로 파일(dealbot.caddy)로 만들어 import 한 줄만 더한다
KNOWN='^[[:space:]]*(#.*)?$|^:80 \{$|^[[:space:]]*root \* /usr/share/caddy$|^[[:space:]]*file_server$|^[0-9-]+\.sslip\.io \{$|^[[:space:]]*reverse_proxy 127\.0\.0\.1:[0-9]+$|^\}$'
BAK=""
if [ -s "$CADDY_DIR/Caddyfile" ]; then
  BAK="$CADDY_DIR/Caddyfile.bak-$(date +%m%d_%H%M%S)"
  sudo cp "$CADDY_DIR/Caddyfile" "$BAK"
  ls -1t "$CADDY_DIR"/Caddyfile.bak-* 2>/dev/null | tail -n +6 | xargs -r sudo rm -f --  # 백업은 최근 5개만
fi
# dealbot.caddy 도 바꾸기 전 내용을 남겨 둔다. 점검에 걸려 Caddyfile 을 되돌릴 때 같이 되돌린다
# (옛 Caddyfile 이 이미 dealbot.caddy 를 import 하고 있으면 새 dealbot.caddy 가 그대로 읽히므로)
INC_BAK=""
WROTE_INC=""
if [ -e "$CADDY_DIR/dealbot.caddy" ]; then
  INC_BAK="$CADDY_DIR/dealbot.caddy.bak"
  sudo cp "$CADDY_DIR/dealbot.caddy" "$INC_BAK"
fi
restore_caddy() {
  if [ -n "$BAK" ]; then sudo cp "$BAK" "$CADDY_DIR/Caddyfile"; fi
  if [ -n "$INC_BAK" ]; then
    sudo cp "$INC_BAK" "$CADDY_DIR/dealbot.caddy"
  elif [ -n "$WROTE_INC" ]; then
    sudo rm -f "$CADDY_DIR/dealbot.caddy"
  fi
}
if [ ! -s "$CADDY_DIR/Caddyfile" ] || ! grep -vqE "$KNOWN" "$CADDY_DIR/Caddyfile"; then
  printf '%s\n' "$SITE" | sudo tee "$CADDY_DIR/Caddyfile" >/dev/null
else
  # 예전 https.sh 가 Caddyfile 에 직접 쓴 봇 주소 블록(…sslip.io { reverse_proxy 127.0.0.1:N })은 뺀다.
  # 남겨 두면 dealbot.caddy 와 같은 주소가 두 번 정의돼 Caddy 가 설정을 거부하고 다른 사이트까지 멈춘다 (IP 가 바뀐 옛 블록은 인증서만 계속 실패)
  # 새 내용은 임시 파일에 먼저 만든다. 'python … | sudo tee Caddyfile' 로 바로 쓰면 tee 가 파일부터 비워서, python 이 실패하면
  # (UTF-8 이 아닌 Caddyfile, 못 읽는 백업) 빈 Caddyfile 만 남고 다음 재시작·재부팅 때 모든 사이트가 사라진다
  TMP_CADDY="$(mktemp)"
  if ! python3 - "$BAK" "import $CADDY_DIR/dealbot.caddy" >"$TMP_CADDY" <<'PY'
import re
import sys

text = open(sys.argv[1], encoding="utf-8").read()
own = re.compile(r"^[0-9-]+\.sslip\.io \{[ \t]*\n(?:[ \t]*reverse_proxy 127\.0\.0\.1:[0-9]+[ \t]*\n)+\}[ \t]*(?:\n|$)", re.M)
text = own.sub("", text).rstrip("\n") + "\n"
if sys.argv[2] not in text.splitlines():
    text += "\n" + sys.argv[2] + "\n"
sys.stdout.write(text)
PY
  then
    rm -f "$TMP_CADDY"
    echo "⛔ Caddyfile 을 읽지 못해서(UTF-8 이 아니거나 읽을 수 없음) 아무것도 바꾸지 않았어요. Caddy 는 그대로 돌고 있어요."
    echo "   확인: $CADDY_DIR/Caddyfile"
    exit 1
  fi
  printf '%s\n' "$SITE" | sudo tee "$CADDY_DIR/dealbot.caddy" >/dev/null
  WROTE_INC=1
  sudo cp "$TMP_CADDY" "$CADDY_DIR/Caddyfile"
  rm -f "$TMP_CADDY"
  echo "   Caddyfile 에 다른 사이트 설정이 있어서 그대로 두고 봇 주소는 import 한 줄로만 넣었어요 (전 파일: $BAK)"
fi
# 다시 켜기 전에 설정을 점검한다. 틀렸으면 원래 파일로 되돌리고 멈춘다 (Caddy 가 안 떠서 다른 사이트까지 멈추지 않게)
if ! sudo caddy validate --adapter caddyfile --config "$CADDY_DIR/Caddyfile" >/dev/null 2>&1; then
  restore_caddy
  echo "⛔ 새 Caddy 설정이 점검(caddy validate)에서 걸려서 원래 Caddyfile(과 dealbot.caddy)로 되돌렸어요. Caddy 는 그대로 돌고 있어요."
  echo "   확인: sudo caddy validate --adapter caddyfile --config $CADDY_DIR/Caddyfile"
  exit 1
fi
sudo systemctl enable caddy >/dev/null
sudo systemctl restart caddy

echo "▶ 3/4 봇에 주소 기록 후 재시작"
# 봇이 이 주소로 콜백 주소를 만들도록 .env 에 기록. 다른 주소를 가리키는 리디렉션 설정(예전 Railway·예시 값)이 남아 있으면
# 봇이 그걸 먼저 써서 스레드 로그인이 '리디렉션 URI 불일치'로 실패하므로 지운다 (바꾸기 전 .env 는 백업)
touch .env
mkdir -p var/env_backups && chmod 700 var/env_backups
cp .env "var/env_backups/env_$(date +%m%d_%H%M%S_%N)"
ls -1t var/env_backups/env_* 2>/dev/null | tail -n +21 | xargs -r rm -f --  # setenv.sh 와 같이 최근 20개만
python3 - .env "$DOMAIN" <<'PY'
import sys
from urllib.parse import urlparse

path, domain = sys.argv[1], sys.argv[2]
out = []
for line in open(path, encoding="utf-8").read().splitlines():
    key, _, value = line.partition("=")
    key = key.strip()
    if key == "PUBLIC_DOMAIN":
        continue
    if key in ("THREADS_REDIRECT_URI", "INSTAGRAM_REDIRECT_URI"):
        host = urlparse(value.strip().strip("'\"")).hostname or ""
        if host != domain.split(":")[0]:
            print(f"   {key} 지움 — 다른 주소({value.strip()})를 가리키고 있었어요. 이제 {domain} 기준으로 자동으로 맞춰져요")
            continue
    out.append(line)
out.append(f"PUBLIC_DOMAIN={domain}")
open(path, "w", encoding="utf-8").write("\n".join(out) + "\n")
PY
chmod 600 .env
sudo systemctl restart dealbot || true

echo "▶ 4/4 인증서 발급·연결 확인 (최대 1분)"
ok=""
for _ in 1 2 3 4 5 6; do
  sleep 10
  if curl -s --max-time 15 -o /dev/null -w '%{http_code}' "https://$DOMAIN/health" | grep -q '^2'; then ok=1; break; fi
done
if [ -n "$ok" ]; then
  echo "✅ HTTPS 준비됨: https://$DOMAIN"
else
  echo "⚠️ 아직 https://$DOMAIN 응답이 없어요 — Lightsail 방화벽 80/443 을 열었는지 확인 (인증서 발급에 1~2분 걸릴 수 있음)"
fi
echo "스레드 앱 '리디렉션 콜백 URL': https://$DOMAIN/threads/callback"
echo "그다음 텔레그램 관리자 챗에서 /threadsauth"
echo
echo "💡 Lightsail 고정 IP 를 붙여 두면 서버를 껐다 켜도 이 주소가 그대로예요"
echo "   (콘솔 → 네트워킹 → 고정 IP 만들기 → 이 인스턴스에 연결). 붙인 뒤엔 이 스크립트를 한 번 더 실행하세요."
