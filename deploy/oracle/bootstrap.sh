#!/usr/bin/env bash
# 오라클 클라우드(Ubuntu) VM 초기 설정: Docker 설치 + 저장소 clone + .env 준비 + swap
# 사용: curl -fsSL https://raw.githubusercontent.com/deren1125/coupang-deal-bot/main/deploy/oracle/bootstrap.sh | bash
set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/deren1125/coupang-deal-bot.git}"
REPO_BRANCH="${REPO_BRANCH:-claude/coupang-hotdeal-bot-l0gr26}"
APP_DIR="${APP_DIR:-$HOME/coupang-deal-bot}"

echo "==> apt 업데이트 & 기본 패키지"
sudo apt-get update -y
sudo apt-get install -y ca-certificates curl git gnupg

if ! command -v docker >/dev/null 2>&1; then
  echo "==> Docker 설치"
  sudo install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
  sudo chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" \
    | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
  sudo apt-get update -y
  sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-compose-plugin
  sudo systemctl enable --now docker
  sudo usermod -aG docker "$USER"
else
  echo "==> Docker 이미 설치됨"
fi

# 메모리 1GB 인스턴스 대비 swap
if [ ! -f /swapfile ] && [ "$(free -m | awk '/Mem:/{print $2}')" -lt 2048 ]; then
  echo "==> 1GB swap 생성"
  sudo fallocate -l 1G /swapfile && sudo chmod 600 /swapfile && sudo mkswap /swapfile && sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab >/dev/null
fi

if [ ! -d "$APP_DIR/.git" ]; then
  echo "==> 저장소 clone → $APP_DIR"
  git clone --branch "$REPO_BRANCH" "$REPO_URL" "$APP_DIR"
else
  echo "==> 저장소 이미 존재: $APP_DIR"
fi

cd "$APP_DIR"

# 시간대
sudo timedatectl set-timezone Asia/Seoul || true

cat <<MSG

설치 완료. 다음 단계는 두 줄이면 끝납니다.

  1) 로그아웃 후 다시 SSH 접속  (docker 그룹 반영에 필요)
       exit  → 다시 접속

  2) 설정 마법사 실행 (값을 하나씩 물어봅니다. 편집기 쓸 일 없음)
       cd $APP_DIR && ./deploy/oracle/setup.sh

MSG
