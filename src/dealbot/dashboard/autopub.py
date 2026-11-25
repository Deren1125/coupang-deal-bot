"""'하루치 한 번에 (발행까지)' 책갈피 (주인 11/23 — "책갈피 누르면 한 번에 글 여러 개를 넣고, 발행 누르는 것까지 가능하게 만들자").

주인이 그 블로그로 로그인한 자기 사파리의 PC 글쓰기 화면에서 한 번 누르면: 그 블로그의 준비된 대기 글을 차례로 — 제목·본문·꾸미기·사진·태그·카테고리·예약 시각
(꾸미기 kkumi.js 통째) → 스스로 점검 → 깨끗하면 마지막 발행 단추까지 누름 → 발행 확인 → 할 일 줄 ✓ → 40~120초 쉬고 다음 글 (한 번에 5편까지).
서버는 네이버를 열지도 로그인하지도 않는다 (모든 일은 주인 브라우저에서 · 서버는 글과 차례·간격·상한·쉼만 정해 줌).
옛 '글쓰기 한 번에'(writebm.py — 11/16 접음 · 발행은 주인이 누름)와 다른 점: 마지막 발행 단추를 책갈피가 누른다 · 그래서 점검에 하나라도 걸리면 그 글에서 멈춘다.

책갈피 = 진행기 코드 통째 (원격 코드 실행 금지 — 메인 11/16): static/autopub.js (껍데기) 의 '@@KKUMI@@' 자리에 static/kkumi.js 를 통째로 (PATCHES 다섯 줄만 바꿈 —
  ①~④ writebm 과 같은 넷 (머리 → 함수 · 붙여넣기 칸 대신 받은 글 · 글 넣기 전 제목 넣기 + 도구 넘기기 · 꼬리) ⑤ 붙여넣기 칸(ask) 코드는 뺌 — 쓰지 않음 · 주소를 짧게).
  kkumi.js 에서 그 줄이 사라지면 ValueError (test_dash_autopub 이 지킴 — kkumi.js 를 고칠 때 같이). 꾸미기 책갈피는 그대로 따로 돈다.
  대시보드는 코드를 내려 주지 않는다 (new Function·eval 없음) · autopub_act 는 데이터만. 고치면 책갈피를 다시 깔아야 함 (판 번호 V · 끝 줄 'v<n>').

POST /dashboard/api/autopub_act (폼 · 로그인 없이 · 열쇠 k = HMAC(kv autopub_secret) · CORS 는 https://*.blog.naver.com 만 · 데이터만):
  a=next b=블로그 [w=1]  {ok, next: {id, title, html, text, at, label, photos, disc} | None, n, m, left, wait(초), text}
      그 블로그의 오늘 할 일 발행·예약 줄 가운데 '올려도 되는 글'만 (ready): 체크 안 됨 · 다 만든 묶음 · 올리기 전 자동 점검 통과(gate.ok 참 — 점검에 걸린 글·점검 안 된 글·
      새 방향과 안 맞는다고 적힌 글(Blog-Auto direction.fit 거짓 — 주인 11/23 · 줄 글 '새 방향' 도)은 주지 않음) · 제휴 링크가 있는데 고지 줄이 없는 글 뺌 · 오늘 이미 해 본 글(ok·unsure·hold·두 번 멈춘 글) 뺌. 차례는 예약 시각 → 화면 차례.
      예약 시각 (v5 · 주인 11/23 13:45 · 간격 v6 11/24): 누른 때 기준 계획 schedule() — 첫 글 20~45분 뒤 · 다음은 짧게 40~60분·보통 75~140분·길게 2시간 반~4시간 섞음
        (이웃 간격끼리 20분 넘게 차이) · 분은 10·20·40·50 (하루 같은 분 두 번까지 · 30분·정각 없음 — 주인 11/24) · 다른 블로그와 20분 넘게 · 07:30~23:00 · 토·일도 평일과 같게 (주인 11/24) ·
        이미 한 그 블로그 시각과 40분 넘게 · kv autopub:plan 에 남겨 다시 눌러도 같은 계획 (지났거나 없는 글만 새로) · plan = 맨 위 한 줄 '오늘 예약 14:20 · 16:10'.
      첫 글 지금 발행 (v7 · 주인 11/16 '오늘 발행 글 없으면 1번 글 지금 발행' — v5 에서 빠졌던 것): 그 블로그에 오늘 발행·오늘 예약이 없으면
        (published_today — 계획의 쓴 시각 · 할 일 목록 체크 줄 · Blog-Auto 오늘 발행 수) 첫 글은 at '' = 발행창 '현재' · 나머지는 지금부터 40분 넘게 뒤 계획 ·
        07:30~23:00 일 때만 · plan = '지금 발행 · 오늘 예약 14:20 · 16:10'.
      wait = 앞 글 뒤 40~120초(GAP · 무작위)가 되기까지 남은 초. 한 판(RUN_IDLE_S 안에 이어진 글)에 RUN_MAX(5)편까지 — 넘으면 next 없음.
      주말 쉼 없음 (주인 11/24 '주말에도 글 쓰고 발행' — 11/21 주말 쉼은 지움 · 서버가 weekend 를 보내지 않음 · 책갈피 v7 에서
        '주말에도 시작' 단추·w 신호를 뺌 (옛 책갈피가 보내는 w 는 무시)).
      네이버 보호·제한 안내 뒤 24시간(kv autopub:cool)은 429 cool.
  a=done b id r [n] [u]  r: hold(발행 단추를 누르기 직전 — '해 봄'으로 적어 같은 글을 다시 주지 않음 · 두 번 발행 막기) · ok(발행·예약 확인 — 할 일 줄 체크 +
      블로그 프로그램에 pasted · u = 글 번호) · unsure(눌렀는데 확인 못 함 — 체크하지 않고 다시 주지 않음) · stop(점검에 걸려 발행하지 않음 · n = 걸린 것 —
      다시 누르면 같은 글부터 · 같은 글이 두 번 멈추면 오늘은 주지 않음) · fail(못 넣음) · wall(네이버 보호·제한 안내 → 그 블로그 24시간 쉼 · id 없어도 됨).
kv: "autopub:day:<날 KST>" = {tried: {줄 id: r}, stops: {줄 id: 수}, last: {블로그: 시각}, gap: {블로그: 초}, run: {블로그: {n, last}}, res: [{id, b, r, n, u, at}]} (그날·전날만) ·
    "autopub:held" = {줄 id: {r, at}} (발행을 눌러 본 글 — 14일 · 날이 바뀌어도 다시 주지 않음 · 이미 있으면 hold 는 409 dup — 두 탭 동시 발행 막기) ·
    "autopub:plan" = {블로그: {slots: {줄 id: 시각}, used: [발행을 눌러 본 글의 시각]}} ·
    "autopub:cool" = {블로그: {until, at, why}} (쉬는 것 — until 을 남김 · CLAUDE.md '딜봇이 스스로 쉬기 시작하면').
today() = 할 일 탭 블로그 카드의 진행 한 줄 (routine.view 의 autopub) · 연결 탭 설치 칸의 한 줄.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import random
import re
import secrets
import time
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dealbot.dashboard import kkumi, seoi
from dealbot.dashboard.statscap import CaptureError

STATIC = Path(__file__).resolve().parent / "static"
SRC = STATIC / "autopub.js"
KV_SECRET = "autopub_secret"
KV_COOL = "autopub:cool"
KV_PLAN = "autopub:plan"  # 누른 때 기준 예약 계획 {블로그: {slots: {줄 id: 'YYYY-MM-DDTHH:MM'}, used: [시각…]}} (주인 11/23 13:45)
KV_HELD = "autopub:held"  # 발행을 눌러 본 글 {줄 id: {r, at}} — 날이 바뀌어도 다시 주지 않음 (두 번 발행 막기 · 검토 11/23)
HELD_S = 14 * 86400.0
DAY_KEY = "autopub:day:"
PATH = "/dashboard/api/autopub_act"
OFF_TEXT = "하루치 한 번에는 꺼져 있어요 — 꾸미기 책갈피를 쓰세요"
GAP = (40, 120)  # 글 사이 쉬는 초 (주인 11/23)
RUN_MAX = 5  # 한 판에 블로그마다 올리는 글 수
RUN_IDLE_S = 1800.0  # 마지막 글 뒤 이만큼 지나면 새 판
COOL_S = 86400.0  # 네이버 보호·제한 안내 뒤 쉬는 시간 (이웃 관리 seoi.COOL_S 와 같게)
STOP_MAX = 2  # 같은 글이 점검에서 이만큼 멈추면 오늘은 주지 않음 (꾸미기 책갈피로 직접)
RESULTS = ("hold", "ok", "unsure", "stop", "fail", "wall")
TRIED = ("hold", "ok", "unsure")  # 오늘은 다시 주지 않는 결과
RES_MAX = 80
NOTE_MAX = 300
GROUPS = ("발행", "예약")
ROW_RE = re.compile(r"^(?:pub|rsv):(finance|issue|hotdeal):(\d{1,9})$")  # actions.PASTE_ROW_RE 와 같게 (test_dash_autopub 이 지킴)
NAMES = seoi.NAMES
# 올리면 안 되는 글 (대시보드 표시): 줄의 한마디·제목에 이런 말이 있으면 주지 않는다 (새 방향과 안 맞는 글 · 주인이 건너뛴 글 · 보류)
UNFIT_RE = re.compile(r"새 방향|방향과 안|안 맞|건너뛰|건너뜀|올리지 마|올리지 않|보류|안 올림")
DISC_RE = re.compile(r"수수료를\s*(받|제공|지급)")  # static/autopub.js DISC 와 같게
AFF_HOSTS = ("link.coupang.com", "coupa.ng", "s.zigzag.kr", "a-bly.com", "ably.link", "naver.me")  # 주소만 봐도 제휴 링크 (Blog-Auto issue_similar.AFF_HOSTS + 쇼핑커넥트 짧은 주소)
_TAG = re.compile(r"<[^>]+>")
U_RE = re.compile(r"\d{6,20}")
# kkumi.js 의 옛 줄 → 진행기 안에서만 바뀌는 줄 (딱 한 번씩 나와야 함)
TOOLS = ("D, W, R, S, vis, press, tap, wait, real, comps, body_, host, setSel, count, tagIn, pubBtn, BOTTOM, any, nImg, quotes, cards, shape, centered, titleMid, MAIN_CENTER, "
         "tagsIn, catIn, bookIn")
PATCHES: tuple[tuple[str, str], ...] = (
    ("(async () => {\n", "async (document, alert, confirm, GOT, PRE) => {\n"),
    ("const got = await ask();\n", "const got = GOT;\n"),
    ("try { window.__kkumiReport = R; } catch (e) {}\n", "if (!await PRE({" + TOOLS + "})) return;\n"),
    ("})().catch(e => alert('꾸미기 오류: ' + e));\n", "}\n"),
    # v5 (11/23 아이패드 '포스트잇 글이 안 들어감'): 안 확인을 4초까지 · 새로 그린 맨 끝 포스트잇도 봄 (꾸미기 차례·붙여넣기는 그대로 — 확인만 넓힘)
    # v5 (11/23 13:40 아이패드 '글 안 들어감' 첫 조각): 첫 조각이 안 들어가면 진행기(GOT.re — 본문이 비었을 때만 주인이 본문을 한 번 더 누름)로 다시
    ("      if (!await pasteIn(p.html, p.lines.join('\\n'), probe)) { R.bad.push('글 안 들어감: ' + (probe || '사진')); if (!R.done) break; }\n",
     "      let ok_ = await pasteIn(p.html, p.lines.join('\\n'), probe);\n"
     "      for (let r_ = 0; !ok_ && !R.done && GOT.re && r_ < 2 && await GOT.re(); r_++) ok_ = await pasteIn(p.html, p.lines.join('\\n'), probe);\n"
     "      if (!ok_) { R.bad.push('글 안 들어감: ' + (probe || '사진')); if (!R.done) break; }\n"),
    # v5: 글자 꾸밈 세기(끝 알림에만 쓰던 것)도 뺌 — 주소를 짧게
    ("const fx = h => [/background-color/gi, /[^-]color\\s*:/gi, /<(b|strong)[\\s>]|font-weight:\\s*(bold|[6-9]00)|class=\"[^\"]*bold/gi].map(x => (h.match(x) || []).length);\n", ""),
    ("R.fx = [fx(ps.filter(p => p.k === 'text').map(p => p.html).join('')), [0, 0, 0]];\n", ""),
    ("  R.fx[1] = fx(body_().map(p => p.innerHTML).join(''));\n", ""),
    # v6: 꾸미기 끝 알림에만 쓰던 한마디(R.notes)는 진행기에서 뺌 — 주소를 짧게 (점검은 R.bad·화면에서)
    ("      if (probe && !await align(p.align)) R.notes.push('가운데 못 함: ' + probe);\n", "      if (probe) await align(p.align);\n"),
    ("      if (why === 'x') R.bad.push('사진 안 들어감'); else if (why === 'slow') R.notes.push('사진 올라가는 중'); else if (why) R.notes.push('사진 주소로'); else R.pics[0]++;\n",
     "      if (why === 'x') R.bad.push('사진 안 들어감'); else if (!why) R.pics[0]++;\n"),
    ("    if (R.tagMiss.length) R.notes.push('태그 빠짐: ' + R.tagMiss.join(', '));\n", ""),
    # v6: 제목 가운데는 주인이 제목 칸을 누른 그 자리에서 (진행기 pre) — 끝에서 제목을 스크립트로 누르는 길은 아이패드에서 안 먹음
    ("  if (ps.some(p => p.align === 'center')) { R.title = await titleMid(); if (R.title) R.notes.push('제목 가운데 ' + R.title + ' — 직접'); }\n", ""),
    ("  const inside = P && await wait(has, 1500);\n", "  const inside = P && await wait(() => has() || tz((quotes().pop() || q).textContent).includes(P), 4000);\n"),
)
CUT = ("const ask = () => new Promise(res => {\n", "  setTimeout(() => box.focus(), 50);\n});\n")  # ⑤ 붙여넣기 칸 — 처음 줄부터 끝 줄까지 뺌
# v5: 꾸미기 끝 알림 글(진행기는 그 알림을 쓰지 않음 — 점검은 R 과 화면에서) 도 뺌 · 주소를 짧게 (49.5KB → 포스트잇 점검을 넣을 자리)
CUTS = (CUT, ("const nm = (a, label) =>", "else R.notes.push('꾸밈 없음');\n"), ("alert((R.ok ? '모두 됐어요'", "'\\nv' + V);\n"))
SLOT = "'@@KKUMI@@'"
STEPS = (
    "아래 '코드 복사'를 눌러요.",
    "사파리에서 아무 페이지나 연 뒤 공유 단추 → '책갈피 추가' → 이름을 '하루치 한 번에'로 저장해요.",
    "책갈피 목록 → '편집' → '하루치 한 번에'의 주소 칸을 모두 지우고, 복사한 코드를 붙여 넣은 뒤 '저장'. (코드가 길어요 — 저장이 안 되면 예전처럼 꾸미기 책갈피로)",
    "쓰는 법: 그 블로그로 로그인한 사파리 프로필에서 PC 글쓰기 화면(blog.naver.com/GoBlogWrite.naver)을 열고 '하루치 한 번에'를 한 번 → "
    "글마다 화면 위 한 줄이 시키는 대로 ① 제목 칸을 한 번 ② 본문을 한 번 눌러 주세요 (아이패드 네이버 에디터는 사람이 누른 자리에만 글이 들어가요 — 꾸미기와 같아요) → "
    "그다음은 손대지 않으면 제목·본문·꾸미기·사진·태그·카테고리·예약 시각까지 채우고, 스스로 점검한 뒤 깨끗하면 발행(예약)까지 눌러요 → "
    f"{GAP[0]}~{GAP[1]}초 쉬고 다음 글 → 한 번에 {RUN_MAX}편까지 → 끝나면 할 일 줄이 체크되고 화면에 요약이 남아요. 화면을 켜 둔 채 그 탭에 두세요.",
    "점검에 하나라도 걸리면 (사진이 많이 빠짐 · 제목/본문이 원고와 다름 · 포스트잇 · 카테고리 못 찾음 · 예약 시각 못 채움 · 제휴 고지 · 태그가 여럿 빠짐 · 가운데 정렬) 그 글은 발행하지 않고 멈춰요 — "
    "사진은 원고의 90% 넘고 2장까지, 태그는 1개(또는 10%)까지 빠진 것은 발행하고 요약 줄에 적어요. "
    "화면에 적힌 것을 고친 뒤 '고쳤어요 — 다시 점검하고 발행' 단추(또는 책갈피 한 번 더)를 누르면 다시 점검하고 이어 가요. 멈추려면 탭을 닫으면 돼요.",
    "예약 시각은 책갈피를 누른 때 기준으로 나눠요 — 첫 글은 20~45분 뒤, 다음 글은 1시간 10분~2시간 40분씩 들쭉날쭉 (분은 10·20·40·50 · 07:30~23:00 · 넘치면 다음 날 아침 · 토·일도 그날). 화면 맨 위에 '오늘 예약 14:20 · 16:10 …'이 보여요. 멈춘 뒤 새 글쓰기 화면에서 다시 눌러도 남은 글은 같은 시각 그대로 (지난 시각만 새로).",
    "이 브라우저에서 스스로 이어가기가 안 되면 화면을 다시 열고 '글마다 한 번'으로 바꿔요 — 그때는 글 하나가 올라갈 때마다 '다음 글 N/M — 책갈피 한 번 더'가 뜨고, "
    "새 글쓰기 화면이 열리면 한 번씩만 눌러 주면 돼요 (나머지는 그대로 자동).",
    "토·일도 평일처럼 올려요 (주인 11/24 — 토요일에 누르면 토요일 자리에 예약). 대시보드 점검에 걸린 글·새 방향과 안 맞는 글·이미 체크한 글은 올리지 않아요.",
    "네이버가 자동입력 방지·보호조치·이용 제한·한도 안내를 보이면 풀지 않고 바로 멈추고, 그 블로그의 하루치 한 번에는 24시간 쉬어요 (그동안 다시 눌러도 하지 않아요). "
    "다른 블로그는 그 블로그 프로필에서 같은 식으로 (한 번에 로그인된 블로그 하나의 글만 해요).",
)
LEN_MAX = 50000  # '글쓰기 한 번에'(writebm.LEN_MAX)와 같은 한도 — 꾸미기(29KB) + 껍데기 · 넘으면 ValueError (연결 탭이 503)
HEAD = "(async()=>{const W=window;if(W.__apr){W.__apb&&W.__apb();return}W.__apr=1;try{await("
TAIL = (")({u:'@@URL@@',k:'@@KEY@@',bl:JSON.parse('@@BLOGS@@')})}catch(e){const o=document.createElement('div');"
        "o.style.cssText='position:fixed;left:8px;right:8px;top:8px;z-index:2147483647;padding:14px;border-radius:12px;background:#111;color:#fff;"
        "font:16px/1.5 sans-serif;text-align:center';o.textContent='하루치 한 번에 오류: '+e;(document.body||document.documentElement).appendChild(o)}W.__apr=0})()")


def _secret(db: Any) -> bytes:
    k = db.kv_get(KV_SECRET)
    if not k:
        k = secrets.token_hex(32)
        db.kv_set(KV_SECRET, k)
    return bytes.fromhex(k)


def key(db: Any) -> str:
    mac = hmac.new(_secret(db), b"autopub", hashlib.sha256).digest()
    return base64.urlsafe_b64encode(mac).decode().rstrip("=")


def key_ok(db: Any, given: str | None) -> bool:
    return bool(given) and hmac.compare_digest(key(db).encode(), str(given).encode())


@lru_cache(maxsize=1)
def version() -> int:
    m = re.search(r"^const V = (\d+);", SRC.read_text(encoding="utf-8"), re.M)
    return int(m.group(1)) if m else 0


def _core() -> str:
    """꾸미기 책갈피 코드 → 진행기가 부르는 함수 (머리 주석 뺌 · PATCHES · 붙여넣기 칸 뺌)."""
    src = re.sub(r"\A\s*/\*.*?\*/\s*", "", kkumi.SRC.read_text(encoding="utf-8"), count=1, flags=re.S)
    for old, new in PATCHES:
        if src.count(old) != 1:
            raise ValueError(f"kkumi.js 에 없는 줄: {old.strip()[:40]}")
        src = src.replace(old, new)
    for a, b in CUTS:
        if src.count(a) != 1 or src.count(b) != 1 or src.index(a) > src.index(b):
            raise ValueError(f"kkumi.js 에 뺄 줄이 없음: {a.strip()[:40]}")
        src = src[:src.index(a)] + src[src.index(b) + len(b):]
    return src.rstrip().rstrip(";")


def _shell() -> str:
    shell = re.sub(r"\A\s*/\*.*?\*/\s*", "", SRC.read_text(encoding="utf-8"), count=1, flags=re.S)
    if shell.count(SLOT) != 1:
        raise ValueError("autopub.js 에 꾸미기 자리가 없음")
    return shell.rstrip().rstrip(";")


@lru_cache(maxsize=1)
def runner() -> str:
    """진행기 코드 (함수 식 하나 — 'async CFG => {…}' · 줄이기 전 · 시험·읽기용)."""
    return _shell().replace(SLOT, _core())


@lru_cache(maxsize=1)
def _code() -> str:
    """줄인 진행기: 껍데기는 한글 글을 Z('…') 로 (seoi.pack — 주소에서 한글 한 자가 9자 → 3자) · 꾸미기 코드는 줄이기만 (정규식 안에 따옴표가 있어 pack 하지 않음)."""
    shell = seoi.pack(kkumi.minify(_shell()))
    if shell.count(SLOT) != 1:
        raise ValueError("autopub.js 에 꾸미기 자리가 없음")
    return HEAD + shell.replace(SLOT, _zcore(kkumi.minify(_core()))) + TAIL


# v5: 꾸미기 코드의 한글 '…' 글도 Z('…') 로 (주소에서 한 자 9자 → 3자 · 포스트잇·본문 다시 누름·예약 계획을 넣을 자리) — 정규식 안 따옴표가 있어 seoi.pack 은 못 씀 →
# 문법상 값 자리( ( , = : ? + [ ! return 뒤)에서 시작하고 안에 따옴표·역슬래시·줄바꿈이 없고 ] 가 바로 뒤따르지 않는 작은따옴표 글만 (kkumi.js 정규식에는 따옴표·공백 없음 — kkumi.py 머리말)
_ZLIT = re.compile(r"(^|[(,=:?+\[!]|return)'([^'\"\\\n]*[가-힣][^'\"\\\n]*)'(?!\])")  # 선택자 안 '…'] (큰따옴표 글 속) 는 그대로


def _zcore(core: str) -> str:
    def sub(m: re.Match[str]) -> str:
        z = seoi.pack_text(m.group(2))
        head = m.group(1) + (" " if m.group(1) == "return" else "")  # return'…' → return Z('…') (붙이면 returnZ — 이름이 됨)
        return head + (f"Z('{z}')" if z is not None else f"'{m.group(2)}'")
    return _ZLIT.sub(sub, core)


def bookmarklet(endpoint: str, k: str) -> str:
    """javascript: 주소 — 진행기 통째 + 주소·열쇠·블로그 표 (따옴표·역슬래시 없는 글자만)."""
    if not re.fullmatch(r"https://[A-Za-z0-9.\-:]+/[A-Za-z0-9_/\-]+", endpoint) or not re.fullmatch(r"[A-Za-z0-9_\-]+", k):
        raise ValueError("bad endpoint or key")
    return "javascript:" + kkumi.encode_short(_code().replace("@@URL@@", endpoint).replace("@@KEY@@", k).replace("@@BLOGS@@", seoi.blogs_json()))


# ── 그날 기록
def _day(now: float, tz: ZoneInfo) -> str:
    return datetime.fromtimestamp(now, tz).strftime("%Y-%m-%d")


def _num(v: Any) -> float:
    return float(v) if isinstance(v, int | float) and not isinstance(v, bool) else 0.0


def _load(db: Any, day: str) -> dict[str, Any]:
    try:
        v = json.loads(db.kv_get(DAY_KEY + day) or "{}")
    except (TypeError, ValueError):
        v = {}
    v = v if isinstance(v, dict) else {}

    def d(k: str) -> dict[str, Any]:
        return v[k] if isinstance(v.get(k), dict) else {}

    return {"tried": {str(k): str(x) for k, x in d("tried").items()}, "stops": {str(k): int(_num(x)) for k, x in d("stops").items()},
            "last": {str(k): _num(x) for k, x in d("last").items()}, "gap": {str(k): _num(x) for k, x in d("gap").items()},
            "run": {str(k): {"n": int(_num(x.get("n"))), "last": _num(x.get("last"))} for k, x in d("run").items() if isinstance(x, dict)},
            "res": [r for r in (v.get("res") if isinstance(v.get("res"), list) else []) if isinstance(r, dict)][-RES_MAX:]}


def _save(db: Any, day: str, v: dict[str, Any]) -> None:
    db.kv_set(DAY_KEY + day, json.dumps(v, ensure_ascii=False, separators=(",", ":")))
    try:
        prev = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        db.kv_delete_prefix(DAY_KEY, keep=lambda k, _v: k in (DAY_KEY + day, DAY_KEY + prev))
    except (ValueError, AttributeError):
        pass


def _run_n(c: dict[str, Any], blog: str, now: float) -> int:
    """이번 판에 그 블로그에서 올린(누른) 글 수 — 마지막 글 뒤 RUN_IDLE_S 가 지났으면 새 판(0)."""
    r = c["run"].get(blog)
    return r["n"] if r and now - r["last"] <= RUN_IDLE_S else 0


def held(db: Any, now: float) -> dict[str, str]:
    """발행을 눌러 본 글 (hold·ok·unsure) — HELD_S 동안. 그날 기록(autopub:day)은 이틀만 남아, 확인 못 한 글이 다음 날 다시 나오는 것을 막는다."""
    try:
        v = json.loads(db.kv_get(KV_HELD) or "{}")
    except (TypeError, ValueError):
        v = {}
    return {str(k): str(x.get("r")) for k, x in (v if isinstance(v, dict) else {}).items()
            if isinstance(x, dict) and isinstance(x.get("at"), int | float) and now - x["at"] < HELD_S}


def _held_add(db: Any, rid: str, r: str, now: float) -> None:
    try:
        v = json.loads(db.kv_get(KV_HELD) or "{}")
    except (TypeError, ValueError):
        v = {}
    v = {k: x for k, x in (v if isinstance(v, dict) else {}).items() if isinstance(x, dict) and isinstance(x.get("at"), int | float) and now - x["at"] < HELD_S}
    v[rid] = {"r": r, "at": int(now)}
    db.kv_set(KV_HELD, json.dumps(v, separators=(",", ":")))


# ── 쉼 (네이버 보호·제한 안내 뒤 24시간)
def cooled(db: Any, now: float) -> dict[str, float]:
    try:
        v = json.loads(db.kv_get(KV_COOL) or "{}")
    except (TypeError, ValueError):
        v = {}
    return {b: float(x["until"]) for b, x in (v if isinstance(v, dict) else {}).items()
            if b in NAMES and isinstance(x, dict) and isinstance(x.get("until"), int | float) and x["until"] > now}


def cool(db: Any, blog: str, why: str, *, now: float) -> float:
    """그 블로그를 COOL_S 쉼 (이미 쉬는 중이면 늘리지 않음 — 처음 본 때부터 24시간). 돌려줌: 끝나는 시각."""
    have = cooled(db, now)
    if blog in have:
        return have[blog]
    try:
        v = json.loads(db.kv_get(KV_COOL) or "{}")
    except (TypeError, ValueError):
        v = {}
    v = {b: x for b, x in (v if isinstance(v, dict) else {}).items() if b in have}  # 지난 것은 지움
    v[blog] = {"until": now + COOL_S, "at": now, "why": why[:60]}
    db.kv_set(KV_COOL, json.dumps(v, ensure_ascii=False, separators=(",", ":")))
    return now + COOL_S


def cool_text(blog: str, until: float, tz: ZoneInfo) -> str:
    d = datetime.fromtimestamp(until, tz)
    return f"네이버 보호·제한 안내로 {NAMES.get(blog, blog)} 하루치 한 번에는 {d.month}/{d.day} {d:%H:%M}까지 쉬어요 — 그동안은 직접 올려 주세요"


# ── 올려도 되는 글
def unfit(i: dict[str, Any]) -> str:
    """그 줄을 스스로 올리면 안 되는 까닭 ('' = 올려도 됨). 대시보드가 막은 글은 절대 올리지 않는다 (주인 11/23)."""
    p = i.get("paste")
    if not isinstance(p, dict) or p.get("stale") or not p.get("html"):
        return "만드는 중이거나 점검에 걸린 글"
    d = i.get("direction")
    if isinstance(d, dict) and d.get("fit") is False:  # Blog-Auto 새 한 분야 방향 판정 (주인 11/23 — 옛 방향 대기 글은 스스로 올리지 않음)
        return f"새 방향과 안 맞음 — 올리지 않음 ({d.get('why') or '까닭 모름'})"
    g = i.get("gate")
    if not isinstance(g, dict) or g.get("ok") is not True:
        return "올리기 전 자동 점검을 통과하지 않은 글"
    if UNFIT_RE.search(f"{i.get('title') or ''} {i.get('note') or ''}"):
        return "대시보드에 올리지 않는 글로 적힌 글"
    html = str(p["html"])
    if any(h in html for h in AFF_HOSTS) and not DISC_RE.search(_TAG.sub("", html)):
        return "제휴 링크가 있는데 고지 줄이 없는 글"
    return ""


def misfits(rv: Any, blog: str) -> list[dict[str, Any]]:
    """그 블로그의 아직 안 한 발행·예약 줄 가운데 새 방향과 안 맞는 줄 (direction.fit 거짓 — 화면 한 줄용)."""
    if not isinstance(rv, dict) or not rv.get("today"):
        return []
    return [i for s in rv.get("slots") or [] for i in s.get("items") or []
            if i.get("group") in GROUPS and i.get("blog") == blog and not i.get("done")
            and isinstance(i.get("direction"), dict) and i["direction"].get("fit") is False]


def ready(rv: Any, blog: str, tried: dict[str, str] | None = None) -> list[dict[str, Any]]:
    """routine.view() 값에서 그 블로그의 '스스로 올려도 되는' 대기 글 — 예약 시각 차례 (없는 것은 화면 차례 그대로 앞에). 오늘 목록이 아니면 []."""
    if not isinstance(rv, dict) or not rv.get("today"):
        return []
    out = []
    for s in rv.get("slots") or []:
        for i in s.get("items") or []:
            if (i.get("group") in GROUPS and i.get("blog") == blog and not i.get("done") and (m := ROW_RE.match(str(i.get("id") or "")))
                    and m.group(1) == blog and i["id"] not in (tried or {}) and not unfit(i)):
                out.append(i)
    return sorted(out, key=lambda i: str((i.get("when") or {}).get("at") or "") if isinstance(i.get("when"), dict) else "")


# ── 누른 때 기준 예약 계획 (주인 11/23 13:45 — "예약 시간이 다 똑같은 거 같다. 내가 책갈피를 언제 누를지 모르니까, 한 번에 넣기 누른 시간 기준으로 적당히 시간 분배해서 세팅해줘")
# v6 (주인 11/24 — "시간 간격도 좀 불규칙하게 바꿔줘. 시간 단위만 불규칙으로 하는 게 아니라"): 사람이 올린 것처럼 —
# 간격: 짧게(40~60분 · 가끔 두 글이 붙음)·보통(75~140분)·길게(2시간 반~4시간) 섞음 · 이웃 간격끼리 20분 넘게 차이 · 날마다 전체 폭(배율)이 다름 ·
# 분: 네이버 예약 칸(10분 단위) 10·20·40·50 · 하루 한 블로그에서 같은 분 두 번까지 · 이웃 글과 같은 분 아님 · 30분·정각 없음 (주인 11/24) ·
# 세 블로그: 같은 날 다른 블로그 예약과 20분 넘게 · 씨앗 = 블로그+날짜+누른 때 (날마다 다른 모양 · 고정 아님).
# 그대로: 첫 글 = 누른 때 + 20~45분 · 사람 시간 07:30~23:00 (넘으면 다음 날 아침 · 토·일도 평일과 같게 — 주인 11/24) ·
# 이미 예약·발행한 그 블로그 시각과 40분 넘게 · kv 에 남겨 새 글쓰기 화면에서 다시 눌러도 같은 계획 (자리가 지났거나 없는 글만 새로) · 블로그마다 따로.
FIRST_MIN, CLASH_MIN, LATE_MIN = (20, 45), 40, 12
GAPS = (("short", (40, 55), 0.25), ("mid", (75, 140), 0.40), ("long", (150, 240), 0.35))  # 간격 종류 (분 · 비율)
GAP_DIFF, NEAR_MIN, MIN_REPEAT = 20, 20, 2  # 이웃 간격 차이 · 다른 블로그와 · 같은 분 하루 두 번까지
SPREAD = (0.85, 1.2)  # 날마다 보통·긴 간격 배율 (하루 전체 폭이 날마다 다르게)
DAY_H = ((7, 30), (22, 50))  # 끝은 10분 자리로 올려도 23:00 을 넘지 않게
MINUTES = (10, 20, 40, 50)  # 이것만 (주인 11/24 — 정각·30분 없음 · Blog-Auto reserve_plan.MINUTES 와 같게)


def _plans(db: Any) -> dict[str, dict[str, Any]]:
    try:
        v = json.loads(db.kv_get(KV_PLAN) or "{}")
    except (TypeError, ValueError):
        v = {}
    out: dict[str, dict[str, Any]] = {}
    for b, x in (v if isinstance(v, dict) else {}).items():
        if b in NAMES and isinstance(x, dict):
            sl = x.get("slots") if isinstance(x.get("slots"), dict) else {}
            us = x.get("used") if isinstance(x.get("used"), list) else []
            nw = x.get("now") if isinstance(x.get("now"), dict) else {}
            out[b] = {"slots": {str(k): str(t) for k, t in sl.items() if _dt(t)}, "used": [str(t) for t in us if _dt(t)]}
            if nw.get("id") and nw.get("day"):  # 지금 발행할 첫 글 (v7 · 그날만)
                out[b]["now"] = {"id": str(nw["id"]), "day": str(nw["day"])}
    return out


def _dt(t: Any) -> datetime | None:
    try:
        return datetime.strptime(str(t), "%Y-%m-%dT%H:%M")
    except ValueError:
        return None


def _fit(d: datetime, rng: Any = random) -> datetime:
    """사람 시간 밖이면 그날·다음 날 아침 (07:30 + 0~50분)으로 (토·일도 같게)."""
    (h0, m0), (h1, m1) = DAY_H
    for _ in range(10):
        start, end = d.replace(hour=h0, minute=m0), d.replace(hour=h1, minute=m1)
        if d < start:
            d = start + timedelta(minutes=rng.randint(0, 50))
            continue
        if d > end:
            d = (d + timedelta(days=1)).replace(hour=h0, minute=m0) + timedelta(minutes=rng.randint(0, 50))
            continue
        break
    return d


def _ok_min(m: int) -> bool:
    return m in MINUTES


def _gap(rng: Any, prev: float | None, spread: float) -> float:
    """다음 간격(분) — 종류를 섞고, 앞 간격과 GAP_DIFF 넘게 다르게."""
    g = 0.0
    for _ in range(20):
        lo, hi = rng.choices([b for _n, b, _p in GAPS], weights=[p for *_x, p in GAPS])[0]
        g = rng.uniform(lo, hi) * (1.0 if lo < 70 else spread)
        if prev is None or abs(g - prev) > GAP_DIFF + 10:  # 10분 자리로 옮겨도 차이가 남게 여유
            return g
    return g


def schedule(db: Any, blog: str, rows: list[dict[str, Any]], now: float, tz: ZoneInfo, *,
             taken: list[str] | None = None, near: list[str] | None = None, rng: Any = None, first_now: bool = False) -> dict[str, str]:
    """그 블로그의 남은 글 rows 의 예약 시각 {줄 id: 'YYYY-MM-DDTHH:MM'} — 살아 있는 계획은 그대로, 지났거나 없는 글만 새로 (kv 에 남김).
    near = 다른 블로그의 이미 한 예약 시각 (계획에 든 다른 블로그 시각은 kv 에서 읽음) — 같은 날 NEAR_MIN 안에 두지 않음.
    first_now (v7 · 주인 11/16 '오늘 발행 글 없으면 1번 글 지금 발행'): 첫 글(rows[0])은 '' = 지금 발행 · 나머지는 지금부터 40분 넘게 뒤로
    (07:30~23:00 일 때만 · 다시 눌러도 그날은 같은 글 · 아니면 전처럼 모두 예약)."""
    allp = _plans(db)
    pl = allp.get(blog, {"slots": {}, "used": []})
    n = datetime.fromtimestamp(now, tz).replace(tzinfo=None, second=0, microsecond=0)
    ids0 = [str(i["id"]) for i in rows]
    nw = pl.get("now") if isinstance(pl.get("now"), dict) else None
    if nw and (nw["day"] != f"{n:%Y-%m-%d}" or nw["id"] not in ids0):
        nw = None
    if first_now and _inday(n) and ids0:
        nw = nw or {"id": ids0[0], "day": f"{n:%Y-%m-%d}"}
    elif not first_now:
        nw = None
    if nw:
        rows = [i for i in rows if str(i["id"]) != nw["id"]]
    if rng is None:  # 블로그+날짜+누른 때 씨앗 (날마다·블로그마다 다른 모양)
        rng = random.Random(f"{blog}|{n:%Y-%m-%dT%H:%M}")
    late = n + timedelta(minutes=LATE_MIN)
    ids = [str(i["id"]) for i in rows]
    keep = {k: t for k, t in pl["slots"].items() if k in ids and (d := _dt(t)) is not None and d > late}
    used = [t for t in pl["used"] if (d := _dt(t)) is not None and d > n - timedelta(days=1)]
    busy = [d for t in (*used, *(taken or []), *keep.values()) if (d := _dt(t)) is not None] + ([n] if nw else [])
    others = [d for b, p in allp.items() if b != blog for t in (*p["slots"].values(), *p["used"]) if (d := _dt(t))]
    others += [d for t in (near or []) if (d := _dt(t))]
    new = [k for k in ids if k not in keep]
    if new:
        spread = rng.uniform(*SPREAD)
        mine = sorted(busy)
        base = max([d for t in keep.values() if (d := _dt(t))], default=n if nw else None)
        ks = sorted(d for t in keep.values() if (d := _dt(t)))
        prev_gap = (ks[-1] - ks[-2]).total_seconds() / 60 if len(ks) > 1 and ks[-1].date() == ks[-2].date() else None
        prev = base
        for k in new:
            d = _place(rng, n, prev, prev_gap, spread, mine, others)
            keep[k] = d.strftime("%Y-%m-%dT%H:%M")
            mine.append(d)
            prev_gap = (d - prev).total_seconds() / 60 if prev is not None and prev.date() == d.date() else None
            prev = d
    allp[blog] = {"slots": keep, "used": used, **({"now": nw} if nw else {})}
    db.kv_set(KV_PLAN, json.dumps(allp, separators=(",", ":")))
    return {nw["id"]: "", **keep} if nw else keep


def _inday(x: datetime) -> bool:
    return DAY_H[0] <= (x.hour, x.minute) <= (23, 0)


def _place(rng: Any, n: datetime, prev: datetime | None, prev_gap: float | None, spread: float,
           mine: list[datetime], others: list[datetime]) -> datetime:
    """다음 글 한 자리 — 규칙을 하나씩 풀어 가며 (40분·사람 시간·정각 없음은 끝까지 지킴).
    단계 4: 모두 · 3: 같은 날 4시간 반 넘는 간격 허용 · 2: 간격 종류를 앞 간격과 상관없이 · 1: 넓게 훑기(다른 블로그 20분·분·이웃 간격 규칙은 지킴) · 0: 그것도 풀기."""
    for strict in (4, 3, 2, 1, 0):
        for _ in range(30 if strict >= 2 else 1):
            if prev is None:
                target = n + timedelta(minutes=rng.randint(*FIRST_MIN))
            else:
                target = prev + timedelta(minutes=_gap(rng, prev_gap if strict >= 3 else None, spread))
            target = _fit(target.replace(second=0, microsecond=0), rng)
            span = 40 if strict >= 2 else 3 * 24 * 60
            cands = [x for j in range(span) if _ok_min((x := target + timedelta(minutes=j)).minute) and _inday(x)]
            if prev is not None and strict >= 2 and rng.random() < 0.3 and len(cands) > 1:  # 가끔 한 칸 건너 (가장 가까운 자리만 고르지 않게 · 첫 글은 그대로)
                cands = cands[1:] + cands[:1]
            for x in cands:
                if any(abs((x - b).total_seconds()) <= CLASH_MIN * 60 for b in mine):
                    continue
                if strict >= 1:
                    if any(x.date() == b.date() and abs((x - b).total_seconds()) <= NEAR_MIN * 60 for b in others):
                        continue
                    if prev is not None and x.minute == prev.minute:
                        continue
                    if sum(1 for b in mine if b.date() == x.date() and b.minute == x.minute) >= MIN_REPEAT:
                        continue
                if strict >= 1 and prev is not None and prev_gap is not None and prev.date() == x.date() \
                        and abs((x - prev).total_seconds() / 60 - prev_gap) <= GAP_DIFF:
                    continue
                if strict >= 4 and prev is not None and prev.date() == x.date() and (x - prev) > timedelta(hours=4, minutes=30):
                    continue  # 같은 날 긴 간격도 4시간 반 넘지 않게
                return x
    raise AssertionError("no slot")  # 사흘을 훑어도 40분 자리가 없을 수는 없음 (한 블로그 글은 몇 편뿐)


def _used(db: Any, blog: str, rid: str, now: float | None = None, tz: ZoneInfo | None = None) -> None:
    """발행을 눌러 본 글의 예약 시각은 계획에서 빼 '쓴 시각'으로 (다른 글이 그 시각 40분 안에 오지 않게).
    v7: 지금 발행한 첫 글은 누른 때를 '쓴 시각'으로 (= 오늘 발행한 글이 있음 → 다음 글부터 예약)."""
    allp = _plans(db)
    pl = allp.get(blog)
    if pl and rid in pl["slots"]:
        pl["used"] = [*pl["used"], pl["slots"].pop(rid)][-20:]
        db.kv_set(KV_PLAN, json.dumps(allp, separators=(",", ":")))
    elif pl and (pl.get("now") or {}).get("id") == rid and now is not None and tz is not None:
        pl.pop("now")
        pl["used"] = [*pl["used"], datetime.fromtimestamp(now, tz).strftime("%Y-%m-%dT%H:%M")][-20:]
        db.kv_set(KV_PLAN, json.dumps(allp, separators=(",", ":")))


def published_today(db: Any, blog: str, now: float, tz: ZoneInfo, rv: Any = None, pub: dict[str, Any] | None = None) -> bool:
    """그 블로그에 오늘(KST) 발행했거나 오늘 올라갈 예약이 이미 있는지 (v7 · 첫 글 지금 발행을 정함) — 하나라도 있으면 참:
    하루치 한 번에 계획의 '쓴 시각'(발행을 눌러 본 글의 예약 시각 · 지금 발행이면 누른 때 — 오늘 날짜만) · 오늘 할 일 목록에서 체크된 그 블로그 발행·예약 줄(예약은 오늘 시각만) ·
    Blog-Auto 활동 기록의 오늘 발행 수 (pub = {블로그: 수} · 없으면 안 봄)."""
    day = _day(now, tz)
    if any(t.startswith(day) for t in (_plans(db).get(blog) or {}).get("used", [])):
        return True
    if isinstance(rv, dict) and rv.get("day") == day:
        for s in rv.get("slots") or []:
            for i in s.get("items") or []:
                if i.get("blog") == blog and i.get("group") in GROUPS and i.get("done"):
                    w = i.get("when") if isinstance(i.get("when"), dict) else {}
                    if not w.get("at") or str(w["at"]).startswith(day):
                        return True
    try:
        return float((pub or {}).get(blog) or 0) > 0
    except (TypeError, ValueError):
        return False


def _label(t: str, now: float, tz: ZoneInfo) -> str:
    d, n = _dt(t), datetime.fromtimestamp(now, tz).replace(tzinfo=None)
    if d is None:
        return t
    k = (d.date() - n.date()).days
    return f"{'오늘' if k == 0 else '내일' if k == 1 else f'{d.month}/{d.day}'} {d:%H:%M}"


def plan_text(slots: dict[str, str], now: float, tz: ZoneInfo) -> str:
    """'오늘 예약 14:20 · 16:10 · 내일 07:50' — 진행기 맨 위 한 줄 (v7: 지금 발행할 글이 있으면 맨 앞 '지금 발행')."""
    out, day = (["지금 발행"] if "" in slots.values() else []), None
    for t in sorted(v for v in slots.values() if v):
        lab = _label(t, now, tz)
        head, hm = lab.rsplit(" ", 1)
        out.append(hm if head == day else (f"{head} 예약 {hm}" if day is None else f"{head} {hm}"))
        day = head
    return " · ".join(out)


def _taken(rv: Any, blog: str) -> list[str]:
    """이미 한(체크된) 그 블로그 예약 줄의 시각 — 겹치지 않게."""
    if not isinstance(rv, dict):
        return []
    return [str(i["when"]["at"]) for s in rv.get("slots") or [] for i in s.get("items") or []
            if i.get("blog") == blog and i.get("done") and isinstance(i.get("when"), dict) and i["when"].get("at")]


def _stamped(text: str, at: str) -> str:
    return kkumi.stamp(kkumi.unstamp(text), at) if at else kkumi.unstamp(text)


# ── 화면 한 줄
def today(db: Any, now: float, tz: ZoneInfo, rv: Any = None) -> dict[str, Any]:
    """오늘 한 것 {by: {블로그: {ok, unsure, stop, ready, cool, line}}, note} — line·note 는 보일 것이 없으면 ''. rv = routine.view 값 (준비된 글 수)."""
    c = _load(db, _day(now, tz))
    last = {str(r.get("id")): r for r in c["res"] if r.get("id")}  # 같은 글은 마지막 결과만
    cl, hd = cooled(db, now), held(db, now)
    by: dict[str, dict[str, Any]] = {}
    for b, name in NAMES.items():
        mine = [r for r in last.values() if r.get("b") == b]
        ok = sum(1 for r in mine if r.get("r") == "ok")
        unsure = sum(1 for r in mine if r.get("r") in ("unsure", "hold"))
        stop = [r for r in mine if r.get("r") == "stop"]
        n = len(ready(rv, b, {**hd, **c["tried"]})) if rv is not None else 0
        mf = misfits(rv, b) if rv is not None else []
        bits = []
        if ok or unsure or stop:
            bits.append(f"올린 글 {ok}")
        if unsure:
            bits.append(f"확인 필요 {unsure} (블로그에서 확인)")
        if stop:
            bits.append(f"멈춘 글 {len(stop)} — {str(stop[-1].get('n') or '점검에 걸림')[:60]}")
        if b in cl:
            bits.append(cool_text(b, cl[b], tz))
        elif n:
            bits.append(f"준비된 글 {n}편 — 글쓰기 화면에서 책갈피 한 번")
        if mf:  # 주인 11/23 — 왜 안 올리는지 알게
            bits.append(f"새 방향과 안 맞아 올리지 않는 글 {len(mf)}편 ({str(mf[0]['direction'].get('why') or '')[:40]}"
                        + (f" 외 {len(mf) - 1}" if len(mf) > 1 else "") + ")")
        by[b] = {"name": name, "ok": ok, "unsure": unsure, "stop": len(stop), "ready": n, "cool": b in cl, "misfit": len(mf),
                 "line": "하루치 한 번에: " + " · ".join(bits) if bits else ""}
    done = [f"{v['name']} {v['ok']}" for v in by.values() if v["ok"]]
    sums = {k: sum(v[k] for v in by.values()) for k in ("unsure", "stop")}
    extra = [f"{t} {sums[k]}" for t, k in (("확인 필요", "unsure"), ("멈춘 글", "stop")) if sums[k]]
    return {"by": by, "note": "오늘 하루치 한 번에: " + " · ".join([*(done or ["올린 글 0"]), *extra]) if last else ""}


def view(db: Any, public_url: str, *, now: float | None = None, tz: ZoneInfo | None = None) -> dict[str, Any]:
    """연결 탭 설치 칸 (로그인 아래에서만 — 열쇠가 든 코드)."""
    code = bookmarklet(public_url.rstrip("/") + PATH, key(db))
    if len(code) > LEN_MAX:
        raise ValueError("bookmarklet too long")
    out: dict[str, Any] = {"code": code, "len": len(code), "steps": list(STEPS), "version": version(),
                           "note": f"지금 판 v{version()} — 끝 요약 맨 끝이 v{version()} 이면 새 판이에요"}
    if tz is not None:
        t = today(db, now or time.time(), tz)
        cools = [cool_text(b, u, tz) for b, u in cooled(db, now or time.time()).items()]
        if extra := " · ".join(x for x in (t["note"], *cools) if x):
            out["note"] += " · " + extra
    return out


# ── 책갈피가 묻는 곳
def _blog(form: dict[str, str]) -> str:
    b = str(form.get("b") or "")
    if b not in NAMES:
        raise CaptureError(400, "who", "어느 블로그인지 몰라요 — 우리 블로그 계정으로 로그인한 글쓰기 화면에서 눌러 주세요")
    return b


def act(db: Any, form: dict[str, str], *, tz: ZoneInfo, now: float | None = None, rv: Any = None, rng: Any = random,
        pub: dict[str, Any] | None = None) -> dict[str, Any]:
    """책갈피가 보낸 폼 하나 (데이터만 — 코드는 내려 주지 않음). rv = routine.view() 값 (호출하는 쪽이 열쇠를 본 뒤 읽어 넘김).
    pub = Blog-Auto 활동 기록의 오늘 발행 수 {블로그: 수} (없으면 None — 첫 글 지금 발행은 딜봇 기록·할 일 목록만 보고 정함)."""
    if not key_ok(db, form.get("k")):
        raise CaptureError(403, "key", "열쇠가 맞지 않아요 — 대시보드 연결 탭에서 '하루치 한 번에' 책갈피를 다시 설치해 주세요")
    now = now or time.time()
    a, blog, day = str(form.get("a") or ""), _blog(form), _day(now, tz)
    cl = cooled(db, now)
    if a == "next":
        if blog in cl:
            raise CaptureError(429, "cool", cool_text(blog, cl[blog], tz))
        c = _load(db, day)
        rows, n = ready(rv, blog, {**held(db, now), **c["tried"]}), _run_n(c, blog, now)
        name = NAMES[blog]
        if not rows:
            fresh = isinstance(rv, dict) and rv.get("today")
            return {"ok": True, "next": None, "left": 0, "wait": 0,
                    "text": f"{name}: 지금 올릴 준비된 글이 없어요" if fresh else "오늘 할 일 목록이 아직 없어요 — 잠시 뒤 다시 눌러 주세요"}
        if n >= RUN_MAX:
            return {"ok": True, "next": None, "left": len(rows), "wait": 0,
                    "text": f"{name}: 한 번에 {RUN_MAX}편까지만 올려요 (남은 글 {len(rows)}) — {int(RUN_IDLE_S // 60)}분쯤 뒤에 다시 눌러 주세요"}
        slots = schedule(db, blog, rows, now, tz, taken=_taken(rv, blog),
                         near=[t for b in NAMES if b != blog for t in _taken(rv, b)],
                         first_now=not published_today(db, blog, now, tz, rv, pub))
        i = min(rows, key=lambda x: slots.get(str(x["id"]), "~"))  # 계획 시각이 이른 글부터
        p, at = i["paste"], slots[str(i["id"])]
        label = _label(at, now, tz) if at else "지금"
        html = _stamped(str(p["html"]), at)
        last, gap = c["last"].get(blog, 0.0), c["gap"].get(blog, 0.0)
        return {"ok": True, "left": len(rows), "n": n + 1, "plan": plan_text(slots, now, tz), "m": n + min(len(rows), RUN_MAX - n),
                "wait": max(0, math.ceil(last + gap - now)) if last else 0, "text": f"{name}: 남은 글 {len(rows)}",
                "next": {"id": i["id"], "title": p["title"], "html": html, "text": _stamped(str(p.get("text") or ""), at), "at": at, "label": label,
                         "photos": html.count("<img"), "disc": bool(DISC_RE.search(_TAG.sub("", html)))}}
    if a == "done":
        rid, r = str(form.get("id") or ""), str(form.get("r") or "")
        m = ROW_RE.match(rid)
        if r not in RESULTS or (r != "wall" and not m) or (m and m.group(1) != blog):
            raise CaptureError(400, "bad", "모르는 요청이에요")
        note = " ".join("".join(ch for ch in str(form.get("n") or "") if ch.isprintable()).split())[:NOTE_MAX]
        c = _load(db, day)
        out: dict[str, Any] = {"ok": True, "checked": False, "text": ""}
        hd = held(db, now)
        known = bool(m) and (rid in c["tried"] or rid in hd or any(i["id"] == rid for i in ready(rv, blog)))
        if m and not known:  # 오늘 그 블로그의 올려도 되는 줄(또는 이미 해 본 줄)만 — 열쇠가 있어도 아무 줄이나 적지 못함
            raise CaptureError(400, "bad", "오늘 올릴 글이 아니에요")
        if r == "ok" and "ok" in (c["tried"].get(rid), hd.get(rid)):  # 같은 확인이 두 번 와도 한 번만
            return out
        if r == "hold" and (c["tried"].get(rid) in TRIED or rid in hd):  # 다른 탭·창이 이미 발행을 누른 글 — 두 번 발행 막기 (검토 11/23)
            raise CaptureError(409, "dup", "이미 발행을 눌러 본 글이에요 — 두 번 올리지 않아요 (블로그에서 확인해 주세요)")
        if r == "hold" and blog in cl:  # 쉬는 중에는 발행 단추를 누르게 두지 않는다 (책갈피는 hold 가 ok 일 때만 누름)
            raise CaptureError(429, "cool", cool_text(blog, cl[blog], tz))
        if r == "ok" and m and any(i["id"] == rid for i in ready(rv, blog)):
            # 오늘 그 블로그의 올려도 되는 대기 줄일 때만 체크 (열쇠가 있어도 아무 줄이나 못 건드림) — 손으로 체크할 때와 같은 길
            from dealbot.dashboard import routine

            routine.set_done(db, str(rv["day"]), rid, True)
            routine.relay_add(db, "pasted", {"blog": blog, "pid": int(m.group(2)), "done": True}, rid, now)
            out.update(checked=True, text="할 일 줄 체크 ✓")
        if r == "wall":
            until = cool(db, blog, note, now=now)
            out.update(until=until, text=cool_text(blog, until, tz))
        if m:
            if r in TRIED:
                c["tried"][rid] = r
                _held_add(db, rid, r, now)
                _used(db, blog, rid, now, tz)
            if r == "stop":
                c["stops"][rid] = c["stops"].get(rid, 0) + 1
                if c["stops"][rid] >= STOP_MAX and rid not in c["tried"]:  # hold(발행을 눌러 봄)을 덮지 않음
                    c["tried"][rid] = "stop"
            if r in ("ok", "unsure"):
                # v5 (주인 11/23 14:35 '5편까지만 … 올린 글 0' — 실제로 올라간 건 3): 한 판 상한은 발행 확인(ok)된 글만 셈 · 멈춘·못 넣은·확인 못 한 글은 안 셈
                c["run"][blog] = {"n": _run_n(c, blog, now) + (r == "ok"), "last": now}
                c["last"][blog], c["gap"][blog] = now, float(rng.randint(*GAP))
        u = str(form.get("u") or "")
        c["res"] = [*c["res"], {"id": rid, "b": blog, "r": r, "n": note, "u": u if U_RE.fullmatch(u) else "", "at": int(now)}][-RES_MAX:]
        _save(db, day, c)
        if r == "ok":
            left = len(ready(rv, blog, c["tried"]))
            out["left"] = min(left, max(0, RUN_MAX - _run_n(c, blog, now)))
        return out
    raise CaptureError(400, "bad", "모르는 요청이에요")
