"""이벤트 참여 (쌀먹 — 돈좀요 이벤트 자동 응모) 를 Sns-board 에 (주인 11/14 "쌀먹을 Sns-board 에 합치자, 지금 쌀먹 대시보드는 예비로 놔두자").
화면에 보이는 이름은 '이벤트 참여' (주인 11/14) — 코드·주소·헤더 이름은 쌀먹 쪽 명세(ssalmuk docs/SNSBOARD.md) 그대로.

쌀먹 서버(perpfarm, 테일스케일 안)는 밖으로만 부른다 — Sns-board 가 쌀먹을 부르는 일은 없다:
  POST /dashboard/api/ssalmuk/push      상태 전체 (덮어쓰기 + 받은 시각) · v==1, app=="ssalmuk" 아니면 400
  GET  /dashboard/api/ssalmuk/commands  아직 ack 안 된 명령 (오래된 순, 50개까지)
  POST /dashboard/api/ssalmuk/ack       {"ids": [...], "results": {id: "ok" | 오류 글}} → 그 명령을 지움
  GET  /dashboard/api/ssalmuk/search    ?type=blog|news|webkr&q=&display=1-20&sort=sim|date → 네이버 검색 Open API 를 딜봇 키로 대신 (딜봇 키가 401/403 이면 Blog-Auto .env 키로 이어서 · state 의 search_proxy.keysrc) (주인 11/14 —
                                        쌀먹 서버에 키를 옮기지 않음). {"items":[{title,link,description,postdate|pubDate,bloggername}],"total"}
                                        키 1개당 1분 60번 · 하루 300번 (KST, kv ssalmuk_search_day) · 넘으면 429 {"code":"limit"} ·
                                        네이버가 401/429/5xx 등을 주면 502 {"code":"naver_error","status"} · 딜봇에 키가 없으면 503 {"code":"no_key"}
  이 넷은 로그인·CSRF 없이 X-Ssalmuk-Key 로만 (해시끼리 hmac.compare_digest — 상수 시간) · 틀리면 401 {"code":"bad_key"} ·
  몸 512KB 까지 · 같은 손님 1분 120번.
화면(로그인 + 같은 사이트 + X-Dash-Token — DashApi.guard):
  GET  /dashboard/api/ssalmuk/state     {"state", "receivedAt", "pending", "stale", "results"} (no-store — 주인 이름·전화가 들어 있음)
  POST /dashboard/api/ssalmuk/act       {"op": "run"|"done"|"profile", "args": {...}} → 명령 쌓기 (done 은 받아 둔 상태에도 먼저)
  GET  /dashboard/api/ssalmuk/key       {"key", "url", "at"} — 연결 칸을 펼칠 때만 (no-store)
  POST /dashboard/api/ssalmuk/rotate    새 키 (예전 키는 바로 안 받음 — 쌀먹 예비 대시보드에 다시 붙여넣기)
저장은 딜봇 DB kv (ssalmuk_key · ssalmuk_state · ssalmuk_cmds · ssalmuk_results). 받은 상태·키는 로그에 남기지 않는다.
"""

from __future__ import annotations

import hashlib
import html
import hmac
import json
import logging
import os
import re
import secrets
import time
from collections.abc import Callable
from pathlib import Path
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

log = logging.getLogger(__name__)

BASE = "/dashboard/api/ssalmuk"
PUSH_PATH = f"{BASE}/push"
COMMANDS_PATH = f"{BASE}/commands"
ACK_PATH = f"{BASE}/ack"
STATE_PATH = f"{BASE}/state"
ACT_PATH = f"{BASE}/act"
KEY_PATH = f"{BASE}/key"
ROTATE_PATH = f"{BASE}/rotate"
# 로그인 없이 여는 것 — 이 넷뿐 (정확한 경로)
SEARCH_PATH = f"{BASE}/search"
PUBLIC_PATHS = (PUSH_PATH, COMMANDS_PATH, ACK_PATH, SEARCH_PATH)

