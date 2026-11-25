"""'한 번에' 책갈피 (주인 11/16 — '책갈피 버튼 합칠 수 있는 거 합치자 · 켜놓고 놔두면 한 번에 처리되게' · '내가 누를 버튼이 없어야 한다').

'통계 보내기'(statscap.js)와 '이웃 관리'(seoi.js) 두 책갈피를 하나로 묶는다 — 두 파일의 코드를 다시 쓰지 않고 껍데기(static/one.js) 안에 통째로 넣는다:
  one.js 의 '@@STAT@@'; 자리 ← 줄인 statday.js (주소·열쇠는 통계 것 · 11/25 v4: 지난 7일 + 오늘 · 조회수 순위 주간·월간 · 11/18 v3: 옛 statscap.js 대신 세 화면 — 조회수 순위·유입분석·검색 유입 + 블로그 이름)
  one.js 의 '@@SEOI@@'; 자리 ← 줄인 seoi.js (주소·열쇠·블로그 표는 이웃 관리 것) + 아래 PATCHES 네 군데 (사람이 누를 것을 없애는 것만):
    ① 블로그 고르기 창을 띄우지 않음 ② 한 명·한 편이 끝날 때마다 껍데기에 알림 ③ [시작]·[멈춤]을 숨기고 바로 돌고 끝나면 껍데기에 알림
    ④ 화면이 무엇이든 자동으로 (한 단계 모드 없음 — 한 단계는 마지막 단추를 주인이 눌러야 하므로)
  간격·한 시간·하루 상한·멈춤 규칙은 seoi.py(대시보드)와 seoi.js 그대로 — 여기에는 숫자가 하나도 없다. PATCHES 의 옛 줄이 seoi.js 에서 사라지면
  ValueError (test_dash_one 이 지킴 — seoi.js 를 고칠 때 같이 고침).
알림·묻기·적기 창은 껍데기의 같은 이름 함수가 받아 화면 아래 칸에 적는다 (창 없음). 새 POST 길은 없다 — 받는 곳은 옛 두 길 그대로
(/dashboard/api/stats_capture · /dashboard/api/seoi_act · 열쇠·CORS·크기 한도·횟수 한도 그대로). a=queue 답에 stats(오늘 그 블로그 통계가 왔는지) 한 칸만 더함 (api.seoi_act).
옛 두 책갈피는 그대로 돈다. 이웃 관리 판 번호(seoi V)는 이 책갈피도 실어 보내므로 '다시 깔기' 안내는 같이 꺼지고 같이 켜진다.
길이: 한글 글은 seoi.pack (Z) · 주소는 kkumi.encode_short (꾸미기와 같게) — 그래도 두 책갈피를 합친 길이라 지금까지의 책갈피(29KB)보다 길다 (LEN_MAX).
"""

from __future__ import annotations

import re
import time
from functools import lru_cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dealbot.dashboard import kkumi, seoi, statscap

SRC = Path(__file__).resolve().parent / "static" / "one.js"
LEN_MAX = 47000  # (11/18 v3: 통계 자리가 statday.js 라 약 30KB) · 통계 20KB + 이웃 관리 28KB 를 합쳐 줄인 것 (11/17 통계 길 고침 +0.6KB 로 46000 → 47000) (아이패드 사파리에서 돈 가장 긴 책갈피는 29KB — 저장이 안 되면 옛 두 책갈피로)
# seoi.js 의 옛 줄 → 한 번에 책갈피 안에서만 바뀌는 줄 (줄 통째로 · 딱 한 번씩 나와야 함)
PATCHES: tuple[tuple[str, str], ...] = (
    ("  if (!BL.some(x => x[1] === b)) b = await choose();\n",
     "  if (!BL.some(x => x[1] === b)) { A('어느 블로그 계정인지 못 알아봤어요 — 네이버에 로그인한 뒤 내 블로그 화면에서 눌러 주세요'); return {b: ''}; }\n"),
    ("        say(said);\n",
     "        say(said);\n        ONE.item(cur, res, said);\n"),
    ("  say('시작을 누르면 차례로 해요 · 멈춘 뒤 멈춤을 한 번 더 누르면 닫혀요');\n  await new Promise(r => { end = r; });\n",
     "  b1.style.display = b1.nextSibling.style.display = 'none';\n  await run();\n  ONE.end(q, ln.textContent);\n"),
    ("if (FM || radios().length) await form();\nelse if (!P.id || ours(P.id)) await auto();\nelse if (P.no && !H.get('bmmsg')) await visit();\nelse await blogpage();\n",
     "await auto();\n"),
)
STEPS = (
    "아래 '코드 복사'를 눌러요.",
    "사파리에서 아무 페이지나 연 뒤 공유 단추 → '책갈피 추가' → 이름을 '한 번에'로 저장해요.",
    "책갈피 목록 → '편집' → '한 번에'의 주소 칸을 모두 지우고, 복사한 코드를 붙여 넣은 뒤 '저장'. (코드가 길어요 — 저장이 안 되면 아래 '따로 쓰고 싶을 때'의 두 버튼을 쓰세요)",
    "쓰는 법: 네이버에 로그인한 사파리에서 내 통계 화면(즐겨찾기해 둔 blog.stat.naver.com)을 열고 '한 번에' → 조회수 순위·유입분석·검색 유입(지난 7일 + 오늘 · 조회수 순위는 주간·월간도 · 1분 남짓)을 "
    "어느 블로그 것인지 붙여 보내면 스스로 이웃새글 화면으로 옮겨 가요 (블로그를 못 알아보면 보내지 않고 그렇게 적어요) → "
    "거기서 '한 번에'를 한 번 더 → 서로이웃 신청·이웃 글 들르기(공감·댓글 등록)·결과 기록·할 일 줄 체크까지 끝까지 스스로 해요. "
    "누른 뒤에는 누를 단추도 확인 창도 없어요 — 화면을 켜 둔 채 그 탭에 두세요. 끝나면 화면 아래 칸에 요약이 남아요.",
    "왜 두 번인가요: 통계 화면과 블로그 화면은 네이버 주소가 달라 브라우저가 한 화면에서 둘을 못 하게 막아요 — 책갈피는 하나, 화면마다 한 번씩이에요. "
    "통계만·이웃만 하려면 그 화면에서만 누르면 돼요 (이웃새글이나 내 블로그 화면에서 바로 눌러도 돼요).",
    "멈추려면 탭을 닫거나 새로 고침 — 다시 누르면 남은 것부터 이어서 해요 (차례·간격·상한은 대시보드가 기억해요: "
    f"서이추 {seoi.GAP['add'][0]}~{seoi.GAP['add'][1]}초 간격·{seoi.ADD_BREAK_EVERY}명마다 {seoi.ADD_BREAK_S[0] // 60}~{seoi.ADD_BREAK_S[1] // 60}분 쉼, "
    f"한 시간 {seoi.HOUR_CAP}개, 하루 서이추 블로그마다 @cap@개·들르기 합쳐 {seoi.VISIT_DAY_CAP}개).",
    "네이버가 한도·제한 안내·자동입력 방지·로그인 화면을 보이면 바로 멈춰요 (돌아가지 않아요) — 한도 안내면 그 블로그의 서이추(또는 들르기)는 24시간 쉬고, "
    "그동안 다시 눌러도 그것은 하지 않아요 ('오늘은 이웃 신청 한도 — 내일 다시'). 요약의 '폰에서 직접'에 남은 것만 폰에서 하면 돼요. "
    "블로그 계정이 여럿이면 계정마다 로그인해서 같은 식으로.",
)


