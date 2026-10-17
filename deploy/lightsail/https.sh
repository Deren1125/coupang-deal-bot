#!/usr/bin/env bash
# 스레드/인스타 로그인(OAuth) 콜백·카드 이미지·핫딜 페이지용 HTTPS 주소 만들기: Caddy + sslip.io (도메인 구입 불필요)
#   bash deploy/lightsail/https.sh                         → https://52-79-136-64.sslip.io
#   bash deploy/lightsail/https.sh --domain example.com    → 내 도메인도 같이 (sslip.io 주소도 계속 받음)
# 미리 할 일: Lightsail 콘솔 → 인스턴스 → 네트워킹 → IPv4 방화벽에 HTTP(80), HTTPS(443) 추가
# 결과 주소 예: https://52-79-136-64.sslip.io/threads/callback  ← 스레드 앱 설정의 '리디렉션 URI' 에 넣는다
# 주소는 서버 IP 로 만들어지므로 Lightsail 고정 IP 를 붙여 두는 게 좋다 (안 붙이면 서버를 껐다 켤 때 IP·주소가 바뀜)
#
# 내 도메인 (주인 결정 10/15, docs/SETUP_KEYS.md '내 도메인 연결'):
#   - Caddy 는 sslip.io 주소와 내 도메인을 둘 다 받는다 → 메타에 등록해 둔 예전 리디렉션 주소가 그대로 된다
#   - .env 의 PUBLIC_DOMAIN 은 내 도메인의 https://도메인/health 확인이 통과하고 DNS A 레코드가 이 서버를 가리킬 때만 내 도메인으로 바꾼다
#     (A 레코드가 다른 서버면 그 서버의 /health 응답일 수 있어 안 바꿈)
#     (안 되면 예전 주소 그대로 두고 이유를 알려 줌 — 대개 DNS A 레코드가 아직 이 서버를 안 가리킴)
#   - 바꾼 뒤 핫딜 페이지(/)·카드 이미지 주소는 내 도메인. 메타에 더할 리디렉션 주소를 마지막에 그대로 보여 준다
#   - 한 번 옮긴 뒤에는 --domain 없이 다시 실행해도 내 도메인을 계속 받는다
set -euo pipefail
cd "$(dirname "$0")/../.."
CADDY_DIR="${CADDY_DIR:-/etc/caddy}"  # (테스트에서만 바꿈)

usage() {
  echo "사용법: bash deploy/lightsail/https.sh [--domain example.com]"
}

CUSTOM=""
while [ $# -gt 0 ]; do
  case "$1" in
    --domain) [ $# -ge 2 ] || { usage; exit 2; }; CUSTOM="$2"; shift 2 ;;
    --domain=*) CUSTOM="${1#*=}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "⛔ 모르는 옵션: $1"; usage; exit 2 ;;
  esac
done

# 도메인 이름 확인 (아무것도 바꾸기 전에): 영문 소문자·숫자·- 로 된 이름들을 . 으로 이은 것, 끝은 영문 2자 이상
valid_host() {
  local h="$1"
  [ "${#h}" -le 253 ] || return 1
  printf '%s' "$h" | grep -Eq '^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$'
}
if [ -n "$CUSTOM" ]; then
  CUSTOM="$(printf '%s' "$CUSTOM" | tr 'A-Z' 'a-z')"
  if ! valid_host "$CUSTOM"; then
    echo "⛔ 도메인 이름이 아니에요: '$CUSTOM' (예: example.com — https:// 나 / 없이 이름만)"
    exit 2
  fi
  case "$CUSTOM" in
    *.sslip.io|*.nip.io) echo "⛔ '$CUSTOM' 은 sslip.io 주소예요 — 그냥 bash deploy/lightsail/https.sh 로 만들어요"; exit 2 ;;
  esac
fi

echo "▶ 1/4 서버 주소 확인"
IP="$(curl -s --max-time 5 https://checkip.amazonaws.com | tr -d '[:space:]' || true)"
[ -n "$IP" ] || IP="$(curl -s --max-time 5 https://api.ipify.org || true)"
[ -n "$IP" ] || { echo "⛔ 공인 IP 를 못 읽었어요"; exit 1; }
DOMAIN="${IP//./-}.sslip.io"
PORT="$(grep -E '^PORT=' .env | tail -1 | cut -d= -f2- || true)"  # 없으면 기본 8080
PORT="${PORT:-8080}"
OLD_DOMAIN="$(grep -E '^PUBLIC_DOMAIN=' .env | tail -1 | cut -d= -f2- | tr -d "'\"" || true)"
# 이미 내 도메인으로 옮긴 서버: --domain 없이 다시 실행해도 그 도메인을 계속 받는다 (안 그러면 핫딜 페이지·카드 주소가 끊김)
if [ -z "$CUSTOM" ] && [ -n "$OLD_DOMAIN" ] && valid_host "$OLD_DOMAIN"; then
  case "$OLD_DOMAIN" in
    *.sslip.io|*.nip.io|*.railway.app) ;;
    *) CUSTOM="$OLD_DOMAIN"; echo "   내 도메인 $CUSTOM 도 계속 받아요 (지금 PUBLIC_DOMAIN)" ;;
  esac
