#!/usr/bin/env bash
# 최신 코드/이미지로 갱신 후 재시작
set -euo pipefail
cd "$(dirname "$0")/../.."
git pull --ff-only
if docker compose pull 2>/dev/null; then
  echo "==> GHCR 이미지 갱신"
else
  echo "==> 이미지 pull 실패 → 로컬 빌드"
  docker compose build
fi
docker compose up -d
docker compose ps
docker compose logs --tail=30 dealbot