def _patched_seoi() -> str:
    src = seoi.SRC.read_text(encoding="utf-8")
    for old, new in PATCHES:
        if src.count(old) != 1:
            raise ValueError(f"seoi.js 에 없는 줄: {old.strip()[:40]}")
        src = src.replace(old, new)
    return src


def _inner(code: str) -> str:
    """줄인 옛 책갈피 '(async function(){…})();' → 껍데기 안에서 기다리는 한 문장."""
    code = code.strip()
    if not code.startswith("(async function") or not code.endswith("})();"):
        raise ValueError("bad inner code")
    return "await " + code


@lru_cache(maxsize=1)
def _code() -> str:
    one = kkumi.minify(SRC.read_text(encoding="utf-8"))
    st = kkumi.minify(statscap.DAY_SRC.read_text(encoding="utf-8")).replace("@@URL@@", "@@SURL@@").replace("@@KEY@@", "@@SKEY@@")
    so = kkumi.minify(_patched_seoi())
    zl = "const Z=" + one.split("const Z=", 1)[1].split(";", 1)[0] + ";" if "const Z=" in one else ""
    if not zl or so.count(zl) != 1:
        raise ValueError("bad Z")
    so = so.replace(zl, "", 1)  # seoi.js 의 Z 는 껍데기의 Z 와 똑같음 — 한 번만 (안쪽 코드는 바깥 Z 를 씀)
    if one.count("'@@STAT@@';") != 1 or one.count("'@@SEOI@@';") != 1:
        raise ValueError("bad one.js")
    return seoi.pack(one.replace("'@@STAT@@';", _inner(st)).replace("'@@SEOI@@';", _inner(so)))


@lru_cache(maxsize=1)
def version() -> int:
    m = re.search(r"^const V1 = (\d+);", SRC.read_text(encoding="utf-8"), re.M)
    return int(m.group(1)) if m else 0


_URL_OK = re.compile(r"https://[A-Za-z0-9.\-:]+/[A-Za-z0-9_/\-]+")
_KEY_OK = re.compile(r"[A-Za-z0-9_\-]+")


def bookmarklet(seoi_url: str, seoi_key: str, stat_url: str, stat_key: str) -> str:
    """javascript: 주소 — 이웃 관리·통계의 주소·열쇠를 저마다 넣는다 (따옴표·역슬래시 없는 글자만)."""
    if not all(_URL_OK.fullmatch(u) for u in (seoi_url, stat_url)) or not all(_KEY_OK.fullmatch(k) for k in (seoi_key, stat_key)):
        raise ValueError("bad endpoint or key")
    code = (_code().replace("@@SURL@@", stat_url).replace("@@SKEY@@", stat_key)
            .replace("@@URL@@", seoi_url).replace("@@KEY@@", seoi_key).replace("@@BLOGS@@", seoi.blogs_json()))
    return "javascript:" + kkumi.encode_short(code)


def view(db: Any, public_url: str, *, now: float | None = None, tz: ZoneInfo | None = None) -> dict[str, Any]:
    """연결 탭 설치 칸 (로그인 아래에서만 — 열쇠가 든 코드). note·go 는 이웃 관리 칸과 같은 오늘 수."""
    base = public_url.rstrip("/")
    code = bookmarklet(base + seoi.PATH, seoi.key(db), base + statscap.PATH, statscap.key(db))
    if len(code) > LEN_MAX:
        raise ValueError("bookmarklet too long")
    out: dict[str, Any] = {"code": code, "len": len(code), "steps": seoi.steps(STEPS), "version": version()}
    if tz is not None:
        sv = seoi.view(db, public_url, now=now, tz=tz)
        for k in ("note", "go"):
            if sv.get(k):
                out[k] = sv[k]
        out["got"] = statscap.received(db, now or time.time(), tz)["line"]  # 11/17 통계 확인 한 줄 (블로그·화면 날짜)
    return out
