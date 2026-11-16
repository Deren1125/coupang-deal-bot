"""X 반응 연속 모드 (주인 11/15 — "X 반응(좋아요·리포스트·답글)을 블로그처럼 한 번에" → 메인이 완전 자동은 말리고 '반자동 연속 모드'로 정함).

화면은 static/xengage.js (SNS 탭 X 칸 카드 → #/sns/xe '조종석'). 오늘 반응할 글 목록(20개까지)을 한 글씩 차례로 보여 준다:
  글 내용 · 답글 초안 2~3개(미리 받아 둠) · [X에서 이 글 열기] · [답글 쓰기](X 공식 글쓰기 창 intent, in_reply_to) · [다음 글].
♥·리포스트·게시는 주인이 X 화면에서 직접 누른다 — 서버·화면·책갈피 어디에도 X 단추를 누르는 코드가 없다 (click·키 이벤트·X 요청 없음).
'다음 글'은 주인이 누를 때만 넘어간다 (시간이 지났다고 저절로 넘어가지 않음).

왜 '조종석'(대시보드 탭)인가 — x.com 글 화면 위 패널이 아니라:
  x.com CSP(connect-src)가 x.com 화면에서 대시보드로 가는 요청을 막아 패널이 초안을 받을 길이 주소(#)뿐이고, 글을 옮길 때마다(새 화면)
  패널이 사라져 책갈피를 글마다 다시 눌러야 한다. 조종석은 책갈피를 목록 만들 때 한 번만 누르고, 초안·쉬는 시간은 서버가 쥔다.
  아이패드에서는 대시보드와 X 를 나란히(Split View) 두면 탭을 오갈 일도 없다.

목록 출처 (서버는 X 에 로그인하지 않고 계정 글도 긁지 않는다):
  · 'X 도우미' 책갈피 (static/xhelper.js): 주인 브라우저의 x.com 프로필·홈·검색 화면에서 누르면 '그때 보이는 글 주소'만 읽어 넘긴다 (열쇠 k)
  · 주인이 붙여넣은 X 글 주소
  화면은 자주 보는 계정(프로필 링크)과 화제 낱말(X 검색 링크)을 보여 줘 주인이 열고 책갈피를 누르기 쉽게만 한다.
  글 내용은 초안을 만들 때 xstudio 의 공개 oEmbed 로 한 번 읽는다 (로그인·키 없음 · 하루 몫은 xstudio 와 같이 셈).

  GET  /dashboard/api/xengage/state    목록 · 반응 쉬는 시간 · 답글 쉬는 시간 · 자주 보는 계정 · 화제 검색 링크 (no-store)
  POST /dashboard/api/xengage/add      {text} → 붙여넣은 글에서 X 글 주소만 골라 목록에 (겹치는 글·이미 한 글·내 글 뺌)
  POST /dashboard/api/xengage/handoff  {k, l: '아이디/글번호,…', h, fo} → 책갈피가 넘긴 보이는 글 (열쇠가 맞을 때만 · h 가 있으면 자주 보는 계정에도)
  POST /dashboard/api/xengage/open     {sid} → 그 글을 X 에서 연다 = 반응 1 (쉬는 시간 60~120초 · 1시간 15 · 하루 40 — 아직이면 429)
  POST /dashboard/api/xengage/step     {sid, op: next|clear} → 다음 글 (연 글을 너무 빨리 넘기면 429 '조금 쉬었다가 n초') · clear = 안 연 글 비우기
  POST /dashboard/api/xengage/draft    {rid, sid, tone} → 답글 초안 (xstudio.draft_replies — 관점 한 줄 규칙·숫자 검사 그대로 · 모델 가드 그대로)
                                       최근에 내준 초안과 비슷한 것은 빼고, 남는 것이 모자라면 다른 말투로 한 번만 다시 쓴다.
모두 로그인 아래 · POST 는 같은 사이트 + X-Dash-Token (DashApi.guard). 답글 한 번 기록·쉬는 시간은 xstudio reply_go 그대로 (화면이 부름).
저장 (kv xengage): 글 번호·아이디·때만. 글 내용·초안은 남기지 않는다 — 비슷한지 보는 값은 글자 두 개씩의 해시 묶음.
하지 않는 것: X 로그인 · X API · 자동 좋아요·리포스트·답글 · 저절로 넘어가기 · 남의 타임라인 긁기.
"""