BODY_MAX = 512 * 1024
ACT_BODY_MAX = 64 * 1024
KEY_HEADER = "x-ssalmuk-key"
KV_KEY = "ssalmuk_key"
KV_STATE = "ssalmuk_state"
KV_CMDS = "ssalmuk_cmds"
KV_RESULTS = "ssalmuk_results"
STALE_AFTER = 20 * 60  # 20분 넘게 push 가 없으면 '연결 끊김' (쌀먹은 적어도 10분마다 보냄)
CMD_KEEP = 86400  # 명령 보관은 하루
CMD_MAX = 200
CMD_LIST = 50
RESULTS_KEEP = 30
OPS = ("run", "done", "profile")
# 쌀먹 sync.js PROFILE_KEYS 와 같게 (점 키 그대로)
PROFILE_KEYS = ("name", "phone", "email", "birth", "gender", "zipcode", "address", "addressDetail", "shareUrl", "surveyMode",
                "watchVideoSeconds", "agreeOptional", "sns.nickname", "sns.instagram", "sns.instagramUrl", "sns.youtube", "sns.blog",
                "demographics.age", "demographics.region", "demographics.job", "commentTemplates", "quizAnswers", "customAnswers")
SEARCH_TYPES = ("blog", "news", "webkr")
SEARCH_URL = "https://openapi.naver.com/v1/search/{type}.json"
SEARCH_TIMEOUT = 8.0
SEARCH_PER_MIN = 60
SEARCH_PER_DAY = 300
SEARCH_Q_MAX = 100
KV_SEARCH_DAY = "ssalmuk_search_day"
KV_SEARCH_KEYSRC = "ssalmuk_search_keysrc"  # 'dealbot' | 'blog' — 어느 키가 통했는지 (값만, 비밀 아님)
KEY_RECHECK = 86400.0  # 블로그 키로 넘어간 뒤에도 딜봇 키를 하루 한 번 다시 봄
_TAG_RE = re.compile(r"<[^>]*>")
OP_LABEL = {"run": "지금 확인하기", "done": "완료 표시", "profile": "내 정보 저장"}


KST = timezone(timedelta(hours=9))


def _plain(v: Any) -> str:
    """네이버 검색 글의 <b> 같은 태그를 지우고 &quot; 같은 글자 표현을 되돌림."""
    return html.unescape(_TAG_RE.sub("", str(v or ""))).strip()


def search_items(data: Any) -> tuple[list[dict[str, str]], int]:
    raw = data.get("items") if isinstance(data, dict) else None
    items: list[dict[str, str]] = []
    for x in raw if isinstance(raw, list) else []:
        if not isinstance(x, dict):
            continue
        it = {"title": _plain(x.get("title")), "link": str(x.get("link") or ""), "description": _plain(x.get("description"))}
        for k in ("postdate", "pubDate", "bloggername"):
            if x.get(k):
                it[k] = _plain(x[k])
        items.append(it)
    total = data.get("total") if isinstance(data, dict) else None
    return items, total if isinstance(total, int) else len(items)


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _digest(v: str) -> bytes:
    return hashlib.sha256(v.encode("utf-8")).digest()


def key_ok(given: str | None, stored: str | None) -> bool:
    """X-Ssalmuk-Key 확인 — 길이가 드러나지 않게 해시끼리 상수 시간 비교. 키가 없거나 비었으면 늘 거절."""
    ok = hmac.compare_digest(_digest(given or ""), _digest(stored or ""))
    return bool(given) and bool(stored) and ok


def _clean_profile(p: Any) -> dict[str, Any] | None:
    if not isinstance(p, dict):
        return None
    out: dict[str, Any] = {}
    for k in PROFILE_KEYS:
        if k not in p:
            continue
        v = p[k]
        if k == "commentTemplates":
            lines = v if isinstance(v, list) else str(v or "").splitlines()
            out[k] = "\n".join(str(s).strip()[:300] for s in lines[:20] if str(s).strip())
        elif k in ("quizAnswers", "customAnswers"):
            out[k] = {str(a)[:100]: str(b)[:300] for a, b in list(v.items())[:100]} if isinstance(v, dict) else {}
        elif k == "agreeOptional":
            out[k] = bool(v)
        elif k == "watchVideoSeconds":
            try:
                out[k] = max(0, min(120, int(float(v or 0))))
            except (TypeError, ValueError):
                out[k] = 0
        else:
            out[k] = str(v if v is not None else "").strip()[:300]
    return out


