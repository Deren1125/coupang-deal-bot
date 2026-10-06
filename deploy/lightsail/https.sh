#!/usr/bin/env bash
# 스레드/인스타 로그인(OAuth) 콜백용 HTTPS 주소 만들기: Caddy + sslip.io (도메인 구입 불필요)
#   bash deploy/lightsail/https.sh
# 미리 할 일: Lightsail 콘솔 → 인스턴스 → 네트워킹 → IPv4 방화벽에 HTTP(80), HTTPS(443) 추가
# 결과 주소 예: https://52-79-136-64.sslip.io/threads/callback  ← 스레드 앱 설정의 '리디렉션 URI' 에 넣는다
set -euo pipefail
cd "$(dirname "$0")/../.."

IP="$(curl -s --max-time 5 https://checkip.amazonaws.com | tr -d '[:space:]')"
[ -n "$IP" ] || { echo "⛔ 공인 IP 를 못 읽었어요"; exit 1; }
DOMAIN="${IP//./-}.sslip.io"
PORT="$(grep -E '^PORT=' .env | tail -1 | cut -d= -f2-)"
PORT="${PORT:-8080}"

if ! command -v caddy >/dev/null; then
  sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https curl gnupg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | sudo gpg --dearmor --yes -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
  curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' | sudo tee /etc/apt/sources.list.d/caddy-stable.list >/dev/null
  sudo apt-get update -q
  sudo apt-get install -y caddy
fi

sudo tee /etc/caddy/Caddyfile >/dev/null <<CADDY
$DOMAIN {
	reverse_proxy 127.0.0.1:$PORT
}
CADDY
sudo systemctl enable caddy >/dev/null
sudo systemctl restart caddy

# 봇이 이 주소로 콜백 주소를 만들도록 .env 에 기록 (값 하나만 바꿈)
python3 - .env "$DOMAIN" <<'PY'
import sys
path, domain = sys.argv[1], sys.argv[2]
lines = [l for l in open(path, encoding="utf-8").read().splitlines() if not l.startswith("PUBLIC_DOMAIN=")]
lines.append(f"PUBLIC_DOMAIN={domain}")
open(path, "w", encoding="utf-8").write("\n".join(lines) + "\n")
PY
chmod 600 .env
sudo systemctl restart dealbot || true

sleep 8
if curl -s --max-time 15 -o /dev/null -w '%{http_code}' "https://$DOMAIN/health" | grep -q '^2'; then
  echo "✅ HTTPS 준비됨: https://$DOMAIN"
else
  echo "⚠️ 아직 https://$DOMAIN 응답이 없어요 — Lightsail 방화벽 80/443 을 열었는지 확인 (인증서 발급에 1~2분 걸릴 수 있음)"
fi
echo "스레드 앱 '리디렉션 콜백 URL': https://$DOMAIN/threads/callback"
echo "그다음 텔레그램 관리자 챗에서 /threadsauth"