from __future__ import annotations

import logging
import re
import zlib
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta
from typing import Any

from dealbot.dashboard import xstudio as xs

log = logging.getLogger(__name__)

BASE = "/dashboard/api/xengage"
STATE_PATH, ADD_PATH, HANDOFF_PATH = f"{BASE}/state", f"{BASE}/add", f"{BASE}/handoff"
OPEN_PATH, STEP_PATH, DRAFT_PATH = f"{BASE}/open", f"{BASE}/step", f"{BASE}/draft"
KV = "xengage"  # {"queue": [{"sid", "h", "src"}], "seen": [글 번호…], "rows": [[연 때, 글 번호]…], "next_ok": 때, "sig": [[해시…]…]}
QUEUE_MAX = 20  # 오늘 반응할 글 목록 (주인 11/15: 15~20개)
PER_HANDLE = 3  # 한 계정 글은 목록에 이만큼만 (한 사람에게 몰아서 반응하지 않게)
SEEN_KEEP, ROWS_KEEP, SIG_KEEP = 600, 600, 60
# 페이스는 xstudio 답글과 같은 값 (주인 11/15 — 60~120초 · 1시간 15 · 하루 40)
GAP_MIN, GAP_MAX, HOUR_CAP, DAY_CAP = xs.GAP_MIN, xs.GAP_MAX, xs.HOUR_CAP, xs.DAY_CAP
SIM_MAX = 0.5  # 글자 두 개씩 묶음이 이만큼 넘게 겹치면 '비슷한 초안'
FRESH_MIN = 2  # 비슷한 것을 빼고 이만큼은 남아야 함 (모자라면 한 번 다시)
AUTO_TONES = ("agree", "add", "wit")  # 말투를 안 골랐을 때 글마다 돌아가며 (반박은 주인이 고를 때만)
ADD_TEXT_MAX, LIST_MAX = 8000, 4000
BODY_MAX = 16 * 1024
SEARCH = "https://x.com/search?f=top&q="  # 화제 낱말로 X 검색 (주인이 열어서 보고 책갈피를 누름)
POST_URL_RE = re.compile(r"(?<![\w.-])(?:(?:www|mobile|m)\.)?(?:x|twitter)\.com/(?:#!/)?(\w{1,15})/status(?:es)?/(\d{5,20})", re.I)
PAIR_RE = re.compile(r"^(\w{1,15})/(\d{5,20})$")
NO_HANDLE = {"i", "intent", "search", "home", "explore", "hashtag", "share"}  # 아이디 자리에 오는 X 길 이름
SIM_WARN = "최근에 쓴 초안과 비슷해요 — 내 말로 바꿔서 달아 주세요"


def parse_posts(v: Any) -> list[tuple[str, str]]:
    """붙여넣은 글·책갈피 목록에서 X 글만 → [(아이디, 글 번호)] (나온 순서 · 같은 글은 한 번). 책갈피는 '아이디/글번호,…' 꼴."""
    s = v if isinstance(v, str) else ""
    found = [m.groups() for m in POST_URL_RE.finditer(s)]
    found += [m.groups() for m in (PAIR_RE.match(t.strip()) for t in s.split(",")) if m]
    out: list[tuple[str, str]] = []
    for h, sid in found:
        h = "" if h.lower() in NO_HANDLE else h
        if all(sid != x[1] for x in out):
            out.append((h, sid))
    return out


def post_url(h: str, sid: str) -> str:
    return f"{xs.PROFILE}{h or 'i/web'}/status/{sid}"


def sig(text: str) -> list[int]:
    """비슷한지 볼 값: 빈칸·문장 부호를 뺀 글의 '두 글자씩'을 해시한 묶음 (글 자체는 남기지 않음)."""
    t = re.sub(r"[\W_]+", "", text.lower())
    return sorted({zlib.crc32(t[i:i + 2].encode()) & 0xFFFF for i in range(len(t) - 1)})