class BlogKeyFile:
    """Blog-Auto .env 에서 NAVER_CLIENT_ID/SECRET 두 줄만 읽음. 처음 쓸 때 읽고 메모리에 두며 파일 수정 시각이 바뀌면 다시 읽음."""

    def __init__(self, path: Callable[[], str]) -> None:
        self._path = path
        self._sig: tuple[str, int] | None = None
        self._pair: tuple[str, str] | None = None

    def get(self) -> tuple[str, str] | None:
        try:
            p = Path(self._path()).expanduser()
            sig = (str(p), os.stat(p).st_mtime_ns)
        except (OSError, ValueError):
            self._sig = self._pair = None
            return None
        if sig != self._sig:
            self._sig, self._pair = sig, self._parse(p)
        return self._pair

    @staticmethod
    def _parse(p: Path) -> tuple[str, str] | None:
        vals: dict[str, str] = {}
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("export "):
                line = line[7:].lstrip()
            name, eq, v = line.partition("=")
            name = name.strip()
            if eq and name in ("NAVER_CLIENT_ID", "NAVER_CLIENT_SECRET"):
                v = v.strip()
                if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
                    v = v[1:-1]
                if v:
                    vals[name] = v
        i, s = vals.get("NAVER_CLIENT_ID"), vals.get("NAVER_CLIENT_SECRET")
        return (i, s) if i and s else None


