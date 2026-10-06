#!/usr/bin/env bash
# .env 의 값 하나를 바꾼다 — 편집기(nano) 없이, Ctrl 키 없이 (아이패드용).
#   bash deploy/lightsail/setenv.sh TELEGRAM_BOT_TOKEN   → 값 입력(화면에 안 보임) → Enter
#   bash deploy/lightsail/setenv.sh --clean                    → 이름 없이 값만 있는 줄·같은 이름의 빈 줄 정리 (백업 남김)
# 같은 이름 줄이 여러 개면 모두 지우고 한 줄로 맞춘다. 바꾸기 전 .env 는 var/env_backups/ 에 백업.
set -uo pipefail
cd "$(dirname "$0")/../.."
ENV_FILE=".env"
touch "$ENV_FILE"
mkdir -p var/env_backups
chmod 700 var/env_backups
cp "$ENV_FILE" "var/env_backups/env_$(date +%m%d_%H%M%S_%N)"

if [ "${1:-}" = "--clean" ]; then
  python3 - "$ENV_FILE" <<'PY'
import sys
path = sys.argv[1]
lines = open(path, encoding="utf-8").read().splitlines()
filled = {l.split("=", 1)[0].strip() for l in lines if "=" in l and not l.lstrip().startswith("#") and l.split("=", 1)[1].strip()}
out, seen, dropped = [], set(), 0
# 같은 이름이 여러 번이면 프로그램(load_env)처럼 '값이 있는 마지막 줄'을 남긴다 → 뒤에서부터 본다
for l in reversed(lines):
    s = l.strip()
    if s and not s.startswith("#") and "=" not in s:
        dropped += 1  # 이름 없이 값만 있는 줄 (붙여넣다 남은 토큰 등)
        continue
    if "=" in s and not s.startswith("#"):
        k, v = s.split("=", 1)
        k = k.strip()
        if (not v.strip() and k in filled) or (v.strip() and k in seen):
            dropped += 1  # 값이 다른 줄에 있는 빈 줄, 또는 같은 이름의 앞쪽 줄
            continue
        if v.strip():
            seen.add(k)
    out.append(l)
out.reverse()
open(path, "w", encoding="utf-8").write("\n".join(out).rstrip("\n") + "\n")
print(f"🧹 정리: {dropped}줄 지움")
PY
  chmod 600 "$ENV_FILE"
  echo "✅ 끝 — 적용하려면: sudo systemctl restart dealbot"
  exit 0
fi

KEY="${1:?사용법: bash deploy/lightsail/setenv.sh 이름   (예: COUPANG_ACCESS_KEY)}"
case "$KEY" in *[!A-Z0-9_]*) echo "⛔ 이름은 영어 대문자·숫자·_ 만: $KEY"; exit 1;; esac
read -r -s -p "$KEY 값 붙여넣기 (화면에 안 보임) → Enter: " VALUE
echo
VALUE="$(printf '%s' "$VALUE" | tr -d '\r\n' | sed 's/^ *//; s/ *$//')"
[ -n "$VALUE" ] || { echo "⛔ 값이 비어 있어 바꾸지 않았어요"; exit 1; }
KEY="$KEY" VALUE="$VALUE" python3 - "$ENV_FILE" <<'PY'
import os, sys
path, key, value = sys.argv[1], os.environ["KEY"], os.environ["VALUE"]
lines = open(path, encoding="utf-8").read().splitlines()
out, placed = [], False
for l in lines:
    s = l.strip()
    if "=" in s and not s.startswith("#") and s.split("=", 1)[0].strip() == key:
        if not placed:
            out.append(f"{key}={value}")
            placed = True
        continue  # 같은 이름의 다른 줄은 지움
    out.append(l)
if not placed:
    out.append(f"{key}={value}")
open(path, "w", encoding="utf-8").write("\n".join(out).rstrip("\n") + "\n")
PY
unset VALUE
chmod 600 "$ENV_FILE"
echo "✅ $KEY 저장함 (값은 표시하지 않음) — 적용하려면: sudo systemctl restart dealbot"