fi

echo "   $DOMAIN${CUSTOM:+ + $CUSTOM}"
# 예전 sslip.io 주소: PUBLIC_DOMAIN 이 sslip.io 이면 그것, 내 도메인으로 옮긴 서버면 PUBLIC_DOMAIN_ALIASES 의 sslip.io 주소
OLD_ALIASES="$(grep -E '^PUBLIC_DOMAIN_ALIASES=' .env | tail -1 | cut -d= -f2- | tr -d "'\"" || true)"
OLD_SSLIP=""
case "$OLD_DOMAIN" in
  *.sslip.io) OLD_SSLIP="$OLD_DOMAIN" ;;
  *) OLD_SSLIP="$(printf '%s' "$OLD_ALIASES" | tr ',' '\n' | grep -E '\.sslip\.io$' | head -1 || true)" ;;
esac
if [ -n "$OLD_SSLIP" ] && [ "$OLD_SSLIP" != "$DOMAIN" ]; then
  echo "⚠️ 서버 주소가 바뀌었어요: $OLD_SSLIP → $DOMAIN"
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

# 사이트 주소: sslip.io 주소 (+ 내 도메인). 둘 다 같은 봇으로 — 예전 리디렉션 주소가 계속 되게
# 대시보드 앱(/dashboard 아래 — 화면 값 JSON·app.js)은 압축해서 보낸다 (휴대폰 데이터로도 빨리 뜨게, 주인 10/18)
SITE="$DOMAIN${CUSTOM:+, $CUSTOM} {
	@dash path /dashboard /dashboard/*
	encode @dash zstd gzip
	reverse_proxy 127.0.0.1:$PORT
}"
# 설치 직후 기본 Caddyfile 이거나 이 스크립트가 쓴 것이면 통째로 바꾸고,
# 다른 사이트 설정이 들어 있으면 그대로 두고 핫딜 봇 주소는 따로 파일(dealbot.caddy)로 만들어 import 한 줄만 더한다
KNOWN='^[[:space:]]*(#.*)?$|^:80 \{$|^[[:space:]]*root \* /usr/share/caddy$|^[[:space:]]*file_server$|^[0-9-]+\.sslip\.io(, [a-z0-9.-]+)? \{$|^[[:space:]]*reverse_proxy 127\.0\.0\.1:[0-9]+$|^[[:space:]]*@dash path /dashboard /dashboard/\*$|^[[:space:]]*encode @dash zstd gzip$|^\}$'
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
  # 예전 https.sh 가 Caddyfile 에 직접 쓴 봇 주소 블록(…sslip.io[, 내 도메인] { reverse_proxy 127.0.0.1:N })은 뺀다.
  # 남겨 두면 dealbot.caddy 와 같은 주소가 두 번 정의돼 Caddy 가 설정을 거부하고 다른 사이트까지 멈춘다 (IP 가 바뀐 옛 블록은 인증서만 계속 실패)
  # 새 내용은 임시 파일에 먼저 만든다. 'python … | sudo tee Caddyfile' 로 바로 쓰면 tee 가 파일부터 비워서, python 이 실패하면
  # (UTF-8 이 아닌 Caddyfile, 못 읽는 백업) 빈 Caddyfile 만 남고 다음 재시작·재부팅 때 모든 사이트가 사라진다
  TMP_CADDY="$(mktemp)"
  if ! python3 - "$BAK" "import $CADDY_DIR/dealbot.caddy" >"$TMP_CADDY" <<'PY'
import re
import sys

text = open(sys.argv[1], encoding="utf-8").read()
own = re.compile(r"^[0-9-]+\.sslip\.io(?:, [a-z0-9.-]+)? \{[ \t]*\n(?:[ \t]*(?:reverse_proxy 127\.0\.0\.1:[0-9]+|@dash path /dashboard /dashboard/\*|encode @dash zstd gzip)[ \t]*\n)+\}[ \t]*(?:\n|$)", re.M)
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

# .env 쓰기: PUBLIC_DOMAIN(봇이 쓰는 공개 주소)·PUBLIC_DOMAIN_ALIASES(같이 받는 주소)를 정하고, 받지 않는 주소를 가리키는
# 리디렉션 설정(예전 Railway·예시 값)은 지운다 (남아 있으면 봇이 그걸 먼저 써서 스레드 로그인이 '리디렉션 URI 불일치'로 실패).
# pin: 그 주소로 스레드·인스타 리디렉션 주소를 고정 (내 도메인으로 옮길 때 — 메타에 등록해 둔 sslip.io 주소가 계속 되게)
# repin: pin 을 '받지 않는 주소라 지운' 리디렉션 설정에만 (이미 옮긴 서버의 IP 가 바뀜 — 예전 sslip.io 로 고정해 둔 것을 새 sslip.io 로.
#        그냥 지우면 봇이 메타에 없을 수 있는 내 도메인 콜백 주소로 넘어간다)
write_env() {  # write_env PUBLIC 별칭(쉼표) 받는주소(쉼표) [pin] [repin]
  touch .env
  mkdir -p var/env_backups && chmod 700 var/env_backups
  cp .env "var/env_backups/env_$(date +%m%d_%H%M%S_%N)"
  ls -1t var/env_backups/env_* 2>/dev/null | tail -n +21 | xargs -r rm -f --  # setenv.sh 와 같이 최근 20개만
  python3 - .env "$1" "$2" "$3" "${4:-}" "${5:-}" <<'PY'
import sys
from urllib.parse import urlparse

path, public, aliases, served, pin, repin = sys.argv[1:7]
hosts = {h.split(":")[0] for h in served.split(",") if h}
out, kept, dropped = [], set(), set()
for line in open(path, encoding="utf-8").read().splitlines():
    key, _, value = line.partition("=")
    key = key.strip()
    if key in ("PUBLIC_DOMAIN", "PUBLIC_DOMAIN_ALIASES"):
        continue
    if key in ("THREADS_REDIRECT_URI", "INSTAGRAM_REDIRECT_URI"):
        host = urlparse(value.strip().strip("'\"")).hostname or ""
        if host not in hosts:
            dropped.add(key)
            if not (pin and repin):
                print(f"   {key} 지움 — 다른 주소({value.strip()})를 가리키고 있었어요. 이제 {public} 기준으로 자동으로 맞춰져요")
            continue
        kept.add(key)
    out.append(line)
out.append(f"PUBLIC_DOMAIN={public}")
if aliases:
    out.append(f"PUBLIC_DOMAIN_ALIASES={aliases}")
if pin:
    for key, cb in (("THREADS_REDIRECT_URI", "/threads/callback"), ("INSTAGRAM_REDIRECT_URI", "/instagram/callback")):
        if key not in kept and (not repin or key in dropped):
            out.append(f"{key}=https://{pin}{cb}")
            print(f"   {key}=https://{pin}{cb} 로 고정 — 메타에 등록해 둔 주소가 그대로 돼요")
open(path, "w", encoding="utf-8").write("\n".join(out) + "\n")
PY
  chmod 600 .env
}

health() {  # health 주소 → 200 이면 0
  curl -s --max-time 15 -o /dev/null -w '%{http_code}' "https://$1/health" | grep -q '^2'
}

dns_of() {  # dns_of 도메인 → A 레코드 IP 들 (공백으로)
  getent ahostsv4 "$1" 2>/dev/null | awk '{print $1}' | sort -u | tr '\n' ' ' | sed 's/ *$//' || true
}

points_elsewhere() {  # 도메인의 A 레코드가 있는데 이 서버($IP)가 없으면 0 — 그 주소의 /health 가 2xx 여도 다른 서버의 응답
  local d
  d="$(dns_of "$1")"
  [ -n "$d" ] && ! printf ' %s ' "$d" | grep -q " $IP "
}

echo "▶ 3/4 봇에 주소 기록 후 재시작"
SERVED="$DOMAIN${CUSTOM:+,$CUSTOM}"
if [ -n "$CUSTOM" ] && [ "$OLD_DOMAIN" = "$CUSTOM" ]; then
  # 이미 옮긴 서버: 내 도메인 그대로, sslip.io 는 별칭. 예전 sslip.io 로 고정해 둔 리디렉션 주소는 새 sslip.io 로 (IP 가 바뀐 경우)
  write_env "$CUSTOM" "$DOMAIN" "$SERVED" "$DOMAIN" repin
else
  write_env "$DOMAIN" "" "$SERVED"  # 내 도메인은 확인이 통과한 뒤에 (4/4)
fi
sudo systemctl restart dealbot || true

echo "▶ 4/4 인증서 발급·연결 확인 (최대 1분)"
ok=""
for _ in 1 2 3 4 5 6; do
  sleep 10
  if health "$DOMAIN"; then ok=1; break; fi
done
if [ -n "$ok" ]; then
  echo "✅ HTTPS 준비됨: https://$DOMAIN"
else
  echo "⚠️ 아직 https://$DOMAIN 응답이 없어요 — Lightsail 방화벽 80/443 을 열었는지 확인 (인증서 발급에 1~2분 걸릴 수 있음)"
fi

PUBLIC="$DOMAIN"
if [ -n "$CUSTOM" ]; then
  cok=""
  for _ in 1 2 3 4 5 6; do
    if health "$CUSTOM" && ! points_elsewhere "$CUSTOM"; then cok=1; break; fi
    sleep 10
  done
  if [ -n "$cok" ]; then
    PUBLIC="$CUSTOM"
    if [ "$OLD_DOMAIN" != "$CUSTOM" ]; then
      write_env "$CUSTOM" "$DOMAIN" "$SERVED" "$DOMAIN"
      sudo systemctl restart dealbot || true
    fi
    echo "✅ 내 도메인 연결됨: https://$CUSTOM (PUBLIC_DOMAIN=$CUSTOM — 핫딜 페이지·카드 이미지 주소가 이 도메인으로)"
    echo "   https://$DOMAIN 도 계속 받아요 (메타에 등록해 둔 예전 리디렉션 주소가 그대로 돼요)"
    echo
    echo "메타 앱에 더할 리디렉션 주소 (developers.facebook.com → 앱 → 사용 사례):"
    echo "  스레드 (Threads API → 설정 → 리디렉션 콜백 URL · 설치 제거 콜백 · 삭제 콜백):"
    echo "    https://$CUSTOM/threads/callback"
    echo "  인스타그램 (Instagram → API 설정 → 비즈니스 로그인 설정 → OAuth 리디렉션 URI):"
    echo "    https://$CUSTOM/instagram/callback"
    echo "  더한 뒤 새 주소로 바꾸려면: bash deploy/lightsail/setenv.sh THREADS_REDIRECT_URI (값 https://$CUSTOM/threads/callback),"
    echo "  INSTAGRAM_REDIRECT_URI (값 https://$CUSTOM/instagram/callback) → sudo systemctl restart dealbot"
  else
    if [ "$OLD_DOMAIN" = "$CUSTOM" ]; then
      PUBLIC="$CUSTOM"
      echo "⚠️ 내 도메인 https://$CUSTOM 이 지금 응답하지 않아요 (PUBLIC_DOMAIN 은 그대로 $CUSTOM)"
    else
      echo "⚠️ 내 도메인 https://$CUSTOM 확인이 안 돼서 PUBLIC_DOMAIN 은 그대로 $DOMAIN 이에요"
    fi
    DNS="$(dns_of "$CUSTOM")"
    if [ -z "$DNS" ]; then
      echo "   이유: DNS 에 $CUSTOM 의 A 레코드가 아직 없어요 — 도메인 관리 화면에서 A 레코드를 $IP 로 (docs/SETUP_KEYS.md '내 도메인 연결')"
    elif ! printf ' %s ' "$DNS" | grep -q " $IP "; then
      echo "   이유: DNS A 레코드가 아직 이 서버($IP)를 가리키지 않아요 (지금: $DNS) — 바꾼 뒤 퍼지는 데 몇 분~몇 시간"
    else
      echo "   이유: DNS 는 이 서버를 가리키는데 HTTPS 응답이 없어요 — 방화벽 80/443·인증서 발급(1~2분)을 기다린 뒤 다시 실행"
    fi
    echo "   준비되면 다시: bash deploy/lightsail/https.sh --domain $CUSTOM"
  fi
fi
echo "스레드 앱 '리디렉션 콜백 URL': https://$DOMAIN/threads/callback"
echo "그다음 텔레그램 관리자 챗에서 /threadsauth"
echo "(지금 봇의 공개 주소: https://$PUBLIC)"
echo
echo "💡 Lightsail 고정 IP 를 붙여 두면 서버를 껐다 켜도 이 주소가 그대로예요"
echo "   (콘솔 → 네트워킹 → 고정 IP 만들기 → 이 인스턴스에 연결). 붙인 뒤엔 이 스크립트를 한 번 더 실행하세요."
