"""주인 알림 한 곳 (주인 10/18 — 대시보드 앱 웹 푸시가 먼저, 카톡은 예비). 딜봇 프로세스만 푸시를 보낸다 (D2).

들어오는 것: 딜봇 AdminNotifier.alert()/resolve()/summary() (같은 프로세스) + Blog-Auto 가 owner_notify/ 에 남긴 알림 (ingest.py).
모두 owner_notices 표에 (src, key) 한 줄 — 대시보드 '할 일' 도 이 표를 읽는다.

정책 (spec §3.2·§3.4):
- 급한 일(URGENT: 링크·사진 부탁, 발행·확인 필요, 연결 끊김)은 바로 푸시, 나머지(DIGEST)는 아침·저녁 정리에만
- 같은 키는 ttl_h 안에 한 번 (해결 뒤 다시 열리면 다시 — 30분 안에 같은 글이면 안 보냄)
- 20초 안에 들어온 급한 일은 한 통으로 묶음 (2개 이상이면 '할 일 N개'), 한 시간에 8통까지 (넘치면 기다렸다 묶어서)
- quiet 알림(대시보드에서 주인이 직접 한 일의 결과·예전 길로 이미 카톡이 간 것·처음 바꿀 때 옮긴 것)은 푸시하지 않음
- Blog-Auto 의 delay_s(늦게 보내기)·group+wait(몇 개 모이거나 오래 기다리면 한 통) 은 푸시만 늦춘다 (할 일 줄은 바로)
- 기기 하나라도 2xx 면 보낸 것. 기기가 없거나 다 실패하면(재시도 30초·2분 뒤) 그 키만 카톡으로 한 번 (kakao_out)
- 해결되면 아직 안 보낸 푸시는 버리고, 카톡에 맡긴 것도 취소 파일을 쓴다
- 재시작해도 보낼 것은 표에 남아 있어 그대로 (여러 개면 한 통으로)
- 같은 머리(err:·loop:)의 DIGEST 가 6시간 안에 3번 오면 한 번은 급한 일로 올림
조용한 시간은 없음 (notify.push_quiet_hours 를 적으면 그 시간엔 ⛔ 말고는 끝날 때까지 기다림).
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import time
from collections.abc import Callable, Iterator
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from dealbot.bridge.kakao_out import KakaoOut
from dealbot.notify import iid as iidmod
from dealbot.notify import vapid, webpush
from dealbot.notify.store import Notice, NotifyStore

log = logging.getLogger(__name__)

# ---------------------------------------------------------------- 급함 표 (두 저장소가 같아야 함 — tests/fixtures/dash_contract/urgency.json)
URGENT_DEALBOT = ("link_request", "review_auth", "review_info", "publish_unsure", "photo_need", "publish_broken",
                  "publisher_stuck", "autolink_stop", "sns_auth", "selfcheck", "claude_login", "update_bad")
URGENT_BLOGAUTO = ("login", "upload_fail", "draft_ready", "ask", "pick_photos", "hot", "owner_edit", "share_confirm",
                   "channel_ask", "channel_review", "double_pub", "link_cards", "own_stats_blocked", "kakao_expiring")
DIGEST_DEALBOT = ("collector_stuck", "linkprice", "sns_zero", "sns_scope", "domain_dns", "ig_ffmpeg", "threads_replies",
                  "published", "link_done", "held", "day", "waiting")
DIGEST_BLOGAUTO = ("job_error", "loop_error", "scan", "scan_auto", "channel_fail", "openchat_fail", "neighbors_config",
                   "comments", "keyzard_login", "restyle_done")
URGENCY: dict[str, str] = {**{k: "DIGEST" for k in (*DIGEST_DEALBOT, *DIGEST_BLOGAUTO)},
                           **{k: "URGENT" for k in (*URGENT_DEALBOT, *URGENT_BLOGAUTO)}}
# 딜봇만 쓰는 정리 종류 (두 저장소 표에는 없음 — 모르는 종류처럼 정리에만, 기록 없이)
LOCAL_DIGEST = ("soldout", "publish_failed", "sns_failed")
SEVS = ("stop", "hot", "todo", "info")
ESCALATE_HEADS = ("err", "loop")
ESCALATE_N, ESCALATE_S = 3, 6 * 3600

COALESCE_S = 20.0
RATE_PER_HOUR = 8
RETRY_S = (30.0, 120.0)  # 5xx·네트워크 실패면 30초, 2분 뒤 다시 — 그래도 안 되면 카톡
DIGEST_TTL = 43200
ACTIVE_DAYS = 30
DEAD_FAILS = 5  # 연달아 이만큼 실패한 기기는 쉬게 (성공하면 다시)
PAYLOAD_MAX = 3000
# 묶음 알림 글 ('링크 넣기 2 · 사진 1')
KIND_VERBS = {"link_request": "링크 넣기", "photo_need": "사진", "pick_photos": "사진", "hot": "사진", "review_auth": "확인",
              "review_info": "확인", "publish_unsure": "채널 확인", "draft_ready": "발행", "ask": "답하기",
              "owner_edit": "직접 고친 글", "share_confirm": "보내기", "channel_ask": "채널", "channel_review": "채널",
              "upload_fail": "다시 올리기", "login": "로그인", "sns_auth": "다시 연결", "kakao_expiring": "카톡 연결"}

_quiet: contextvars.ContextVar[bool] = contextvars.ContextVar("dash_quiet", default=False)


def urgency(kind: str) -> str:
    """종류 → URGENT | DIGEST. 모르는 종류는 DIGEST (기록만)."""
    u = URGENCY.get(kind)
    if u is None and kind in LOCAL_DIGEST:
        return "DIGEST"
    if u is None:
        log.info("notify: 모르는 종류 '%s' → 정리에만", (kind or "")[:40])
        return "DIGEST"
    return u


def sev_of(text: str) -> str:
    """카톡 글 첫머리 표시 → stop | hot | info | todo."""
    t = (text or "").lstrip()
    if t.startswith("⛔"):
        return "stop"
    if t.startswith("🔥"):
        return "hot"
    if t.startswith("ℹ"):
        return "info"
    return "todo"


def _parse_quiet(spec: str) -> tuple[int, int] | None:
    """'00:00-07:00' → (0, 420) 분. 비었거나 이상하면 None."""
    try:
        a, b = (spec or "").split("-")
        ha, ma = (int(x) for x in a.strip().split(":"))
        hb, mb = (int(x) for x in b.strip().split(":"))
    except ValueError:
        return None
    s, e = ha * 60 + ma, hb * 60 + mb
    return (s, e) if 0 <= s < 1440 and 0 <= e <= 1440 and s != e else None


class NotifyRouter:
    def __init__(self, db: Any, *, data_dir: Path | str, http: httpx.AsyncClient, public_url: str, tz: str,
                 kakao: KakaoOut, clock: Callable[[], float] = time.time, quiet_hours: str = "",
                 badge: Callable[[], int] | None = None) -> None:
        self.db = db
        self.store = NotifyStore(db)
        self.data_dir = Path(data_dir)
        self.http = http
        self.public_url = public_url.rstrip("/")
        self.tz = tz
        self.kakao = kakao
        self.clock = clock
        self.quiet_hours = _parse_quiet(quiet_hours)
        self.badge = badge
        self._key: Any = None
        self.key_error: str | None = None

    # ------------------------------------------------------------ 키
    def load_key(self) -> bool:
        """VAPID 키 (없으면 만든다 — 값은 어디에도 남기지 않음). 못 읽으면 푸시는 꺼지고 key_error."""
        if self._key is not None:
            return True
        try:
            self._key = vapid.load_or_create(self.data_dir)
            self.key_error = None
        except vapid.VapidError as e:
            self.key_error = str(e)
            log.error("web push off: %s", e)
        except OSError as e:
            self.key_error = f"알림 키를 만들지 못함 ({type(e).__name__})"
            log.error("web push off: %s", self.key_error)
        return self._key is not None

    @property
    def public_key(self) -> str | None:
        return vapid.public_b64(self._key) if self.load_key() else None

    def link(self, iid: str) -> str:
        return f"{self.public_url}/dashboard/go/{iid}"

    # ------------------------------------------------------------ 대시보드에서 한 일 (조용히)
    @contextlib.contextmanager
    def quiet(self) -> Iterator[None]:
        """이 안에서 열린 알림은 quiet — 주인이 화면을 보고 있으니 푸시하지 않는다 (할 일 줄은 그대로)."""
        token = _quiet.set(True)
        try:
            yield
        finally:
            _quiet.reset(token)

    # ------------------------------------------------------------ 들어오는 것
    def open(self, n: dict[str, Any]) -> str:
        """알림 하나 (걸러 둔 모양: src·key·kind·iid·title·body …). 적용 결과 ('new'·'updated'·'stale' …)."""
        now = self.clock()
        n = dict(n)
        if _quiet.get():
            n["quiet"] = True
        if n.get("urgent") is None:
            n["urgent"] = urgency(str(n.get("kind") or "")) == "URGENT"
        if not n["urgent"] and self._escalate(n, now):
            n["urgent"] = True
        what, _row = self.store.upsert_open(n, now)
        return what

    def _escalate(self, n: dict[str, Any], now: float) -> bool:
        head = str(n.get("key") or "").split(":", 1)[0]
        if head not in ESCALATE_HEADS:
            return False
        kv = f"notify_esc:{n.get('src')}:{head}"
        try:
            data = json.loads(self.db.kv_get(kv) or "{}")
        except ValueError:
            data = {}
        times = [t for t in data.get("t", []) if isinstance(t, int | float) and now - t <= ESCALATE_S] + [now]
        done = data.get("done")
        promote = len(times) >= ESCALATE_N and not (isinstance(done, int | float) and now - done <= ESCALATE_S)
        self.db.kv_set(kv, json.dumps({"t": times[-10:], "done": now if promote else done}))
        return promote

    def resolve(self, src: str, keys: list[str] | tuple[str, ...]) -> int:
        """같은 src 의 줄만 닫는다 (':' 로 끝나면 앞부분). 카톡에 맡긴 것은 취소 파일."""
        gone = self.store.resolve(src, list(keys), self.clock())
        handed = [n.key for n in gone if n.kakao_at is not None]
        if handed and src == "dealbot":
            self.kakao.resolve(handed)
        return len(gone)

    def summary(self, cat: str, text: str, *, replace: bool = False) -> None:
        """저녁 정리에 들어갈 딜봇 한 줄 (오늘·cat 마다 — replace 면 마지막 것만)."""
        now = self.clock()
        day = datetime.fromtimestamp(now, ZoneInfo(self.tz)).date().isoformat()
        kv = f"notify_summary:{day}"
        try:
            data = json.loads(self.db.kv_get(kv) or "[]")
        except ValueError:
            data = []
        if not isinstance(data, list):
            data = []
        if replace:
            data = [d for d in data if isinstance(d, dict) and d.get("cat") != cat]
        if text:
            data.append({"at": now, "cat": cat, "text": text[:300]})
        self.db.kv_set(kv, json.dumps(data[-200:], ensure_ascii=False))

    def summaries(self, day: str) -> list[dict[str, Any]]:
        try:
            data = json.loads(self.db.kv_get(f"notify_summary:{day}") or "[]")
        except ValueError:
            return []
        return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []

    # ------------------------------------------------------------ 기기
    def devices(self, now: float | None = None) -> list[dict[str, Any]]:
        """살아 있는 기기: 켜져 있고 30일 안에 받았거나 30일 안에 등록했고, 연달아 실패한 게 DEAD_FAILS 번 미만인 것
        (한두 번 실패는 재시도 — 성공하면 셈이 0 으로)."""
        now = self.clock() if now is None else now
        cut = now - ACTIVE_DAYS * 86400
        out = []
        for s in self.store.subs():
            if ((s.get("last_ok") or 0) >= cut or s["created"] >= cut) and (s["fails"] or 0) < DEAD_FAILS:
                out.append(s)
        return out

    def nowhere(self, kakao_down: bool) -> bool:
        """알림이 어디로도 가지 않음 (푸시 기기 없음 + 카톡 끊김) — 대시보드 경고 줄."""
        return not self.devices() and kakao_down

    # ------------------------------------------------------------ 보내기
    def _in_quiet(self, now: float) -> bool:
        if self.quiet_hours is None:
            return False
        local = datetime.fromtimestamp(now, ZoneInfo(self.tz))
        m = local.hour * 60 + local.minute
        s, e = self.quiet_hours
        return s <= m < e if s < e else (m >= s or m < e)

    def _held(self, n: Notice, rows: list[Notice], now: float) -> bool:
        """푸시를 아직 미룰까 (delay_s · group+wait · 조용한 시간)."""
        if now < n.opened_at + n.delay_s:
            return True
        if n.group and n.wait_s and n.wait_n:
            same = [r for r in rows if r.group == n.group and r.src == n.src and r.pushed_at is None]
            oldest = min(r.opened_at for r in same) if same else n.opened_at
            if len(same) < n.wait_n and now - oldest < n.wait_s:
                return True
        return n.sev != "stop" and self._in_quiet(now)

    def _pending(self, now: float) -> tuple[list[Notice], list[Notice]]:
        """(보낼 차례인 급한 일, 카톡으로 넘길 실패한 일)."""
        rows = self.store.open_rows(now)
        due, failed = [], []
        for n in rows:
            if not n.urgent or n.quiet:
                continue
            if n.push_ok is False and n.kakao_at is None:
                failed.append(n)
                continue
            if n.pushed_at is not None:
                continue
            if n.next_try is not None and now < n.next_try:
                continue
            if self._held(n, rows, now):
                continue
            due.append(n)
        return due, failed

    def _payload(self, rows: list[Notice], now: float, *, short: bool = False) -> tuple[dict[str, Any], str]:
        badge = self.badge() if self.badge is not None else len(self.store.open_rows(now))
        if len(rows) == 1:
            n = rows[0]
            body = n.body if not short else n.body[:60]
            p = {"v": 1, "type": "item", "title": n.title or n.short, "body": body, "tag": n.iid,
                 "url": f"/dashboard/go/{n.iid}", "urgent": True, "badge": badge, "ts": int(now)}
            return p, n.iid
        counts: dict[str, int] = {}
        for n in rows:
            verb = KIND_VERBS.get(n.kind, "할 일")
            counts[verb] = counts.get(verb, 0) + 1
        body = " · ".join(f"{k} {v}" for k, v in sorted(counts.items(), key=lambda kv: -kv[1]))
        p = {"v": 1, "type": "batch", "title": f"할 일 {len(rows)}개", "body": body[:120], "tag": "todo-batch",
             "url": f"/dashboard/go/{iidmod.TODO_IID}", "urgent": True, "badge": badge, "ts": int(now)}
        return p, "todo-batch"

    def _rate_room(self, now: float) -> int:
        return max(0, RATE_PER_HOUR - len(self.store.pushes_since(now - 3600)))

    async def _deliver(self, payload: dict[str, Any], *, urgent: bool, ttl: int, tag: str,
                       devices: list[dict[str, Any]]) -> tuple[int, int, bool, float | None]:
        """기기마다 한 통. (보낸 수, 실패 수, 다시 해볼 만한 실패만 있었나, Retry-After)."""
        if not self.load_key():
            return 0, len(devices), False, None
        raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        sent = failed = 0
        retry = False
        retry_after: float | None = None
        now = self.clock()
        for s in devices:
            res = await webpush.send(self.http, self._key, self.public_url + "/dashboard", endpoint=s["endpoint"],
                                     p256dh=s["p256dh"], auth=s["auth"], payload=raw, urgent=urgent, ttl=ttl, tag=tag)
            if res.status == 413:  # 너무 큼 → 글을 줄여 한 번 더
                short = dict(payload, body=str(payload.get("body") or "")[:40])
                raw2 = json.dumps(short, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
                res = await webpush.send(self.http, self._key, self.public_url + "/dashboard", endpoint=s["endpoint"],
                                         p256dh=s["p256dh"], auth=s["auth"], payload=raw2, urgent=urgent, ttl=ttl, tag=tag)
            if res.ok:
                sent += 1
                self.store.sub_result(s["id"], ok=True, code=res.status, now=now)
                continue
            failed += 1
            if res.gone:
                self.store.drop_sub(s["id"])
                log.info("web push: 끝난 구독을 지움 (%s)", webpush.log_name(s["endpoint"]))
                continue
            self.store.sub_result(s["id"], ok=False, code=res.status, now=now)
            if res.retryable:
                retry = True
                if res.retry_after is not None:
                    retry_after = res.retry_after
        return sent, failed, retry and sent == 0, retry_after

    def _kakao_text(self, n: Notice) -> str:
        return f"{n.title}\n{n.body}" if n.body and n.body != n.title else (n.title or n.short)

    def _to_kakao(self, rows: list[Notice], now: float) -> int:
        """푸시가 안 간 급한 일 → 카톡 (키마다 한 번). Blog-Auto 가 아직 다리를 모르면 받은편지함 줄로 (kakao_out.py)."""
        done = 0
        for n in rows:
            if n.kakao_at is not None:
                continue
            if self.kakao.send("urgent", key=n.key, text=self._kakao_text(n), link=self.link(n.iid), short=n.short, ttl_h=n.ttl_h):
                done += 1
            self.store.set_push([n.ref], kakao_at=now, push_ok=0, pushed_at=n.pushed_at or now)
        return done

    async def tick(self) -> dict[str, int]:
        """2초마다 (scheduler): 보낼 차례인 급한 일을 한 통으로 보내고, 실패한 일은 카톡으로."""
        now = self.clock()
        out = {"pushed": 0, "kakao": 0}
        due, failed = self._pending(now)
        if failed:
            out["kakao"] += self._to_kakao(failed, now)
        if not due:
            return out
        fresh = [n for n in due if n.push_tries == 0]
        if fresh and not [n for n in due if n.push_tries] and now - min(n.opened_at + n.delay_s for n in fresh) < COALESCE_S:
            return out  # 20초 동안 더 모은다
        devices = self.devices(now)
        if not devices:
            out["kakao"] += self._to_kakao(due, now)
            return out
        if self._rate_room(now) <= 0:
            return out  # 한 시간 8통 — 자리가 나면 묶어서
        payload, tag = self._payload(due, now)
        ttl = int(min(max(n.ttl_h for n in due) * 3600, 86400))
        sent, failed_n, retry, retry_after = await self._deliver(payload, urgent=True, ttl=ttl, tag=tag, devices=devices)
        self.store.log_push(type_=payload["type"], tag=tag, sent=sent, failed=failed_n, now=now)
        refs = [n.ref for n in due]
        if sent:
            self.store.set_push(refs, pushed_at=now, push_ok=1, next_try=None)
            out["pushed"] = len(due)
            return out
        tries = max(n.push_tries for n in due) + 1
        if retry and tries <= len(RETRY_S):
            wait = retry_after if retry_after is not None else RETRY_S[tries - 1]
            self.store.set_push(refs, push_tries=tries, next_try=now + wait)
            return out
        self.store.set_push(refs, pushed_at=now, push_ok=0, push_tries=tries, next_try=None)
        out["kakao"] += self._to_kakao([self.store.get(*r) or n for r, n in zip(refs, due, strict=True)], now)
        return out

    async def push_digest(self, did: str, title: str, body: str) -> int:
        """아침·저녁 정리 한 통 (급하지 않음, 묶음 셈에는 넣지만 막지는 않음). 보낸 기기 수."""
        devices = self.devices()
        if not devices:
            return 0
        now = self.clock()
        payload = {"v": 1, "type": "digest", "title": title, "body": body[:120], "tag": "digest",
                   "url": f"/dashboard/go/{did}", "urgent": False,
                   "badge": self.badge() if self.badge is not None else 0, "ts": int(now)}
        sent, failed, _retry, _ra = await self._deliver(payload, urgent=False, ttl=DIGEST_TTL, tag="digest", devices=devices)
        self.store.log_push(type_="digest", tag="digest", sent=sent, failed=failed, now=now)
        return sent

    async def push_test(self, devices: list[dict[str, Any]] | None = None) -> dict[str, int]:
        """연결 → 알림 → '테스트 알림'. 기기가 없으면 '테스트 카톡'."""
        devices = self.devices() if devices is None else devices
        now = self.clock()
        if not devices:
            ok = self.kakao.send("test", key="test", text="🔔 테스트 카톡 — 대시보드 알림이 꺼져 있어 카톡으로 보냈어요",
                                 link=f"{self.public_url}/dashboard/go/todo")
            return {"sent": 0, "failed": 0, "kakao": int(ok)}
        payload = {"v": 1, "type": "test", "title": "🔔 테스트 알림", "body": "이 알림이 보이면 휴대폰 알림이 잘 돼요.",
                   "tag": "test", "url": "/dashboard/#/connect", "urgent": False, "badge": 0, "ts": int(now)}
        sent, failed, _r, _ra = await self._deliver(payload, urgent=False, ttl=600, tag="test", devices=devices)
        self.store.log_push(type_="test", tag="test", sent=sent, failed=failed, now=now)
        return {"sent": sent, "failed": failed, "kakao": 0}