class Ssalmuk:
    """kv 에 둔 상태·명령·키. clock 은 시험용."""

    def __init__(self, db: Any, *, clock: Callable[[], float] = time.time) -> None:
        self.db = db
        self.clock = clock

    # ------------------------------------------------------------ 키
    def key(self) -> str:
        k = self.db.kv_get(KV_KEY)
        if not k:
            k = self.rotate()
        return json.loads(k)["key"] if k.startswith("{") else k

    def key_at(self) -> str | None:
        raw = self.db.kv_get(KV_KEY) or ""
        return json.loads(raw).get("at") if raw.startswith("{") else None

    def rotate(self) -> str:
        """새 연결 키 (32바이트 무작위 → 64글자). 예전 키는 바로 안 받는다."""
        raw = json.dumps({"key": secrets.token_hex(32), "at": _iso(self.clock())})
        self.db.kv_set(KV_KEY, raw)
        return raw

    def machine_ok(self, headers: dict[str, str]) -> bool:
        raw = self.db.kv_get(KV_KEY) or ""  # 키가 아직 없으면 만들지 않고 거절 (화면에서 연결 칸을 열 때 생김)
        stored = json.loads(raw).get("key") if raw.startswith("{") else raw
        return key_ok((headers.get(KEY_HEADER) or "").strip(), stored)

    # ------------------------------------------------------------ 상태
    def _load(self, k: str, default: Any) -> Any:
        try:
            v = json.loads(self.db.kv_get(k) or "null")
        except ValueError:
            return default
        return default if v is None else v

    def push(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if body.get("v") != 1 or body.get("app") != "ssalmuk":
            return 400, {"ok": False, "code": "bad_body", "text": "v=1 · app=ssalmuk 상태만 받아요"}
        now = self.clock()
        self.db.kv_set(KV_STATE, json.dumps({"state": body, "receivedAt": _iso(now), "ts": now}, ensure_ascii=False))
        return 200, {"ok": True}

    def _cmds(self) -> list[dict[str, Any]]:
        now = self.clock()
        cmds = self._load(KV_CMDS, [])
        return [c for c in cmds if isinstance(c, dict) and now - float(c.get("ts") or 0) < CMD_KEEP] if isinstance(cmds, list) else []

    def commands(self) -> dict[str, Any]:
        return {"commands": [{k: c[k] for k in ("id", "op", "args", "at")} for c in self._cmds()[:CMD_LIST]]}

    def ack(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        ids = body.get("ids")
        results = body.get("results") or {}
        if not isinstance(ids, list) or not isinstance(results, dict):
            return 400, {"ok": False, "code": "bad_body", "text": "ids 목록이 필요해요"}
        gone = {str(i) for i in ids[:500]}
        cmds = self._cmds()
        done = [c for c in cmds if c["id"] in gone]
        self.db.kv_set(KV_CMDS, json.dumps([c for c in cmds if c["id"] not in gone], ensure_ascii=False))
        if done:
            now = self.clock()
            rs = self._load(KV_RESULTS, [])
            rs = rs if isinstance(rs, list) else []
            for c in done:
                r = results.get(c["id"], "ok")
                rs.append({"id": c["id"], "op": c["op"], "result": str(r)[:200] if r is not None else "ok", "at": _iso(now)})
            self.db.kv_set(KV_RESULTS, json.dumps(rs[-RESULTS_KEEP:], ensure_ascii=False))
        return 200, {"ok": True, "removed": len(done)}

    def search_day_hit(self, limit: int = SEARCH_PER_DAY) -> bool:
        """하루(KST) 300번 — 안 넘었으면 하나 세고 True."""
        day = datetime.fromtimestamp(self.clock(), KST).strftime("%Y-%m-%d")
        saved = self._load(KV_SEARCH_DAY, {})
        n = int(saved.get("n") or 0) if isinstance(saved, dict) and saved.get("d") == day else 0
        if n >= limit:
            return False
        self.db.kv_set(KV_SEARCH_DAY, json.dumps({"d": day, "n": n + 1}))
        return True

    def search_keysrc(self) -> str | None:
        v = self.db.kv_get(KV_SEARCH_KEYSRC)
        return v if v in ("dealbot", "blog") else None

    def set_search_keysrc(self, src: str) -> None:
        if self.search_keysrc() != src:
            self.db.kv_set(KV_SEARCH_KEYSRC, src)

    def search_today(self) -> int:
        day = datetime.fromtimestamp(self.clock(), KST).strftime("%Y-%m-%d")
        saved = self._load(KV_SEARCH_DAY, {})
        return int(saved.get("n") or 0) if isinstance(saved, dict) and saved.get("d") == day else 0

    def view(self) -> dict[str, Any]:
        """화면용 (로그인 아래에서만): 받아 둔 상태 · 받은 시각 · 안 끝난 명령 수 · 끊김 여부 · 최근 처리 결과."""
        saved = self._load(KV_STATE, {})
        ts = saved.get("ts") if isinstance(saved, dict) else None
        return {"state": saved.get("state") if isinstance(saved, dict) else None,
                "receivedAt": saved.get("receivedAt") if isinstance(saved, dict) else None,
                "pending": len(self._cmds()), "stale": stale(ts, self.clock()),
                "results": self._load(KV_RESULTS, [])[-10:],
                "search_proxy": {"keysrc": self.search_keysrc(), "today_count": self.search_today()}}

    # ------------------------------------------------------------ 명령
    def act(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        op = body.get("op")
        args = body.get("args") if isinstance(body.get("args"), dict) else {}
        if op not in OPS:
            return 400, {"status": "error", "code": "bad_op", "text": "모르는 명령이에요"}
        cmds = self._cmds()
        if op == "run":
            args = {}
            same = next((c for c in cmds if c["op"] == "run"), None)
            if same is not None:  # 겹치면 하나만
                return 200, {"status": "ok", "ok": True, "id": same["id"], "dup": True}
        elif op == "done":
            url = args.get("url")
            if not isinstance(url, str) or not url.startswith(("https://", "http://")) or len(url) > 2000:
                return 400, {"status": "error", "code": "bad_args", "text": "이벤트 주소가 이상해요"}
            args = {"url": url, "done": bool(args.get("done"))}
            cmds = [c for c in cmds if not (c["op"] == "done" and c["args"].get("url") == url)]  # 같은 이벤트는 마지막 것만
        else:
            prof = _clean_profile(args.get("profile"))
            if not prof:
                return 400, {"status": "error", "code": "bad_args", "text": "바꿀 내 정보가 없어요"}
            args = {"profile": prof}
        if len(cmds) >= CMD_MAX:
            return 429, {"status": "error", "code": "too_many", "text": "이벤트 참여 서버가 명령을 안 가져가고 있어요 — 연결을 확인해 주세요"}
        now = self.clock()
        cmd = {"id": secrets.token_hex(8), "op": op, "args": args, "at": _iso(now), "ts": now}
        cmds.append(cmd)
        self.db.kv_set(KV_CMDS, json.dumps(cmds, ensure_ascii=False))
        if op == "done":
            self._mark_done(args["url"], args["done"])
        return 200, {"status": "ok", "ok": True, "id": cmd["id"]}

    def _mark_done(self, url: str, done: bool) -> None:
        """화면이 바로 바뀌게 받아 둔 상태에도 먼저 (쌀먹이 처리하고 다시 보내면 그걸로 덮임)."""
        saved = self._load(KV_STATE, {})
        st = saved.get("state") if isinstance(saved, dict) else None
        todo = st.get("todo") if isinstance(st, dict) else None
        if not isinstance(todo, list):
            return
        for t in todo:
            if isinstance(t, dict) and t.get("url") == url:
                t["done"] = done
        left = [t for t in todo if isinstance(t, dict) and not t.get("done")]
        if isinstance(st.get("counts"), dict):
            st["counts"]["left"] = len(left)
            st["counts"]["today"] = sum(1 for t in left if t.get("today"))
        self.db.kv_set(KV_STATE, json.dumps(saved, ensure_ascii=False))


def stale(received_ts: float | None, now: float) -> bool:
    """'연결 끊김': 받은 적이 없거나 20분 넘게 push 가 없음."""
    return received_ts is None or now - float(received_ts) > STALE_AFTER


# ---------------------------------------------------------------- 웹
JT = "application/json; charset=utf-8"
NO_STORE = {"Cache-Control": "no-store"}


def _j(status: int, obj: Any) -> tuple[int, str, str, dict[str, str]]:
    return status, json.dumps(obj, ensure_ascii=False, separators=(",", ":")), JT, dict(NO_STORE)


def register(web: Any, api: Any) -> Ssalmuk:
    """DashApi.register 가 부른다. api = DashApi (guard · caller · audit · bot.settings)."""
    from dealbot.dashboard.actions import RateWindow
    from dealbot.dashboard.auth import client_key

    desk = Ssalmuk(api.dash.db)
    rate = RateWindow(120, 60.0)  # 쌀먹 서버 (같은 손님 1분 120번)
    screen_rate = RateWindow(60, 60.0)
    bad = _j(401, {"code": "bad_key", "text": "연결 키가 맞지 않아요"})

    def machine(headers: dict[str, str]) -> tuple[int, str, str, dict[str, str]] | None:
        if not rate.hit(client_key(headers)):
            return _j(429, {"code": "rate", "text": "너무 자주 불렀어요"})
        if not desk.machine_ok(headers):
            log.info("ssalmuk: wrong key from %s", hashlib.sha256(client_key(headers).encode()).hexdigest()[:8])
            return bad
        return None

    async def push(body: dict[str, Any], headers: dict[str, str]) -> Any:
        return machine(headers) or _j(*desk.push(body))

    async def commands(query: dict[str, str], headers: dict[str, str]) -> Any:
        return machine(headers) or _j(200, desk.commands())

    async def ack(body: dict[str, Any], headers: dict[str, str]) -> Any:
        return machine(headers) or _j(*desk.ack(body))

    search_rate = RateWindow(SEARCH_PER_MIN, 60.0)  # 키 하나당 (손님 주소와 상관없이)
    blog_keys = BlogKeyFile(lambda: api.bot.settings.dashboard.ssalmuk_search_env_file)
    state_rt = {"dealbot_try": 0.0}  # 딜봇 키를 마지막으로 써 본 때 (메모리 — 재시작하면 한 번 다시 봄)

    async def search(query: dict[str, str], headers: dict[str, str]) -> Any:
        import httpx

        denied = machine(headers)
        if denied is not None:
            return denied
        typ = (query.get("type") or "blog").strip()
        q = (query.get("q") or "").strip()
        sort = (query.get("sort") or "sim").strip()
        if typ not in SEARCH_TYPES or not 1 <= len(q) <= SEARCH_Q_MAX or sort not in ("sim", "date"):
            return _j(400, {"code": "bad_query", "text": "type(blog|news|webkr)·q(1~100자)·sort(sim|date) 를 확인해 주세요"})
        try:
            display = max(1, min(20, int(query.get("display") or 10)))
        except ValueError:
            display = 10
        sec = api.bot.settings.secrets
        keys: dict[str, tuple[str, str]] = {}
        if sec.naver_client_id and sec.naver_client_secret:
            keys["dealbot"] = (sec.naver_client_id, sec.naver_client_secret)
        blog = blog_keys.get()
        if blog:
            keys["blog"] = blog
        if not keys:
            return _j(503, {"code": "no_key", "text": "딜봇에 네이버 검색 키가 없어요"})
        if not search_rate.hit("key") or not desk.search_day_hit():
            return _j(429, {"code": "limit", "text": "검색 한도(1분 60번·하루 300번)를 넘었어요"})
        now = desk.clock()
        recheck = now - state_rt["dealbot_try"] >= KEY_RECHECK
        order = ["blog", "dealbot"] if desk.search_keysrc() == "blog" and not recheck else ["dealbot", "blog"]
        status, data = 0, None
        for src in (o for o in order if o in keys):
            if src == "dealbot":
                state_rt["dealbot_try"] = now
            cid, csec = keys[src]
            try:
                resp = await api.bot.http.get(
                    SEARCH_URL.format(type=typ), params={"query": q, "display": display, "sort": sort},
                    headers={"X-Naver-Client-Id": cid, "X-Naver-Client-Secret": csec}, timeout=SEARCH_TIMEOUT)
                status = resp.status_code
                data = resp.json() if status == 200 else None
            except (httpx.HTTPError, ValueError) as e:  # 시간 초과·연결 실패·깨진 답 (키·주소는 로그에 안 남김)
                log.info("ssalmuk search: %s (%s key)", type(e).__name__, src)
                return _j(502, {"code": "naver_error", "status": 0, "text": "네이버 검색이 답하지 않아요"})
            if status in (401, 403):  # 이 키는 막힘 — 다른 키로
                log.info("ssalmuk search: naver HTTP %s (%s key)", status, src)
                continue
            if status == 200:
                desk.set_search_keysrc(src)
            break
        if status != 200:
            log.info("ssalmuk search: naver HTTP %s", status)
            return _j(502, {"code": "naver_error", "status": status, "text": "네이버 검색이 오류를 줬어요"})
        items, total = search_items(data)
        return _j(200, {"items": items, "total": total})

    async def state(query: dict[str, str], headers: dict[str, str]) -> Any:
        caller, _i, _t = api.caller(headers)
        if not screen_rate.hit(caller.session):
            return _j(429, {"status": "error", "code": "rate", "text": "너무 자주 불렀어요 — 잠시 뒤 다시"})
        return _j(200, desk.view())

    async def act(body: dict[str, Any], headers: dict[str, str]) -> Any:
        g = api.guard(headers)
        if len(g) == 4:
            return g
        caller, _ident = g
        status, out = desk.act(body)
        # 기록엔 명령 이름만 (내 정보 값·주소는 남기지 않음)
        api.audit(session8=caller.session, client8=caller.client8, action=f"ssalmuk.{str(body.get('op'))[:12]}", target="",
                  args={}, code=out.get("code") or "done", action_id=None)
        return _j(status, out)

    def key_view() -> dict[str, Any]:
        return {"key": desk.key(), "at": desk.key_at(), "url": api.bot.settings.dashboard.public_url.rstrip("/")}

    async def key(query: dict[str, str], headers: dict[str, str]) -> Any:
        return _j(200, key_view())

    async def rotate(body: dict[str, Any], headers: dict[str, str]) -> Any:
        g = api.guard(headers)
        if len(g) == 4:
            return g
        caller, _ident = g
        desk.rotate()
        api.audit(session8=caller.session, client8=caller.client8, action="ssalmuk.rotate", target="", args={}, code="done",
                  action_id=None)
        return _j(200, key_view())

    web.route_json(PUSH_PATH, push, max_body=BODY_MAX)
    web.route(COMMANDS_PATH, commands, with_headers=True)
    web.route_json(ACK_PATH, ack, max_body=BODY_MAX)
    web.route(SEARCH_PATH, search, with_headers=True)
    web.route(STATE_PATH, state, with_headers=True)
    web.route_json(ACT_PATH, act, max_body=ACT_BODY_MAX)
    web.route(KEY_PATH, key, with_headers=True)
    web.route_json(ROTATE_PATH, rotate, max_body=1024)
    return desk
