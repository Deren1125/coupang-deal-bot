"""대시보드 앱(Sns-board) API — /dashboard/api/… (spec §5.1). 모두 /dashboard 앞부분 로그인(guard_prefix) 아래.

  GET  /dashboard/                      앱 화면 (C 의 shell.page — boot JSON 을 넣어서). 없으면 예전 화면
  GET  /dashboard/go/<iid>              → 302 /dashboard/#/i/<iid> (알림·카톡의 바로가기 — 로그인 화면을 거쳐도 next 로 돌아옴)
  GET  /dashboard/api/state             State (ETag · If-None-Match → 304). 1분 60번
  GET  /dashboard/api/item/<iid>        Item 하나 (긴 칸까지)
  POST /dashboard/api/action            {"action","args","cid","ctoken"} → actions.ActionDesk
  GET  /dashboard/api/actions?ids=a_1,a_2   {"actions":[ActionStatus]} (20개까지)
  GET  /dashboard/api/today[?day=yesterday] 오늘 한 일 시트 (today.TodayView — 열 때만). 1분 30번
  POST /dashboard/api/upload            본문 = 파일 하나 (X-Upload-Kind: photo|ledger) → {"upload_id","w","h","bytes","thumb"}
  GET  /dashboard/upload/<id>/thumb     받은 사진 썸네일 · GET /dashboard/thumb/<이름>  Blog-Auto 사진 자리 썸네일
  GET  /dashboard/pic/<이름>            링크에서 고른 후보 사진 원본 (blog_view/pics — 꾹 눌러 저장·복사)
  POST /dashboard/api/push/subscribe    {"endpoint","keys":{"p256dh","auth"},"label"} → {"id"}
  POST /dashboard/api/push/unsubscribe  {"id"} 또는 {"endpoint"}   ·  POST /dashboard/api/push/test {} → {"sent","failed"}
  GET  /dashboard/api/push/key          {"key": VAPID 공개 키}
  GET  /dashboard/api/digest/<id>       {"id","title","at","sections":[{"label","sentences"}]}
  GET  /dashboard/api/deal/hot?n=  ·  GET /dashboard/api/deal/find?q=   (/hot · /find)
  GET  /dashboard/api/stats.html        통계 카드 조각 (폼·스크립트 없음)
  GET  /dashboard/connect/<threads|instagram>   스레드·인스타 다시 연결 → 302 승인 화면 (같은 사이트에서 연 것만)
  GET  /dashboard/sw.js · /dashboard/static/{app.js,app.css,edit.js,product.js,sched.js,badge-72.png}   앱 파일 (로그인 없이 — 데이터 없음)

상태를 바꾸는 POST 는 모두: 세션마다 다른 X-Dash-Token + Sec-Fetch-Site(same-origin 또는 없음) + Origin(있으면 대시보드 주소만).
응답·기록에 비밀값(토큰·코드·카카오 주소·푸시 주소·CSRF 키)을 넣지 않는다.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import quote

from dealbot.dashboard import webapp
from dealbot.dashboard.actions import TEXTS, ActionDesk, Caller, RateWindow
from dealbot.dashboard.auth import client_key
from dealbot.dashboard.blogauto import PIC_FILE_RE
from dealbot.dashboard.login import (
    SESSION_COOKIE,
    SessionCsrf,
    cookie_values,
    session_cookie,
    session_hash,
)
from dealbot.dashboard.uploads import ID_RE as UPLOAD_ID_RE
from dealbot.dashboard.uploads import UploadError, Uploads, max_bytes, media_type
from dealbot.notify import iid as iidmod
from dealbot.notify import webpush

log = logging.getLogger(__name__)

JT = "application/json; charset=utf-8"
API = "/dashboard/api"
THUMB_RE = re.compile(r"^[a-z0-9-]{1,64}\.(jpg|webp)$")
PIC_MAX_BYTES = 8_000_000
APP_FILES = {"app.js": "text/javascript; charset=utf-8", "app.css": "text/css; charset=utf-8", "badge-72.png": "image/png",
             "edit.js": "text/javascript; charset=utf-8",  # edit.js: 사진 고치기·편집기 (10/19, 처음 쓸 때만)
             "product.js": "text/javascript; charset=utf-8",  # product.js: 제품 테스트 (10/21, 처음 열 때만)
             "sched.js": "text/javascript; charset=utf-8",  # sched.js: 일정 화면 (10/21, 처음 열 때만)
             "threads.js": "text/javascript; charset=utf-8"}  # threads.js: 스레드 화면 (10/21, 처음 열 때만)
SW_PATH = "/dashboard/sw.js"
ACTION_BODY_MAX = 64 * 1024
PUSH_BODY_MAX = 4096
TEST_BODY_MAX = 1024
LABELS = ("Galaxy", "iPhone", "iPad", "컴퓨터", "휴대폰")


def jresp(status: int, obj: Any, headers: dict[str, str] | None = None) -> tuple[int, str, str, dict[str, str]]:
    return status, json.dumps(obj, ensure_ascii=False, separators=(",", ":")), JT, dict(headers or {})


def err(status: int, code: str, text: str | None = None) -> tuple[int, str, str, dict[str, str]]:
    return jresp(status, {"status": "error", "code": code, "text": text or TEXTS.get(code, TEXTS["failed"])})


def device_label(ua: str) -> str:
    """User-Agent → 'Galaxy' · 'iPhone' · 'iPad' · '컴퓨터' (기기 이름 칸)."""
    u = (ua or "").lower()
    if "ipad" in u or ("macintosh" in u and "mobile" in u):
        return "iPad"
    if "iphone" in u:
        return "iPhone"
    if "samsung" in u or "sm-" in u or "galaxy" in u:
        return "Galaxy"
    if "android" in u:
        return "휴대폰"
    return "컴퓨터"


def boot_json(state: dict[str, Any]) -> str:
    """<script type="application/json" id="boot"> 안에 넣을 글 ('</' 와 꺾쇠를 막아 페이지를 깨지 않게)."""
    raw = json.dumps(state, ensure_ascii=False, separators=(",", ":"))
    return raw.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026").replace("\u2028", "\\u2028").replace(
        "\u2029", "\\u2029")


class DashApi:
    def __init__(self, dash: Any, bot: Any) -> None:
        self.dash = dash
        self.bot = bot
        self.csrf = SessionCsrf(dash.db)
        self.uploads = Uploads(dash.db, bot.settings.data_dir)
        key = hashlib.sha256(b"ctoken|" + self.csrf._secret()).digest()
        self.state_rate = RateWindow(60, 60.0)
        self.today_rate = RateWindow(30, 60.0)
        self.upload_rate = RateWindow(60, 600.0)
        self.sub_rate = RateWindow(10, 3600.0)
        self.test_rate = RateWindow(5, 600.0)  # /api/push/test 와 /api/action push.test 가 같이 센다
        self.desk = ActionDesk(bot, outbox=bot.actions_out, uploads=self.uploads, key=key, audit=self.audit,
                               facts=self._facts, test_rate=self.test_rate)

    # ---------------------------------------------------------- 누가
    def caller(self, headers: dict[str, str]) -> tuple[Caller, str, str | None]:
        """(Caller, CSRF 를 셀 이름, 세션 쿠키 값). 세션 쿠키가 먼저, 없으면 기본 인증 ('basic:' + 지문)."""
        token = self.dash.sessions.first_valid(cookie_values(headers, SESSION_COOKIE))
        client8 = hashlib.sha256(client_key(headers).encode()).hexdigest()[:8]
        if token is not None:
            h = session_hash(token)
            return Caller(session=h[:8], client8=client8), h, token
        return Caller(session="basic", client8=client8), "basic:" + self.dash.sessions.fingerprint, None

    def csrf_token(self, headers: dict[str, str]) -> str:
        return self.csrf.token(self.caller(headers)[1])

    def allowed_origins(self) -> set[str]:
        s = self.bot.settings
        out = {s.dashboard.public_url.rstrip("/")}
        for host in s.secrets.served_hosts:
            out.add(f"https://{host}")
        return out

    def same_site(self, headers: dict[str, str]) -> bool:
        site = (headers.get("sec-fetch-site") or "").strip().lower()
        if site not in ("", "same-origin"):
            return False
        origin = (headers.get("origin") or "").strip().rstrip("/")
        return not origin or origin in self.allowed_origins()

    def guard(self, headers: dict[str, str]) -> tuple[Caller, str] | tuple[int, str, str, dict[str, str]]:
        """POST 앞 확인: 같은 사이트 + X-Dash-Token. 통과하면 (Caller, ident)."""
        if not self.same_site(headers):
            return err(403, "cross_site", "다른 곳에서 보낸 요청은 받지 않아요")
        caller, ident, _tok = self.caller(headers)
        if not self.csrf.check(ident, headers.get("x-dash-token")):
            return err(403, "csrf", "화면이 오래됐어요 — 다시 불러올게요")
        return caller, ident

    def audit(self, *, session8: str, client8: str, action: str, target: str, args: dict[str, Any], code: str,
              action_id: str | None) -> None:
        with self.dash.db._lock:
            self.dash.db._conn.execute(
                "INSERT INTO dash_audit (ts, session8, client8, action, target, args_redacted, result_code, action_id) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (time.time(), session8[:8], client8[:8], action[:40], target[:64], json.dumps(args, ensure_ascii=False)[:2000],
                 code[:40], action_id))

    def _facts(self, target: str) -> dict[str, Any]:
        post = iidmod.post_of(target)
        if post is None or self.bot.dash_state is None:
            return {}
        export = self.bot.dash_state._export()
        for p in (getattr(export, "v2", None) or {}).get("posts", {}).get(post[0], []):
            if p["pid"] == post[1]:
                return {"owner_edited": bool(p.get("owner_edited"))}
        return {}

    # ---------------------------------------------------------- 등록
    def register(self, web: Any) -> None:
        web.route(f"{API}/state", self.state, with_headers=True)
        web.route_prefix(f"{API}/item/", self.item, with_headers=True)
        web.route_json(f"{API}/action", self.action, max_body=ACTION_BODY_MAX)
        web.route(f"{API}/actions", self.actions, with_headers=True)
        web.route(f"{API}/today", self.today, with_headers=True)
        web.route_stream(f"{API}/upload", self.upload, max_body=lambda h: max_bytes((h.get("x-upload-kind") or "").strip()),
                         tmp_path=self.uploads.tmp_path)
        web.route_prefix("/dashboard/upload/", self.upload_thumb)
        web.route_prefix("/dashboard/thumb/", self.blog_thumb)
        web.route_prefix("/dashboard/pic/", self.blog_pic)
        web.route_json(f"{API}/push/subscribe", self.push_subscribe, max_body=PUSH_BODY_MAX)
        web.route_json(f"{API}/push/unsubscribe", self.push_unsubscribe, max_body=PUSH_BODY_MAX)
        web.route_json(f"{API}/push/test", self.push_test, max_body=TEST_BODY_MAX)
        web.route(f"{API}/push/key", self.push_key)
        web.route_prefix(f"{API}/digest/", self.digest)
        web.route(f"{API}/deal/hot", self.deal_hot)
        web.route(f"{API}/deal/find", self.deal_find)
        web.route(f"{API}/stats.html", self.stats_html)
        web.route_prefix("/dashboard/go/", self.go)
        web.route_prefix("/dashboard/connect/", self.connect, with_headers=True)
        web.route(SW_PATH, self.sw)
        for name in APP_FILES:
            web.route(webapp.STATIC_PREFIX + name, self._app_file(name))

    @staticmethod
    def public_paths() -> list[str]:
        """로그인 없이 여는 앱 파일 (데이터 없음)."""
        return [SW_PATH, *(webapp.STATIC_PREFIX + n for n in APP_FILES)]

    # ---------------------------------------------------------- 화면
    def state_body(self, headers: dict[str, str]) -> dict[str, Any]:
        caller, ident, _tok = self.caller(headers)
        return self.bot.dash_state.state(session=caller.session, csrf=self.csrf.token(ident), logout_t=self.dash.form_token)

    def renew_headers(self, headers: dict[str, str]) -> dict[str, str]:
        """세션이 7일 안에 끝나면 30일로 늘리고 쿠키도 다시."""
        _c, _i, token = self.caller(headers)
        if token is not None and self.dash.sessions.renew(token):
            return {"Set-Cookie": session_cookie(token)}
        return {}

    async def state(self, query: dict[str, str], headers: dict[str, str]) -> Any:
        caller, _ident, _tok = self.caller(headers)
        if not self.state_rate.hit(caller.session):
            return err(429, "rate")
        body = self.state_body(headers)
        etag = self.bot.dash_state.etag(body)
        extra = {"ETag": f'"{etag}"', "Cache-Control": "no-cache", **self.renew_headers(headers)}
        body["etag"] = etag
        if (headers.get("if-none-match") or "").strip().strip('"') == etag:
            return 304, b"", JT, extra
        return jresp(200, body, extra)

    async def item(self, query: dict[str, str], headers: dict[str, str]) -> Any:
        iid = query.get("path", "")
        if not iidmod.ok(iid):
            return err(404, "not_found")
        if iidmod.digest_of(iid) is not None and self.bot.router is not None:
            d = self.bot.router.store.digest(iid)
            return jresp(200, d) if d else err(404, "not_found")
        it = self.bot.dash_state.item(iid)
        return jresp(200, it) if it is not None else err(404, "not_found", "이미 처리됐어요")

    async def action(self, body: dict[str, Any], headers: dict[str, str]) -> Any:
        g = self.guard(headers)
        if len(g) == 4:
            return g
        caller, _ident = g  # type: ignore[misc]
        status, obj = await self.desk.run(body, caller)
        return jresp(status, obj)

    async def actions(self, query: dict[str, str], headers: dict[str, str]) -> Any:
        ids = [x for x in (query.get("ids") or "").split(",") if re.fullmatch(r"a_[0-9a-z]{26}", x)][:20]
        try:
            self.bot.actions_out.ingest()  # 고를 때마다 결과를 먼저 읽는다
        except Exception as e:  # noqa: BLE001
            log.warning("dashboard actions ingest failed: %s", e)
        from dealbot.bridge.actions_out import status_of

        out = []
        for aid in ids:
            row = self.bot.actions_out.store.get(aid)
            if row is None:
                continue
            st = status_of(row)
            if row["action"] == "blog.kakao_auth_url" and row["status"] == "ok":
                url = self.bot.actions_out.take_url(aid)
                if url:
                    st["url"] = url  # 한 번만 (저장하지 않음)
            out.append(st)
        return jresp(200, {"actions": out}, {"Cache-Control": "no-store"})

    async def today(self, query: dict[str, str], headers: dict[str, str]) -> Any:
        """오늘 한 일 (주인 10/18) — 로그인(guard_prefix) 아래, 읽기만이라 state 처럼 CSRF 없이."""
        from dealbot.dashboard.today import TodayView

        caller, _ident, _tok = self.caller(headers)
        if not self.today_rate.hit(caller.session):
            return err(429, "rate")
        try:
            self.bot.actions_out.ingest()  # Blog-Auto 결과를 먼저 읽는다 (진행 중 → 완료)
        except Exception as e:  # noqa: BLE001
            log.warning("dashboard today ingest failed: %s", e)
        back = 1 if query.get("day") == "yesterday" else 0
        return jresp(200, TodayView(self.bot).view(now=time.time(), back=back), {"Cache-Control": "no-store"})

    # ---------------------------------------------------------- 파일
    async def upload(self, query: dict[str, str], headers: dict[str, str], path: Path) -> Any:
        try:
            g = self.guard(headers)
            if len(g) == 4:
                return g
            caller, _ident = g  # type: ignore[misc]
            if not self.upload_rate.hit(caller.session):
                return err(429, "rate")
            kind = (headers.get("x-upload-kind") or "").strip()
            ctype = media_type(headers)
            try:
                got = await asyncio.to_thread(self.uploads.process, path, kind=kind, ctype=ctype, session=caller.session)
            except UploadError as e:
                self.audit(session8=caller.session, client8=caller.client8, action="upload", target="", args={"kind": kind},
                           code=e.code, action_id=None)
                return err(415 if e.code == "bad_type" else 413 if e.code == "too_big" else 400, "bad_upload", e.text)
            self.audit(session8=caller.session, client8=caller.client8, action="upload", target="", args={"kind": kind},
                       code="done", action_id=None)
            return jresp(200, got)
        finally:
            if path.exists():
                path.unlink(missing_ok=True)

    @staticmethod
    def _file(path: Path | None, ctype: str) -> Any:
        if path is None:
            return 404, "없는 파일", "text/plain; charset=utf-8", {"X-Content-Type-Options": "nosniff"}
        return 200, path.read_bytes(), ctype, {"Cache-Control": "private, max-age=300", "X-Content-Type-Options": "nosniff"}

    async def upload_thumb(self, query: dict[str, str]) -> Any:
        rest = query.get("path", "")
        uid, _, tail = rest.partition("/")
        if tail != "thumb" or not UPLOAD_ID_RE.match(uid):
            return self._file(None, "")
        return self._file(self.uploads.thumb_path(uid), "image/jpeg")

    def _blog_view(self, sub: str, name: str) -> Path | None:
        """blog_view/<sub>/<이름> — 그 폴더 바로 아래의 진짜 파일만 (바로가기·밖으로 나가는 이름은 None)."""
        base = (Path(self.bot.settings.data_dir) / "blog_view" / sub).resolve()
        p = base / name
        try:
            return p if p.is_file() and not p.is_symlink() and p.resolve().parent == base else None
        except OSError:
            return None

    async def blog_thumb(self, query: dict[str, str]) -> Any:
        name = query.get("path", "")
        if not THUMB_RE.match(name):
            return self._file(None, "")
        return self._file(self._blog_view("thumbs", name), "image/webp" if name.endswith(".webp") else "image/jpeg")

    async def blog_pic(self, query: dict[str, str]) -> Any:
        """후보 사진 원본 (로그인 아래 · 이름 규칙 PIC_FILE_RE · 개인 캐시). 크면 휴대폰이 받는 데 오래 걸려 8MB 까지."""
        name = query.get("path", "")
        p = self._blog_view("pics", name) if PIC_FILE_RE.match(name) else None
        try:
            if p is not None and p.stat().st_size > PIC_MAX_BYTES:
                p = None
        except OSError:
            p = None
        ext = name.rsplit(".", 1)[-1]
        return self._file(p, {"png": "image/png", "webp": "image/webp"}.get(ext, "image/jpeg"))

    # ---------------------------------------------------------- 푸시
    async def push_key(self, query: dict[str, str]) -> Any:
        router = self.bot.router
        key = router.public_key if router is not None else None
        return jresp(200, {"key": key}) if key else err(503, "off", "알림 키가 없어요")

    async def push_subscribe(self, body: dict[str, Any], headers: dict[str, str]) -> Any:
        g = self.guard(headers)
        if len(g) == 4:
            return g
        caller, _ident = g  # type: ignore[misc]
        router = self.bot.router
        if router is None:
            return err(503, "off")
        if not self.sub_rate.hit(caller.session):
            return err(429, "rate")
        endpoint = body.get("endpoint")
        keys = body.get("keys") if isinstance(body.get("keys"), dict) else {}
        p256dh, auth = keys.get("p256dh"), keys.get("auth")
        if webpush.endpoint_problem(endpoint) is not None:
            self.audit(session8=caller.session, client8=caller.client8, action="push.subscribe", target="", args={},
                       code="bad_endpoint", action_id=None)
            return err(400, "bad_endpoint", "이 브라우저의 알림 주소는 받을 수 없어요")
        try:
            from cryptography.hazmat.primitives.asymmetric import ec

            from dealbot.notify.vapid import b64u_decode

            point = b64u_decode(str(p256dh))
            ok = len(point) == 65 and point[0] == 0x04 and len(b64u_decode(str(auth))) == 16
            if ok:  # P-256 위의 점인지 (아니면 보낼 때마다 암호화가 깨진다)
                ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), point)
        except (ValueError, TypeError):
            ok = False
        if not ok:
            return err(400, "bad_args")
        label = body.get("label") if body.get("label") in LABELS else device_label(headers.get("user-agent") or "")
        sid = router.store.add_sub(endpoint=str(endpoint), endpoint_hash=webpush.endpoint_hash(str(endpoint)), p256dh=str(p256dh),
                                   auth=str(auth), label=label, session=caller.session, now=time.time())
        self.audit(session8=caller.session, client8=caller.client8, action="push.subscribe", target=sid, args={"label": label},
                   code="done", action_id=None)
        log.info("web push: 기기 등록 (%s, %s)", label, webpush.log_name(str(endpoint)))
        return jresp(200, {"id": sid})

    async def push_unsubscribe(self, body: dict[str, Any], headers: dict[str, str]) -> Any:
        g = self.guard(headers)
        if len(g) == 4:
            return g
        caller, _ident = g  # type: ignore[misc]
        router = self.bot.router
        if router is None:
            return err(503, "off")
        sub = None
        if isinstance(body.get("id"), str):
            sub = router.store.sub(body["id"])
        elif isinstance(body.get("endpoint"), str):
            sub = router.store.sub_by_hash(webpush.endpoint_hash(body["endpoint"]))
        if sub is None:
            return jresp(200, {"status": "ok", "code": "done"})
        if sub.get("session") != caller.session:  # 다른 기기 → 확인 창 (★)
            if body.get("ctoken") is None:
                tok = self.desk.tokens.issue(caller.session, "push.unsubscribe", {"id": sub["id"]})
                return jresp(409, {"status": "confirm", "ctoken": tok, "title": f"{sub.get('label') or '그 기기'} 알림을 끌까요?",
                                   "body": "그 기기에는 알림이 더 가지 않아요.", "ok": "끄기", "danger": False})
            if not self.desk.tokens.check(body.get("ctoken"), caller.session, "push.unsubscribe", {"id": sub["id"]}):
                return err(409, "confirm_expired")
        router.store.drop_sub(sub["id"])
        self.audit(session8=caller.session, client8=caller.client8, action="push.unsubscribe", target=sub["id"], args={},
                   code="done", action_id=None)
        return jresp(200, {"status": "ok", "code": "done"})

    async def push_test(self, body: dict[str, Any], headers: dict[str, str]) -> Any:
        g = self.guard(headers)
        if len(g) == 4:
            return g
        caller, _ident = g  # type: ignore[misc]
        router = self.bot.router
        if router is None:
            return err(503, "off")
        if not self.test_rate.hit(caller.session):
            return err(429, "rate")
        got = await router.push_test()
        self.audit(session8=caller.session, client8=caller.client8, action="push.test", target="", args={},
                   code="pushed" if got["sent"] else ("no_device" if got.get("kakao") else "failed"), action_id=None)
        return jresp(200, got)

    # ---------------------------------------------------------- 그 밖
    async def digest(self, query: dict[str, str]) -> Any:
        did = query.get("path", "")
        router = self.bot.router
        d = router.store.digest(did) if router is not None and iidmod.digest_of(did) else None
        if d is None:
            return err(404, "not_found")
        return jresp(200, {"id": d["id"], "title": d["title"], "at": d["at"], "sections": d["sections"]})

    async def deal_hot(self, query: dict[str, str]) -> Any:
        raw = (query.get("n") or "5").strip()
        n = int(raw) if raw.isdigit() and 1 <= int(raw) <= 100 else 5
        return jresp(200, {"items": self.bot.hot_view(n)})

    async def deal_find(self, query: dict[str, str]) -> Any:
        q = " ".join((query.get("q") or "").split())
        if not q or len(q) > 40:
            return err(400, "bad_args", "찾을 낱말을 40자 안으로 넣어 주세요")
        return jresp(200, {"items": self.bot.find_view(q)})

    async def stats_html(self, query: dict[str, str]) -> Any:
        """통계 카드 조각 — render.stats_fragment(C) 가 있으면 그것, 없으면 예전 카드 그대로 (폼 없이)."""
        from dealbot.dashboard import render

        views = self.dash.views()
        frag = getattr(render, "stats_fragment", None)
        if frag is not None:
            html = frag(views=views, today=self.dash.ctx.today(), tz=self.dash.ctx.tz, spark_days=self.dash.cfg.spark_days)
        else:
            html = "".join(render.card_html(v, token="", today=self.dash.ctx.today(), tz=self.dash.ctx.tz,
                                            spark_days=self.dash.cfg.spark_days) for v in views)
            html = re.sub(r"<form\b.*?</form>", "", html, flags=re.S)
        return 200, html, "text/html; charset=utf-8", {"Cache-Control": "no-store"}

    async def go(self, query: dict[str, str]) -> Any:
        """알림·카톡의 바로가기: /dashboard/go/<iid> → /dashboard/#/i/<iid> (할 일 묶음은 #/todo)."""
        iid = query.get("path", "")
        if iid == iidmod.TODO_IID:
            return 302, "/dashboard/#/todo"
        if not iidmod.ok(iid):
            return 302, "/dashboard/"
        return 302, f"/dashboard/#/i/{quote(iid)}"

    async def connect(self, query: dict[str, str], headers: dict[str, str]) -> Any:
        """스레드·인스타 다시 연결: 같은 사이트에서 연 것만 (다른 사이트 링크로 주인 브라우저에서 OAuth 를 시작하지 못하게)."""
        platform = query.get("path", "")
        if (headers.get("sec-fetch-site") or "").strip().lower() not in ("", "same-origin") or platform not in ("threads", "instagram"):
            return 403, "다른 곳에서 연 링크는 받지 않아요", "text/plain; charset=utf-8", {}
        url = await (self.bot.threads_auth_link() if platform == "threads" else self.bot.instagram_auth_link())
        caller = self.caller(headers)[0]
        self.audit(session8=caller.session, client8=caller.client8, action="connect", target=platform, args={},
                   code="redirect" if url else "failed", action_id=None)  # 주소(state 포함)는 남기지 않는다
        if not url:
            return 302, "/dashboard/#/connect"
        log.info("%s auth redirect", platform)
        return 302, url, None, {"Referrer-Policy": "no-referrer", "Cache-Control": "no-store"}

    async def sw(self, query: dict[str, str]) -> Any:
        p = webapp.STATIC_DIR / "sw.js"
        if not p.is_file():
            return 404, "없는 파일", "text/plain; charset=utf-8", {}
        return 200, p.read_bytes(), "text/javascript; charset=utf-8", {
            "Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff", "Service-Worker-Allowed": "/dashboard/"}

    @staticmethod
    def _app_file(name: str) -> Any:
        async def handler(query: dict[str, str]) -> Any:
            p = webapp.STATIC_DIR / name
            if not p.is_file():
                return 404, "없는 파일", "text/plain; charset=utf-8", {"X-Content-Type-Options": "nosniff"}
            return 200, p.read_bytes(), APP_FILES[name], {"Cache-Control": "no-cache", "X-Content-Type-Options": "nosniff"}

        return handler