def similar(a: list[int], b: list[int]) -> bool:
    x, y = set(a), set(b)
    return bool(x and y) and len(x & y) / len(x | y) >= SIM_MAX


class Engage:
    """반응 목록·쉬는 시간 (kv) + 답글 초안. 시계·난수·AI·글 읽기는 Studio(desk) 것을 쓴다 — 모델 가드도 거기."""

    def __init__(self, desk: xs.Studio, own: Callable[[], str] = lambda: "") -> None:
        self.desk = desk
        self.own = own
        self.on: Callable[[], bool] = lambda: True  # 화면 숨김 스위치 — register 가 설정 dashboard.x_engage 를 건다 (주인 11/16 기본 끔 · API 는 그대로)

    # ---------------------------------------------------------- kv
    def _st(self) -> dict[str, Any]:
        st = self.desk._load(KV, {})
        queue = [{"sid": q["sid"], "h": q["h"] if isinstance(q.get("h"), str) and xs.HANDLE_RE.match(q["h"]) else "", "src": str(q.get("src") or "")[:8]}
                 for q in st.get("queue") or [] if isinstance(q, dict) and isinstance(q.get("sid"), str) and re.fullmatch(r"\d{5,20}", q["sid"])]
        rows = [r for r in st.get("rows") or [] if isinstance(r, list) and len(r) == 2 and isinstance(r[0], (int, float))]
        nxt = st.get("next_ok")
        return {"queue": queue[:QUEUE_MAX], "seen": [s for s in st.get("seen") or [] if isinstance(s, str)], "rows": rows,
                "next_ok": float(nxt) if isinstance(nxt, (int, float)) else 0.0,
                "sig": [s for s in st.get("sig") or [] if isinstance(s, list)]}

    def _save(self, st: dict[str, Any]) -> None:
        st["seen"], st["rows"], st["sig"] = st["seen"][-SEEN_KEEP:], st["rows"][-ROWS_KEEP:], st["sig"][-SIG_KEEP:]
        self.desk._save(KV, st)

    # ---------------------------------------------------------- 쉬는 시간 (글을 X 에서 연 것 = 반응 1)
    def pace(self, st: dict[str, Any] | None = None) -> dict[str, Any]:
        """지금 다음 글을 열어도 되는지. wait_s > 0 이면 그만큼 쉼 (why: gap 글 사이 · hour 한 시간 15 · day 하루 40)."""
        now, st = self.desk.clock(), st or self._st()
        day = xs._day(now)
        today = [r for r in st["rows"] if xs._day(r[0]) == day]
        hour = [r for r in st["rows"] if 0 <= now - r[0] < 3600]
        wait, why = 0.0, ""
        if len(today) >= DAY_CAP:
            d = datetime.fromtimestamp(now, xs.KST)
            wait, why = (d.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)).timestamp() - now, "day"
        elif len(hour) >= HOUR_CAP:
            wait, why = sorted(r[0] for r in hour)[len(hour) - HOUR_CAP] + 3600 - now, "hour"
        elif st["next_ok"] > now:
            wait, why = st["next_ok"] - now, "gap"
        text = {"day": f"오늘은 글 {DAY_CAP}개에 반응했어요 — 내일 다시", "hour": f"한 시간에 {HOUR_CAP}개까지 — {xs._wait_text(wait)} 뒤에",
                "gap": f"조금 쉬었다가 {xs._wait_text(wait)} 뒤에 (글 사이 1~2분 쉬기)"}.get(why, "")
        return {"today": len(today), "hour": len(hour), "day_cap": DAY_CAP, "hour_cap": HOUR_CAP, "wait_s": int(wait + 0.999) if wait > 0 else 0,
                "why": why, "text": text, "opened": [str(r[1]) for r in today][-DAY_CAP:], "gap": [int(GAP_MIN), int(GAP_MAX)]}

    # ---------------------------------------------------------- 목록
    def add(self, posts: list[tuple[str, str]], src: str) -> dict[str, int]:
        """목록에 넣기 — 목록에 있는 글 · 이미 넘긴 글 · 오늘 답글 단 글 · 내 글은 빼고, 한 계정 PER_HANDLE · 모두 QUEUE_MAX 까지."""
        st = self._st()
        own = (self.own() or "").lower()
        have = {q["sid"] for q in st["queue"]} | set(st["seen"]) | set(self.desk.pace()["sids"])
        n = {"added": 0, "dup": 0, "full": 0, "mine": 0}
        for h, sid in posts:
            if own and h.lower() == own:
                n["mine"] += 1
            elif sid in have:
                n["dup"] += 1
            elif len(st["queue"]) >= QUEUE_MAX or (h and sum(1 for q in st["queue"] if q["h"].lower() == h.lower()) >= PER_HANDLE):
                n["full"] += 1
            else:
                st["queue"].append({"sid": sid, "h": h, "src": src})
                have.add(sid)
                n["added"] += 1
        if n["added"]:
            self._save(st)
        return n

    @staticmethod
    def _added_text(n: dict[str, int]) -> str:
        if not sum(n.values()):
            return "X 글 주소를 찾지 못했어요 (x.com/아이디/status/숫자)"
        rest = [f"{label} {n[k]}개" for k, label in (("dup", "이미 있거나 한 글"), ("mine", "내 글"), ("full", "목록이 차서 못 넣은 글")) if n[k]]
        return f"글 {n['added']}개를 반응 목록에 넣었어요" + (f" ({' · '.join(rest)} 뺌)" if rest else "")

    def add_pasted(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        text = body.get("text")
        if not isinstance(text, str) or not text.strip() or len(text) > ADD_TEXT_MAX:
            raise xs.StudioError(400, "bad_args", "X 글 주소를 붙여넣어 주세요 (여러 개면 줄을 바꿔서)")
        posts = parse_posts(text)
        if not posts:
            raise xs.StudioError(400, "bad_args", self._added_text({}))
        n = self.add(posts, "paste")
        return 200, {**self.view(), "text": self._added_text(n), "added": n["added"]}

    def handoff(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """'X 도우미' 책갈피가 주소(#)로 넘긴 '보이는 글' 목록. 열쇠가 맞아야 한다 (남의 링크로는 목록이 바뀌지 않음)."""
        if not self.desk.helper_on():
            return xs.helper_off()
        if not xs.helper_key_ok(self.desk.db, body.get("k")):
            return 403, {"status": "error", "code": "bad_key", "text": "책갈피가 예전 것이에요 — 연결 탭에서 'X 도우미'를 다시 설치해 주세요"}
        raw = body.get("l")
        posts = parse_posts(raw[:LIST_MAX] if isinstance(raw, str) else "")
        if not posts:
            raise xs.StudioError(400, "bad_args", "보이는 글을 읽지 못했어요 — 글이 보이는 화면에서 다시 눌러 주세요")
        text = self._added_text(self.add(posts, "helper"))
        h, own = str(body.get("h") or "").lstrip("@"), self.own() or ""
        mine = bool(own) and h.lower() == own.lower()
        if xs.HANDLE_RE.fullmatch(h) and (mine or all(t["h"].lower() != h.lower() for t in self.desk.targets())):
            try:  # 프로필에서 눌렀으면 xstudio 책갈피의 프로필 동작 그대로: 남의 계정 = 자주 보는 계정에 · 내 계정 = 팔로워 수 기록
                _s, got = self.desk.handoff({"k": body.get("k"), "a": "target", "h": h, "fo": body.get("fo")}, own)
                text += f" · {got['text']}"
            except xs.StudioError:
                pass  # 계정 목록이 찼거나 팔로워 수를 못 읽었어도 글은 넣었음
        return 200, {**self.view(), "text": text}

    def open(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """목록의 글을 X 에서 열 때 한 번 기록 (= 반응 1). 아직 쉬는 중이면 429 — 이미 연 글을 다시 여는 것은 그냥 됨."""
        st, sid = self._st(), xs.status_id(body.get("sid"))
        if not sid or all(q["sid"] != sid for q in st["queue"]):
            raise xs.StudioError(404, "gone", "그 글은 목록에 없어요 — 새로고침해 주세요")
        p = self.pace(st)
        if sid not in p["opened"]:
            if p["wait_s"] > 0:
                return 429, {"status": "error", "code": "pace", "text": p["text"], "pace": p}
            now = self.desk.clock()
            st["rows"].append([now, sid])
            st["next_ok"] = now + self.desk.rng.uniform(GAP_MIN, GAP_MAX)
            self._save(st)
        return 200, {"status": "ok", "pace": self.pace()}

    def step(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        """'다음 글' — 주인이 누를 때만. 방금 연 글을 쉬는 시간 안에 넘기면 429 '조금 쉬었다가 n초'. 안 연 글은 바로 넘어감 (반응이 아님)."""
        st = self._st()
        if body.get("op") == "clear":  # 안 연 글 비우기 (연 글은 '다음 글'로)
            opened = set(self.pace(st)["opened"])
            st["queue"] = [q for q in st["queue"] if q["sid"] in opened]
            self._save(st)
            return 200, self.view()
        sid = xs.status_id(body.get("sid"))
        if not sid or all(q["sid"] != sid for q in st["queue"]):
            raise xs.StudioError(404, "gone", "그 글은 이미 넘어갔어요 — 새로고침해 주세요")
        p = self.pace(st)
        if sid in p["opened"] and p["why"] == "gap":
            return 429, {"status": "error", "code": "pace", "text": p["text"], "pace": p}
        st["queue"] = [q for q in st["queue"] if q["sid"] != sid]
        st["seen"].append(sid)
        self._save(st)
        return 200, self.view()

    # ---------------------------------------------------------- 화면 값
    def view(self) -> dict[str, Any]:
        st, d = self._st(), self.desk
        hot = [{"keyword": k, "url": SEARCH + xs.enc(k)} for k in dict.fromkeys(str(h.get("keyword") or "").strip()[:40] for h in d.hot_list()) if k][:8]
        return {"status": "ok", "v": 1, "queue": [{"sid": q["sid"], "h": q["h"], "url": post_url(q["h"], q["sid"])} for q in st["queue"]],
                "pace": self.pace(st), "replies": d.pace(), "targets": d.targets(), "hot": hot,
                "tones": [["auto", "알아서"]] + [[k, lab] for k, (lab, _g) in xs.TONES.items()],
                "limits": {"queue": QUEUE_MAX, "per_handle": PER_HANDLE, "reply": [xs.REPLY_LO, xs.REPLY_HI], "add": ADD_TEXT_MAX},
                "left": d._left("reply", xs.REPLY_DRAFT_PER_DAY), "links": {"reply": xs.INTENT_REPLY, "profile": xs.PROFILE},
                "model": xs.model_label(d.model), "on": bool(self.on()), "helper": bool(d.helper_on())}

    # ---------------------------------------------------------- 답글 초안 (xstudio 규칙 그대로 + 같은 답글 되풀이 막기)
    async def draft(self, body: dict[str, Any]) -> dict[str, Any]:
        d = self.desk
        xs.assert_writer_model(d.model)  # 게시되는 글은 Opus 까지만 (Studio.ask 도 부를 때마다 다시 봄)
        st, sid = self._st(), xs.status_id(body.get("sid"))
        item = next((q for q in st["queue"] if q["sid"] == sid), None) if sid else None
        if item is None:
            raise xs.StudioError(404, "gone", "그 글은 목록에 없어요 — 새로고침해 주세요")
        tone = body.get("tone") if body.get("tone") in xs.TONES else AUTO_TONES[(len(st["seen"]) + st["queue"].index(item)) % len(AUTO_TONES)]
        url = post_url(item["h"], sid)
        out = await d.draft_replies({"url": url, "text": "", "tone": tone})
        old = self._st()["sig"]
        fresh: list[dict[str, Any]] = []
        sigs: list[list[int]] = []

        def take(rows: list[dict[str, Any]]) -> None:
            for r in rows:
                s = sig(r["text"])
                if len(fresh) < 3 and not any(similar(s, o) for o in old + sigs):
                    fresh.append(r)
                    sigs.append(s)

        take(out["replies"])
        if len(fresh) < FRESH_MIN:  # 최근 초안과 비슷함 → 다른 말투로 한 번만 다시 (읽어 둔 원글로 — 서버가 그 글을 또 읽지 않게)
            again = next(t for t in (*AUTO_TONES, "counter") if t != tone)
            post = out["post"]["text"]
            try:
                more = await d.draft_replies({"url": url, "text": post if 5 <= len(post) < 400 else "", "tone": again})
                take(more["replies"])
            except xs.StudioError as e:
                log.info("xengage draft retry: %s", e.code)
        rows = fresh
        if not rows:  # 다시 써도 비슷하면 처음 것을 경고와 함께 (주인이 내 말로 바꿈)
            rows = [{**r, "warn": [*r.get("warn", []), SIM_WARN]} for r in out["replies"][:FRESH_MIN]]
            sigs = [sig(r["text"]) for r in rows]
        st = self._st()
        st["sig"] += sigs
        self._save(st)
        log.info("xengage draft: tone=%s → %d (새로운 것 %d)", tone, len(rows), len(fresh))
        return {"status": "ok", "sid": sid, "tone": tone, "replies": rows, "post": out["post"], "left": d._left("reply", xs.REPLY_DRAFT_PER_DAY),
                "model": xs.model_label(d.model)}


def register(web: Any, api: Any, desk: xs.Studio) -> Engage:
    """DashApi.register 가 xstudio 다음에 부른다. desk = xstudio.register 가 돌려준 Studio (같은 kv·같은 AI 줄)."""
    from dealbot.dashboard.actions import RateWindow, x_handles

    eng = Engage(desk, lambda: x_handles(api.dash.db).get("revenue") or "")
    eng.on = lambda: bool(getattr(api.bot.settings.dashboard, "x_engage", False))
    view_rate, draft_rate, tap_rate = RateWindow(60, 60.0), RateWindow(xs.DRAFT_PER_MIN, 60.0), RateWindow(60, 60.0)
    slow = xs._j(429, {"status": "error", "code": "rate", "text": "너무 자주 눌렀어요 — 1분 뒤 다시"})

    def audit(caller: Any, action: str, code: str, **args: Any) -> None:
        api.audit(session8=caller.session, client8=caller.client8, action=f"xengage.{action}", target="", args=args, code=code,
                  action_id=None)  # 글·주소는 남기지 않는다

    async def state(query: dict[str, str], headers: dict[str, str]) -> Any:
        caller, _i, _t = api.caller(headers)
        return xs._j(200, eng.view()) if view_rate.hit(caller.session) else slow

    def tapper(name: str, run: Callable[[dict[str, Any]], tuple[int, dict[str, Any]]]) -> Any:
        async def handler(body: dict[str, Any], headers: dict[str, str]) -> Any:
            g = api.guard(headers)
            if len(g) == 4:
                return g
            caller, _ident = g
            if not tap_rate.hit(caller.session):
                return slow
            try:
                status, out = run(body)
            except xs.StudioError as e:
                status, out = e.status, e.body()
            audit(caller, name, str(out.get("code") or "done"), op=str(body.get("op") or "")[:8])
            return xs._j(status, out)

        return handler

    async def draft(body: dict[str, Any], headers: dict[str, str]) -> Any:
        g = api.guard(headers)
        if len(g) == 4:
            return g
        caller, _ident = g
        rid = body.get("rid") if isinstance(body.get("rid"), str) and xs.RID_RE.match(body["rid"]) else ""
        key = f"engage:{caller.session}:{rid}" if rid else ""
        if key not in desk.jobs and not draft_rate.hit(caller.session):
            return slow
        make: Callable[[], Awaitable[dict[str, Any]]] = lambda: eng.draft(body)  # noqa: E731
        try:
            out = await desk.job(key, make)
        except xs.StudioError as e:
            audit(caller, "draft", e.code)
            return xs._j(e.status, e.body())
        audit(caller, "draft", "done")
        return xs._j(200, out)

    web.route(STATE_PATH, state, with_headers=True)
    web.route_json(ADD_PATH, tapper("add", eng.add_pasted), max_body=BODY_MAX)
    web.route_json(HANDOFF_PATH, tapper("handoff", eng.handoff), max_body=BODY_MAX)
    web.route_json(OPEN_PATH, tapper("open", eng.open), max_body=1024)
    web.route_json(STEP_PATH, tapper("step", eng.step), max_body=1024)
    web.route_json(DRAFT_PATH, draft, max_body=1024)
    return eng
