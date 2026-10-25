"""대시보드 앱 화면 값 (GET /dashboard/api/state · 첫 화면 boot JSON · GET /dashboard/api/item/<iid>) — spec §5.1 의 State·Item·Action.

'할 일' 은 한 곳에 (spec §3.8, 한 가지 일은 한 줄 — D9):
  1) 봇 DB 지금 상태 (링크 기다리는 딜 · 더 좋은 딜로 바뀐 딜(늦게 넣어도 받는 동안) · 확인 기다리는 딜 · 채널 확인 · 사진 기다리는 딜)
  2) owner_notices 의 열린 알림 (두 프로그램)
  3) Blog-Auto 기록 파일 todo 중 알림 줄이 없는 것 (서로이웃 신청·7일째 새 글 없음 같은 셈한 일, 예전 길로 열린 일)
  + 글(post-B-N)은 기록 파일 posts[] 의 needs 가 있으면 (제목·필요한 일·사진 자리·질문·직접 고친 글은 늘 posts[] 에서)
줄마다 눌러야 할 것 하나(primary)와 '더보기'(more). 글자는 한국어만 — 텔레그램 명령·주소·영어는 넣지 않는다.
비밀값(토큰·푸시 주소·CSRF 키·VAPID 키·.env 값·대시보드 아이디/비밀번호)은 넣지 않는다.
긴 칸(정보 글 본문·질문 전문·답글 초안·/hot·/find·info 결과)은 state 에 넣지 않고 /api/item/<iid> 로.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from dealbot.dashboard import earn, rank, schedview, thpost, xview
from dealbot.dashboard.actions import (
    BLOG_NAMES,
    MAY_PUBLISH,
    dismissible,
    hidden_rows,
    is_hidden,
    needs_confirm,
    publish_blog,
    x_handles,
    x_stats_rows,
)
from dealbot.dashboard.remote_login import TARGETS as REMOTE_TARGETS
from dealbot.dashboard.searchreg import SearchReg
from dealbot.dashboard.today import count as today_count
from dealbot.dashboard.todo import ago, left, link_target
from dealbot.notify import iid as iidmod
from dealbot.utils.text import clean_name, truncate

log = logging.getLogger(__name__)

STATE_V = 1
BLOG_LABELS = {"finance": "Deren의 아카이브", "issue": "Deren's Pick", "hotdeal": "오늘의 핫딜"}
CHIPS = {"finance": "아카이브", "issue": "Pick", "hotdeal": "핫딜"}
VISITS_IID = "visits"  # 할 일 '오늘 들를 이웃 글' 한 줄
SEARCH_IID = "search"  # 할 일 '구글 검색 등록 N개' 한 줄 (주인 10/18 22:20)
SEV_ORDER = {"stop": 0, "hot": 1, "todo": 2, "info": 3}
UNSURE_DAYS = 7
OPS_MARK = {"ok": "✅", "fixed": "🔧", "blocked": "⛔", "owner": "🙋"}  # 직전 AI 점검: 새 오류 없음 · 고침 · 막힘 · 주인 할 일
LINKS_MAX = 3  # 사진 부탁 줄의 '사진 받을 곳' (카드 버튼은 첫 링크, 시트에 모두)
NAME_MAX = 40
# 알림 종류 → 글의 할 일 (기록 파일 posts[] 가 아직 없을 때)
KIND_NEED = {"draft_ready": "publish", "ask": "answers", "pick_photos": "photos", "hot": "photos", "upload_fail": "retry",
             "owner_edit": "owner_edit", "share_confirm": "share", "channel_ask": "channel", "channel_review": "channel"}
NEED_ORDER = ("owner_edit", "answers", "photos", "retry", "publish", "share", "channel")  # 하나만 고를 때 앞의 것
NEED_STATUS = {"owner_edit": "직접 고친 글 · 건드리지 않음", "answers": "질문에 답해 주세요", "photos": "사진 필요",
               "retry": "업로드 실패", "publish": "발행 대기", "share": "직접 발행 · 채널·카톡 보낼까요",
               "channel": "채널에 아직 안 올림"}


def _app_ver() -> str:
    from dealbot.dashboard.shell import APP_JS, asset_url

    return asset_url(APP_JS).rsplit("v=", 1)[-1]


def act(action: str, label: str, args: dict[str, Any] | None = None, *, input: str = "none", danger: bool | None = None,
        url: str | None = None, goto: str | None = None, **extra: Any) -> dict[str, Any]:
    """Action (spec §5.1): input = url|text|answers|photos|file|secret|choice|none|open|goto."""
    args = dict(args or {})
    out: dict[str, Any] = {"action": action, "label": label, "args": args, "input": input,
                           "confirm": needs_confirm(action, args) if action not in ("open", "goto") else False,
                           "danger": bool(danger) if danger is not None else action in ("blog.drop", "deal.skip", "blog.oedit_redo"),
                           "url": url, "goto": goto}
    out.update({k: v for k, v in extra.items() if v is not None})
    return out


def open_link(label: str, url: str) -> dict[str, Any]:
    return act("open", label, input="open", url=url)


def goto(label: str, route: str) -> dict[str, Any]:
    return act("goto", label, input="goto", goto=route)


def _ts(dt: datetime | None) -> float | None:
    return dt.timestamp() if dt is not None else None


def _iso_ts(v: Any, tz: str) -> float | None:
    if not isinstance(v, str) or not v:
        return None
    try:
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=ZoneInfo(tz))).timestamp()


def _name(text: str) -> str:
    return truncate(" ".join(clean_name(text or "").split()), NAME_MAX)


class StateBuilder:
    """bot = app.DealBot. 바깥 요청 없음 — 봇 DB·로컬 파일만."""

    def __init__(self, bot: Any) -> None:
        self.bot = bot
        # 간격 두고 발행 (주인 10/18): ok = Blog-Auto 기록 파일에 schedule 칸이 있고 schedule 일을 앎 (아니면 예전 '발행' 그대로),
        # spaced = 간격 두고만 발행하는 블로그 (Pick — '지금 발행' 없음). items()·blogs() 가 기록 파일을 읽을 때 정함
        self._sctx: dict[str, Any] = {"ok": False, "spaced": {}}

    def _sched_ctx(self, v2: dict[str, Any]) -> None:
        sch = v2.get("schedule")
        supported = self._supported(self._ba_status())
        ok = isinstance(sch, dict) and (supported is None or {"schedule", "unschedule"} <= supported)
        self._sctx = {"ok": ok, "spaced": {b: bool((x or {}).get("spaced_only")) for b, x in ((sch or {}).get("blogs") or {}).items()}
                      if ok else {}, "sup": supported}

    @staticmethod
    def queued(post: dict[str, Any] | None) -> bool:
        """간격 두고 발행으로 예약된 글 (예약·올리는 중) — 주인 할 일이 아니라 할 일 목록에 넣지 않는다 (멈춘 예약은 넣음)."""
        return ((post or {}).get("schedule") or {}).get("status") in ("scheduled", "publishing")

    def _when(self, t: float) -> str:
        """'오늘 14:20' · '내일 08:47' · '10/21 08:47'."""
        tz = ZoneInfo(self.tz)
        d, n = datetime.fromtimestamp(t, tz), datetime.now(tz)
        if d.date() == n.date():
            return f"오늘 {d:%H:%M}"
        if d.date() == (n + timedelta(days=1)).date():
            return f"내일 {d:%H:%M}"
        return f"{d.month}/{d.day} {d:%H:%M}"

    # ---------------------------------------------------------- 공통
    @property
    def tz(self) -> str:
        return self.bot.settings.app.timezone

    def _now(self) -> datetime:
        return datetime.now(UTC)

    def _export(self) -> Any:
        dash = getattr(self.bot, "dashboard", None)
        reader = getattr(getattr(dash, "ctx", None), "blogauto", None)
        try:
            return reader.read() if reader is not None else None
        except Exception as e:  # noqa: BLE001
            log.warning("dashboard state: Blog-Auto 기록을 못 읽음: %s", e)
            return None

    def _ba_status(self) -> Any:
        return self.bot.actions_out.status()

    def _supported(self, st: Any) -> set[str] | None:
        """Blog-Auto 가 아는 일 이름들 (모르면 None — 그때는 다 보여 주고 Blog-Auto 가 unsupported 로 답함)."""
        return set(st.actions) if st is not None and st.actions else None

    def _filter(self, actions: list[dict[str, Any]], supported: set[str] | None) -> list[dict[str, Any]]:
        if supported is None:
            return actions
        return [a for a in actions if not a["action"].startswith("blog.") or a["action"][5:] in supported]

    @staticmethod
    def _modes(v2: dict[str, Any]) -> dict[str, str]:
        blogs = (v2.get("status") or {}).get("blogs") or {}
        return {b: str((blogs.get(b) or {}).get("mode") or "") for b in ("finance", "issue", "hotdeal")}

    def _mark_publish(self, a: dict[str, Any], modes: dict[str, str]) -> None:
        """자동 발행 모드 블로그의 MAY_PUBLISH 일은 확인 창이 있다고 표시 (서버 ActionDesk 가 같은 기준으로 409)."""
        if a.get("action") in MAY_PUBLISH:
            # (blog.fix 처럼 원래 ★ 인 일은 임시저장 모드여도 ★ 그대로). 간격 두고만 발행하는 블로그(Pick)는 바로 공개되지 않음
            blog = publish_blog(a["action"], a.get("args") or {}) or ""
            spaced = bool(self._sctx["spaced"].get(blog))
            a["confirm"] = bool(a.get("confirm")) or (not spaced and modes.get(blog) != "draft")

    def _hhmm(self, dt: datetime) -> str:
        return dt.astimezone(ZoneInfo(self.tz)).strftime("%H:%M")

    # ---------------------------------------------------------- 딜 (봇 DB)
    def _shop(self, key: str) -> Any:
        return self.bot.registry.get(key)

    def _deal_items(self, now: datetime, *, long: bool = False) -> dict[str, dict[str, Any]]:
        bot = self.bot
        db = bot.db
        ttl = timedelta(hours=bot.settings.publish.manual_link_ttl_hours)
        items: dict[str, dict[str, Any]] = {}

        def base(it: Any, kind: str, status: str, primary: dict[str, Any], more: list[dict[str, Any]],
                 detail: dict[str, Any], *, expires: datetime | None = None, since: datetime | None = None) -> None:
            iid = iidmod.deal_iid(it.id)
            p = it.deal.product
            v = it.deal.verdict
            hot = rank.deal_hot(it.score, price=p.price, low_price=v.low_price, discount=p.effective_discount_rate(),
                                recommend=p.recommend_count)
            items[iid] = {"iid": iid, "src": "deal", "blog": "", "kind": kind, "sev": "todo", "urgent": True,
                          "title": f"#{it.id} {_name(p.name)}", "status": status, "since": _ts(since or it.created_at),
                          "expires_at": _ts(expires), "primary": primary, "more": more, "detail": detail, "_hot": hot}

        for it in db.awaiting_items(max(50, bot.settings.publish.max_awaiting_links)):
            p = it.deal.product
            shop = self._shop(p.shop)
            shop_name = shop.name if shop else p.shop
            price = f"{p.price:,}원" if p.has_price else "가격 없음"
            exp = it.created_at + ttl
            from dealbot.monitoring.admin import wants_owner_photo

            photo = wants_owner_photo(p.shop, p)
            prod = link_target(p.url)
            toss = self._toss_open(p, prod)  # 토스: 앱에서만 쉐어링크를 만들 수 있어 '토스에서 열기' + 붙여넣기 때 토스 링크만 받음 (주인 10/23)
            primary = act("deal.link", "링크 넣기", {"qid": it.id}, input="url", url=prod, placeholder="만든 링크 붙여넣기",
                          photo_optional=photo or None, accept="image/*" if photo else None, **toss)
            more = [act("deal.skip", "안 올리기", {"qid": it.id})]
            if (post := link_target(p.extra.get("post_url"))) is not None:
                more.append(open_link("원문 글 보기", post))
            detail: dict[str, Any] = {"how": (shop.manual_hint if shop and shop.manual_hint else "그 몰 앱에서 내 링크를 만들어 넣어 주세요"),
                                      "open": [{"label": toss.get("ol") or "상품 열기", "url": prod}] if prod else []}
            if p.shop == "naver":
                from dealbot.monitoring.admin import naver_queries

                detail["search"], short = naver_queries(p.name, p.shop, p.category)
                if short:  # 긴 이름이 0건이면 이걸로 (주인 10/18)
                    detail["short"] = short
                # 쇼핑 커넥트에서 못 찾음 → 한 번에 빠지게 (주인 10/18). 판매처를 기억해 다음 딜부터 묻지 않음
                more.insert(0, act("deal.nolink", "쇼핑 커넥트에 없음", {"qid": it.id}, input="choice",
                                   note="이 판매처는 기억해 두고, 다음 딜부터 묻지 않고 링크 없이 올려요"
                                   if bot.settings.autolink.naver.no_link_autopost else None,
                                   choices=[{"value": "post", "label": "링크 없이 올리기", "primary": True},
                                            {"value": "skip", "label": "안 올리기"}]))
                detail["how"] = "쇼핑 커넥트 '상품 찾기'에서 아래 검색어로 찾아 링크를 만들어 주세요 · 사진은 봇이 상품 페이지에서 넣어요"
            if (hold := p.extra.get("price_hold_note")) and isinstance(hold, str):
                detail["note"] = truncate(hold, 120)
            base(it, "link_request", f"[{shop_name}] {price} · {left(exp, now)}", primary, more, detail, expires=exp)
        for it in bot.replaced_link_view():
            p = it.deal.product
            exp = it.created_at + ttl
            new_id = (it.last_error or "").split("#", 1)[-1].split(" ", 1)[0] if "#" in (it.last_error or "") else ""
            primary = act("deal.link", "링크 넣기", {"qid": it.id}, input="url", url=link_target(p.url), placeholder="만든 링크 붙여넣기")
            base(it, "link_replaced", f"더 좋은 딜로 바뀜 · {self._hhmm(exp)}까지 넣으면 올려요", primary,
                 [act("deal.skip", "안 올리기", {"qid": it.id})], {"replaced_by": f"#{new_id}" if new_id.isdigit() else ""},
                 expires=exp)
            items[iidmod.deal_iid(it.id)]["urgent"] = False
        for it in db.awaiting_items(30, statuses=("awaiting_approval",)):
            p = it.deal.product
            exp = it.created_at + ttl
            if p.deal_kind == "info" or "info_draft" in p.extra:
                draft = p.extra.get("info_draft") or {}
                detail = {"photo_count": len(draft.get("images") or []) if isinstance(draft, dict) else 0,
                          "preview": (str(draft.get("text") or "")[:4000] if long else None) if isinstance(draft, dict) else None}
                base(it, "review_info", "확인 후 올리기", act("deal.ok", "올리기", {"qid": it.id}),
                     [act("deal.ok_text", "고쳐서 올리기", {"qid": it.id}, input="text", placeholder="올릴 본문"),
                      act("deal.skip", "안 올리기", {"qid": it.id})], detail, expires=exp)
                items[iidmod.deal_iid(it.id)]["title"] = f"#{it.id} 정보 글"
            else:
                opens = [{"label": "상품 열기", "url": u} for u in (link_target(p.url),) if u]
                if (post := link_target(p.extra.get("post_url"))) is not None:
                    opens.append({"label": "원문 글 보기", "url": post})
                base(it, "review_auth", "정품 확인 필요", act("deal.ok", "올리기", {"qid": it.id}),
                     [act("deal.skip", "안 올리기", {"qid": it.id})],
                     {"reason": truncate(str(p.extra.get("auth_reason") or it.last_error or ""), 120), "open": opens}, expires=exp)
        for it, at in db.maybe_posted_items(now - timedelta(days=UNSURE_DAYS), 20):
            more = [act("deal.repost", "없어요 · 다시 올리기", {"qid": it.id})] if it.deal.affiliate_url else \
                [act("deal.link", "없어요 · 링크 넣기", {"qid": it.id}, input="url", placeholder="만든 링크 붙여넣기")]
            base(it, "publish_unsure", "채널에 올라갔는지 확인",
                 act("deal.posted", "채널에 있어요", {"qid": it.id}, input="url", optional=True, placeholder="채널 글 주소 (없어도 돼요)"),
                 more, {"open": [{"label": "채널 열기", "url": u} for u in (self._channel_url(),) if u]}, since=at)
        for qid, until in bot.late_photo_view(now):
            it = db.get_queue_item(qid)
            if it is None or it.status != "published":
                continue
            from dealbot.monitoring.admin import product_links

            links = product_links(it.deal.product)  # 상품 페이지 — 사진 올리기 옆 '상품 열기 ↗' (주인 10/18)
            base(it, "photo_need", f"사진 필요 · {self._hhmm(until)}까지",
                 act("deal.late_photo", "사진 올리기", {"qid": qid}, input="photos", accept="image/*", multiple=False), [],
                 {"how": "상품이 잘 보이는 사진 한 장 (블로그·인스타에 써요) · 화면 캡처면 상품 사진 부분을 알아서 잘라요"},
                 expires=until)  # 상품 페이지는 사진 올리기 옆 버튼 (줄·시트 모두 — 같은 링크를 두 번 그리지 않음)
            items[iidmod.deal_iid(qid)]["links"] = links
        return items

    TOSS_PASTE = r"https?://(?:toss\.im/_m/|toss\.shopping/)[^\s]+"  # app.js paste(): 복사한 글에서 이 모양의 주소만 뽑아 칸에 넣음

    def _toss_open(self, p: Any, prod: str | None) -> dict[str, Any]:
        """토스 딜의 링크 넣기 칸에 덧붙일 것: pm = 붙여넣기가 받는 주소 모양(토스 쉐어링크만), ol = 상품 열기 단추 글자 '토스에서 열기'
        (글에서 찾은 toss.im/_m/·toss.shopping/t/ 주소는 모바일에서 토스 앱으로 열리는 앱 링크라 따로 딥링크를 만들지 않음). 토스 딜이 아니면 빈 값."""
        shop = self._shop(p.shop)
        if p.shop != "toss" or shop is None:
            return {}
        return {"pm": self.TOSS_PASTE, **({"ol": "토스에서 열기"} if prod and shop.matches_url(prod) else {})}

    def _channel_url(self) -> str | None:
        base = getattr(self.bot.notifier, "channel_post_base", None)
        return link_target(base) if base else None

    def _move_act(self, a: dict[str, Any], sch: dict[str, Any]) -> dict[str, Any] | None:
        """예약 시간 바꾸기 (주인 10/19 — "데런 픽 글 예약 시간 조정할 수 있는 기능도 넣어줘"): 시각 칸(지금 예약 시각) + 오늘/내일."""
        sup = self._sctx.get("sup")
        at = _iso_ts(sch.get("scheduled_at"), self.tz)
        if (sup is not None and "schedule_move" not in sup) or at is None:
            return None
        tz = ZoneInfo(self.tz)
        d = datetime.fromtimestamp(at, tz)
        return act("blog.schedule_move", "시간 바꾸기", a, input="when", value=f"{d:%H:%M}",
                   day="tomorrow" if d.date() > datetime.now(tz).date() else "today")

    def _notes_act(self, blog: str, pid: int | None = None) -> dict[str, Any] | None:
        """내 후기 쓰기 (주인 10/20 — 텔레그램을 안 써서): 핫딜 글에 메모 + 사진(선택) → Blog-Auto 가 가이드·제품 소개 글을
        그 메모로만 1인칭 후기로 다시 씀. pid 없으면 '후기 쓸 제품 고르기' (제품 이름 칸 + 메모 → 새 제품 소개 글)."""
        sup = self._sctx.get("sup")
        if blog != "hotdeal" or (sup is not None and "post_notes" not in sup):
            return None
        a = {"blog": blog, "pid": pid} if pid else {"blog": blog}
        return act("blog.post_notes", "내 후기 쓰기" if pid else "후기 쓸 제품 고르기", a, input="text",
                   placeholder="직접 써 본 메모 — 여기 적은 것만 1인칭 후기로 써요", photos_optional=True, accept="image/*",
                   multiple=True, product=None if pid else True)

    def _similar_act(self, blog: str, pid: int, post: dict[str, Any] | None) -> dict[str, Any] | None:
        """비슷한 아이템 링크 넣기 (주인 10/20 — 에이블리 크리에이터 링크는 앱에서만 만들어져 주인이 붙여 넣음): Pick 패션 글
        (Blog-Auto 가 similar 칸을 준 글)에만. 글 칸에 한 줄에 하나 '이름 | 링크' → Blog-Auto similar_links (확인 창 없음)."""
        sup = self._sctx.get("sup")
        if blog != "issue" or (post or {}).get("similar") is None or (sup is not None and "similar_links" not in sup):
            return None
        return act("blog.similar_links", "비슷한 아이템 링크 넣기", {"blog": blog, "pid": pid}, input="text",
                   placeholder="한 줄에 하나씩 · 이름 | 링크 (이름은 선택)\n예: 베이지 트렌치코트 | https://m.a-bly.com/goods/…")

    # ---------------------------------------------------------- 글 (기록 파일)
    def _post_actions(self, blog: str, pid: int, needs: list[str], post: dict[str, Any] | None) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
        a = {"blog": blog, "pid": pid}
        acts: dict[str, dict[str, Any]] = {
            "owner_edit": act("blog.oedit_keep", "그대로 두기", {"pid": pid, "blog": blog}),
            "answers": act("blog.answer", "답하기", {"pid": pid, "blog": blog}, input="answers"),
            "photos": act("blog.photos", "사진 올리기", a, input="photos", accept="image/*", multiple=True,
                          options=["replace", "in_order", "crop", "credit"]),
            "retry": act("blog.retry", "다시 올리기", a),
            "publish": act("blog.publish", "발행", a),
            "share": act("blog.share", "보내기", a),
            "channel": act("blog.channel", "채널에 올리기", a),
        }
        sch = (post or {}).get("schedule") or {}
        sched_ok, spaced = self._sctx["ok"], bool(self._sctx["spaced"].get(blog))
        now_pub = act("blog.publish", "지금 발행", a)  # 확인 창(★) 그대로
        if sched_ok and sch.get("status") in ("scheduled", "publishing"):
            # 예약된 글: 차분한 상태 한 줄만 (누를 것 없음) · 더보기 → 예약 취소 / 지금 발행
            more = [act("blog.unschedule", "예약 취소", a)] if sch["status"] == "scheduled" else []
            if sch["status"] == "scheduled" and (mv := self._move_act(a, sch)):
                more.insert(0, mv)
            if not spaced and sch["status"] == "scheduled":
                more.append(now_pub)
            if sch["status"] == "scheduled" and (na := self._notes_act(blog, pid)):
                more.append(na)
            if sch["status"] == "scheduled" and (sa := self._similar_act(blog, pid, post)):
                more.append(sa)
            if (url := (post or {}).get("naver_url")):
                more.append(open_link("네이버에서 보기", url))
            return None, more
        if sched_ok:  # 간격 두고 발행 (주인 10/18) — 큰 버튼은 예약, '지금 발행' 은 더보기로 (Pick 은 없음)
            acts["publish"] = act("blog.schedule", "다시 예약" if sch.get("status") == "blocked" else "간격 두고 발행",
                                  {"blog": blog, "pids": [pid]})
        order = [n for n in NEED_ORDER if n in needs]
        if sched_ok and sch.get("status") == "blocked" and "publish" not in order:
            order.append("publish")  # 멈춘 예약: 고칠 일(사진·답 …)이 먼저, 없으면 '다시 예약'
        primary = acts[order[0]] if order else goto("자세히", f"#/i/{iidmod.post_iid(blog, pid)}")
        more = [acts[n] for n in order[1:]]
        if sched_ok and sch.get("status") == "blocked":
            more.append(act("blog.unschedule", "예약 취소", a))
        if sched_ok and "publish" in order and not spaced:
            more.insert(0, now_pub)
        if "owner_edit" in needs:
            more.append(act("blog.oedit_redo", "봇 글로 덮어쓰기", {"pid": pid, "blog": blog}, danger=True))
        if "answers" in needs:
            more.append(act("blog.noanswer", "답 없이 진행", {"pid": pid, "blog": blog}))
        if "share" in needs:
            more.append(act("blog.noshare", "보내지 않기", a))
        if "channel" in needs:
            more.append(act("blog.channel", "직접 발행한 주소 넣기", a, input="url", placeholder="내 블로그 글 주소"))
        if blog in ("finance", "issue"):
            more.append(act("blog.fix", "말로 고치기", a, input="text", placeholder="어떻게 고칠지 적어 주세요"))
        if (post or {}).get("status") != "published" and (na := self._notes_act(blog, pid)):
            more.append(na)
        if (post or {}).get("status") not in ("published", "drafting") and (sa := self._similar_act(blog, pid, post)):
            more.append(sa)
        if blog == "issue":
            more.append(act("blog.rewrite", "다시 쓰기", {"blog": "issue", "pids": [pid]}))
            more.append(act("blog.restyle", "스타일 다시", {"blog": "issue", "pids": [pid]}))
        if not (post or {}).get("status") == "published":
            more.append(act("blog.drop", "버리기", {"blog": blog, "pids": [pid]}))
        if (url := (post or {}).get("naver_url")):
            more.append(open_link("네이버에서 보기", url))
        return primary, more

    def _post_item(self, blog: str, post: dict[str, Any] | None, *, pid: int, title: str = "", needs: list[str] | None = None,
                   long: bool = False) -> dict[str, Any]:
        needs = list((post or {}).get("needs") or needs or [])
        primary, more = self._post_actions(blog, pid, needs, post)
        first = next((n for n in NEED_ORDER if n in needs), "")
        status = NEED_STATUS.get(first, "기다리는 글")
        sch = ((post or {}).get("schedule") or {}) if self._sctx["ok"] else {}
        kind = first or "waiting"
        if first == "photos" and post and post.get("photo_request"):
            n = len(post["photo_request"].get("must") or [])
            status = f"사진 {n}장 필요" if n else status
        if first == "answers" and post and post.get("questions"):
            status = f"질문 {len(post['questions'])}개"
        if first == "retry" and post and post.get("fail"):  # 실패 이유 한 줄 (주인 10/21 — 서버 쪽에서 글자로, app.js 는 그대로)
            status = self._fail_text(status, post["fail"])
        if first == "publish" and post and post.get("popular"):
            status = "발행 대기 · 인기 예상"
        # 끝나는 시간 (질문 마감·사진 부탁 마감) → 줄에 '· 18:00까지' + 정렬·경고색 (딜 줄과 같은 기준으로 앞에)
        pr = (post or {}).get("photo_request") if isinstance((post or {}).get("photo_request"), dict) else {}
        exp = (_iso_ts((post or {}).get("deadline"), self.tz) or _iso_ts(pr.get("deadline"), self.tz)) if needs else None
        if exp is not None and first in ("answers", "photos", "publish"):
            when = datetime.fromtimestamp(exp, ZoneInfo(self.tz))
            same_day = when.date() == datetime.now(ZoneInfo(self.tz)).date()
            status = f"{status} · {when:%H:%M}까지" if same_day else f"{status} · {when.month}/{when.day} {when:%H:%M}까지"
        if sch.get("status") in ("scheduled", "publishing"):  # 예약됨 — 주인 할 일이 아님 (할 일 목록에는 넣지 않는다)
            at = _iso_ts(sch.get("scheduled_at"), self.tz)
            status = "지금 발행하는 중" if sch["status"] == "publishing" or at is None else f"{self._when(at)} 발행 예정"
            kind, exp = "scheduled", None
        elif sch.get("status") == "blocked":  # 차례에 올리지 못함 — 이유와 고칠 일 (멈춘 주인 일과 같은 순서)
            status = f"예약 발행 멈춤 · {sch.get('reason') or '확인이 필요해요'}"
            kind, exp = "sched_blocked", None
        # 질문은 자르지 않는다 — 답하려면 전체 문장이 보여야 함 (주인 10/18: '질문이 잘려')
        pics = self._pics_view(blog, pid, post, needs)
        if pics and pics.get("put") and pics["put"]["args"]["pics"] and "photos" in needs and (primary or {}).get("action") == "blog.photos":
            more.insert(0, primary)  # 추천 사진이 준비됐으면 큰 버튼은 '이대로 넣기' (주인 10/18 — 한 번에), 직접 올리기는 더보기로
            primary = pics["put"]
        if pics and kind == "photos" and pics["state"] in ("working", "ready"):
            status += " · 사진 찾는 중…" if pics["state"] == "working" else f" · 추천 사진 {len(pics['items'])}장"
        questions = list((post or {}).get("questions") or [])
        slots = [dict(s, thumb=f"/dashboard/thumb/{s['thumb']}" if s.get("thumb") else None,
                      **{k: f"/dashboard/thumb/{s[k]}" for k in ("full", "final") if s.get(k)}) for s in (post or {}).get("slots") or []]
        iid = iidmod.post_iid(blog, pid)
        return {"iid": iid, "src": "blog", "blog": blog, "kind": kind, "sev": "todo", "urgent": bool(needs) or kind == "sched_blocked",
                "title": (post or {}).get("title") or title or f"{CHIPS[blog]} #{pid}", "chip": CHIPS[blog], "status": status,
                "since": _iso_ts((post or {}).get("saved_at"), self.tz), "expires_at": exp, "primary": primary, "more": more,
                "detail": {"needs": needs, "photo_request": (post or {}).get("photo_request"), "slots": slots,
                           "questions": questions, "deadline": (post or {}).get("deadline"),
                           "owner_edited": bool((post or {}).get("owner_edited")), "pics": pics, "ig": self._ig_view(blog, pid, post),
                           "open": [{"label": "네이버에서 보기", "url": post["naver_url"]}] if post and post.get("naver_url") else []},
                "links": list((pr or {}).get("links") or [])[:LINKS_MAX] if "photos" in needs else [],
                "_hot": rank.post_hot(post), "_hot_rank": (post or {}).get("hot_rank")}

    def _fail_text(self, status: str, fail: dict[str, Any]) -> str:
        """'업로드 실패 · 이유 — 20분 뒤 한 번 자동으로 다시 해요' (memory·busy 만 자동으로 한 번 다시)."""
        text = f"{status} · {fail['why']}"
        if fail["auto_retry"] == "done":
            return text + " — 자동으로 한 번 다시 해 봤어요"
        if fail["auto_retry"] != "pending":
            return text
        at = _iso_ts(fail.get("at"), self.tz)
        left = 20 - int((time.time() - at) // 60) if at else 20
        return text + (f" — {left}분 뒤 한 번 자동으로 다시 해요" if left > 0 else " — 곧 한 번 자동으로 다시 해요")

    @staticmethod
    def _ig_view(blog: str, pid: int, post: dict[str, Any] | None) -> list[dict[str, Any]]:
        """사진 칸 '넣으면 좋을 인스타 게시물' (Blog-Auto ig_suggest): 줄마다 {text, open(게시물 또는 계정 주소), src(기사), put}.
        put = 사진 올리기의 인스타 링크 칸과 같은 blog.photos links (빈 자리에) — 게시물 주소가 있고 아직 안 넣은 것만."""
        if blog != "issue" or not post or post.get("status") == "published":
            return []
        out = []
        for x in post.get("ig_suggest") or []:
            url = x["url"] or (f"https://www.instagram.com/{x['handle']}/" if x["handle"] else None)
            put = act("blog.photos", "넣기", {"blog": "issue", "pid": pid, "links": [{"slot": 0, "url": x["url"]}]}) \
                if x["url"] and not x["inserted"] else None
            out.append({"text": x["what"] + (f" · {x['when']}" if x["when"] else "") + (" · 넣음" if x["inserted"] else ""),
                        "open": url, "src": x["source"], "put": put})
        return out

    # ---------------------------------------------------------- 링크에서 사진 고르기 (주인 10/18 — Blog-Auto pickphotos)
    def _pics_ok(self) -> bool:
        sup = self._sctx.get("sup")
        return sup is None or "pics_fetch" in sup

    @staticmethod
    def _pic_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [dict(x, thumb=f"/dashboard/thumb/{x['thumb']}", full=f"/dashboard/pic/{x['full']}") for x in items]

    def _pics_view(self, blog: str, pid: int, post: dict[str, Any] | None, needs: list[str]) -> dict[str, Any] | None:
        """글의 사진 칸 '추천 사진 N장' — AI 가 고른 것(slot)은 미리 골라 둠 · '이대로 넣기' = 그 자리 순서대로 blog.photos pics.
        후보가 없고 사진도 필요 없는 글은 None."""
        p = (post or {}).get("pics")
        if p is None and "photos" not in needs:
            return None
        p = p or {"state": "none", "text": "", "items": []}
        a = {"blog": blog, "pid": pid}
        items = self._pic_items(p["items"])
        chosen = sorted((x for x in items if x["slot"] is not None), key=lambda x: (x["slot"] == 0, x["slot"]))
        ok = self._pics_ok()
        return {"state": p["state"], "text": p["text"], "items": items,
                "fetch": act("blog.pics_fetch", "링크에서 사진 찾기" if p["state"] == "none" else "다시 찾기", a) if ok else None,
                "clear": act("blog.pics_clear", "후보 지우기", a) if ok and items else None,
                "put": act("blog.photos", f"이대로 넣기 · {len(chosen)}장", dict(a, pics=[{"id": x["id"], "slot": x["slot"]} for x in chosen]),
                           input="pics") if p["state"] == "ready" and items else None}

    def pics_tool(self, export: Any) -> dict[str, Any] | None:
        """블로그 탭 '링크에서 사진 뽑기': 링크(+원하는 사진)를 붙여 넣으면 Blog-Auto 가 사진을 골라 둠 (pics_jobs, 최근 것 먼저).
        Blog-Auto 가 이 일을 모르면 None (화면이 숨김)."""
        v2 = getattr(export, "v2", None) or {}
        sup = self._supported(self._ba_status())
        jobs = v2.get("pics_jobs")
        if (sup is not None and "pics_fetch" not in sup) or (sup is None and jobs is None):
            return None
        from urllib.parse import urlsplit

        out = []
        for j in jobs or []:
            try:
                host = (urlsplit(j["url"]).hostname or "").removeprefix("www.")
            except ValueError:
                host = ""
            out.append({"id": j["id"], "url": j["url"], "host": host, "want": j["want"], "state": j["state"], "text": j["text"],
                        "at": _iso_ts(j.get("at"), self.tz), "items": self._pic_items(j["items"]),
                        "clear": act("blog.pics_clear", "지우기", {"job": j["id"]})})
        return {"jobs": out, "fetch": act("blog.pics_fetch", "사진 뽑기", {}, input="url", placeholder="기사·쇼핑몰 링크 붙여넣기")}

    def photo_trial(self, export: Any) -> dict[str, Any] | None:
        """블로그 탭 '사진 테스트' (주인 10/21 — Blog-Auto photo_trial): 시험 글마다 자리 사진 크게(올라가는 판)·자리 설명·출처·검수,
        ✅ 괜찮아 / ❌ 별로(이유 칩·메모) · 글 전체(배치·분포·개수·메모) · 테스트 끝 → blog.photo_feedback. 사진을 누르면 평소 편집기
        (it 모양 그대로 — 편집도 배운 것이 된다). 시험 글이 없거나 Blog-Auto 가 이 일을 모르면 None (화면이 숨김)."""
        v2 = getattr(export, "v2", None) or {}
        pt = v2.get("photo_trial")
        sup = self._supported(self._ba_status())
        if not pt or (sup is not None and "photo_feedback" not in sup):
            return None
        posts = []
        for p in pt["posts"]:
            a = {"blog": "issue", "pid": p["pid"]}
            slots = [dict(s, **{k: f"/dashboard/thumb/{s[k]}" for k in ("final", "full") if s.get(k)}) for s in p["slots"]]
            posts.append({"pid": p["pid"], "iid": iidmod.post_iid("issue", p["pid"]), "title": p["title"], "at": p["at"],
                          "feedback_done": p["feedback_done"], "slots": slots, "read": p.get("read"),
                          # 편집기(edit.js)가 쓰는 자리 모양 {slot, label, thumb, full, final}
                          "edit": [{"slot": s["slot"], "label": s["desc"] or s.get("where") or f"자리 {s['slot']}", "thumb": s.get("final") or s.get("full"),
                                    **{k: s[k] for k in ("full", "final") if s.get(k)}} for s in slots],
                          "fb": act("blog.photo_feedback", "사진 피드백", a),
                          "done": act("blog.photo_feedback", "테스트 끝", dict(a, verdict="done"))})
        return {"posts": posts, "rules": pt["rules"]}

    def product_trial(self, export: Any) -> dict[str, Any] | None:
        """블로그 › Pick '제품 테스트' 화면 (주인 10/21 — Blog-Auto product_trial.py): 글마다 착장 아이템 → 후보 카드 (사진·이름·
        판매처·가격/날짜·열기) ✅ 괜찮아 / ❌ 별로(이유 칩 + 한 줄) · 글 전체 한 줄·테스트 끝 · 맨 위 '배운 기준'.
        누르기 = blog.product_feedback (확인 없음 — 글 본문은 바뀌지 않음). 테스트 중인 글이 없거나 Blog-Auto 가 모르면 None (숨김)."""
        pt = (getattr(export, "v2", None) or {}).get("product_trial")
        sup = self._supported(self._ba_status())
        if not pt or not pt["posts"] or (sup is not None and "product_feedback" not in sup):
            return None
        posts = []
        for p in pt["posts"]:
            base = {"blog": "issue", "pid": p["pid"]}
            posts.append({"pid": p["pid"], "title": p["title"], "pieces": p["pieces"], "read": p.get("read"),
                          "items": [dict(x, img=f"/dashboard/thumb/{x['img']}" if x.get("img") else None,
                                         act=act("blog.product_feedback", "판정", dict(base, item_id=x["id"]))) for x in p["items"]],
                          "act": act("blog.product_feedback", "테스트 끝", base)})
        return {"posts": posts, "learned": pt["learned"], "ok": pt["ok"], "goal": pt["goal"], "reasons": pt["reasons"]}

    # ---------------------------------------------------------- 알림 줄 (owner_notices · 기록 파일 todo)
    def _notice_item(self, iid: str, rows: list[Any], now: float) -> dict[str, Any] | None:
        first = sorted(rows, key=lambda n: (SEV_ORDER.get(n.sev, 2), n.opened_at))[0]
        key = first.key
        head = key.split(":", 1)[0]
        dismiss = act("todo.dismiss", "다 했어요", {"iid": iid, "key": key})
        can_dismiss = dismissible(iid, key, dealbot_row=first.src == "dealbot")
        more: list[dict[str, Any]] = []
        if iid.startswith("sys-"):
            primary = goto("자세히", f"#/i/{iid}")  # 무엇이 멈췄는지는 그 시트에 (연결 탭은 그다음)
            more.append(goto("연결 보기", "#/connect"))
            if first.sev == "info":
                more.append(dismiss)
        elif iid.startswith("conn-naver-"):
            blog = iid.rsplit("-", 1)[-1]
            primary = act("blog.login_check", "로그인 확인", {"blog": blog})
            more.append(act("login.remote", "원격 화면으로 다시 로그인", {"target": blog}))
        elif iid.startswith("conn-mall-"):  # 쇼핑몰 로그인·사람 확인 — 원격 화면에서 주인이 한 번 (봇은 확인을 뚫지 않음)
            mall = iid.removeprefix("conn-mall-")
            primary = act("login.remote", "원격 화면으로 확인", {"target": mall}) if mall in REMOTE_TARGETS else goto("연결 보기", "#/connect")
        elif iid in ("conn-kakao", "sns-threads", "sns-instagram"):
            primary = goto("다시 연결", "#/connect")
        elif iid == "sns-replies":
            primary = goto("답글 보기", "#/i/sns-replies")
            more.append(dismiss)
        elif iid == "sns-status":
            primary = goto("자세히", "#/connect")
            more.append(dismiss)
        elif iid.startswith("blog-"):
            primary = goto("글감 보기", f"#/blog/{iid.split('-')[1]}") if iid.endswith("-scan") else goto("블로그 보기", f"#/blog/{iid.split('-')[1]}")
            more.append(dismiss)
        else:
            primary = goto("자세히", f"#/i/{iid}")
            more.append(goto("연결 보기", "#/connect"))
        relog = {"autolink:login": "finance", "autolink_toss:login": "toss"}  # 쇼핑커넥트(아카이브 프로필)·토스 로그인 풀림
        for r in rows:
            if r.key in relog and not any(a["action"] == "login.remote" for a in more):
                more.insert(0, act("login.remote", "원격 화면으로 다시 로그인", {"target": relog[r.key]}))
        if not can_dismiss:
            more = [a for a in more if a["action"] != "todo.dismiss"]
        when = ago(datetime.fromtimestamp(first.opened_at, UTC), datetime.fromtimestamp(now, UTC))
        return {"iid": iid, "src": "dealbot" if first.src == "dealbot" else "blog", "blog": first.blog, "kind": first.kind,
                "sev": first.sev, "urgent": any(n.urgent for n in rows), "title": first.title or first.short,
                "status": notice_status(first.body, first.title) or when,
                "since": first.opened_at, "expires_at": None, "primary": primary, "more": more,
                "detail": {"lines": [n.body for n in rows if n.body][:6], "key": head}}

    # ---------------------------------------------------------- 할 일 모으기
    def items(self, *, long: bool = False, export: Any = None) -> list[dict[str, Any]]:
        now_dt = self._now()
        now = now_dt.timestamp()
        export = export if export is not None else self._export()
        v2 = getattr(export, "v2", None) or {}
        posts = {b: {p["pid"]: p for p in v2.get("posts", {}).get(b, [])} for b in ("finance", "issue", "hotdeal")}
        self._sched_ctx(v2)
        queued = {iidmod.post_iid(b, pid) for b, rows in posts.items() for pid, p in rows.items() if self._sctx["ok"] and self.queued(p)}
        out: dict[str, dict[str, Any]] = {}
        try:
            out.update(self._deal_items(now_dt, long=long))
        except Exception as e:  # noqa: BLE001 — 봇 DB 를 못 읽어도 나머지는 보인다
            log.warning("dashboard state: 딜 할 일을 못 읽음: %s", e)
        router = getattr(self.bot, "router", None)
        notices = router.store.open_rows(now) if router is not None else []
        by_iid: dict[str, list[Any]] = {}
        for n in notices:
            by_iid.setdefault(n.iid, []).append(n)
        for iid, rows in by_iid.items():
            if iid.startswith("deal-"):
                if iid in out:  # 딜은 봇 DB 줄이 이긴다 — 알림은 급함·시각만 더함
                    out[iid]["since"] = min(r.opened_at for r in rows)
                    out[iid]["urgent"] = out[iid]["urgent"] or any(r.urgent for r in rows)
                continue
            post = iidmod.post_of(iid)
            if iid in queued and not any(r.key.split(":", 1)[0] in ("dup", "cards", "openchat") for r in rows):
                continue  # 예약된 글 — 차례가 되면 봇이 발행 (예전에 열린 '발행 대기' 알림이 남아 있어도)
            if post is not None:
                blog, pid = post
                p = posts[blog].get(pid)
                if p is not None and p.get("status") == "published" and not (p.get("needs") or []):
                    if not any(r.key.split(":", 1)[0] in ("dup", "cards", "openchat") for r in rows):
                        continue
                needs = [KIND_NEED[r.kind] for r in rows if r.kind in KIND_NEED]
                item = self._post_item(blog, p, pid=pid, title=rows[0].title, needs=needs, long=long)
                extra = [r for r in rows if r.key.split(":", 1)[0] in ("dup", "cards", "openchat")]
                if extra and not item["detail"]["needs"]:
                    item["kind"] = extra[0].kind or "double_pub"
                    item["status"] = extra[0].title
                    url = (p or {}).get("naver_url")
                    item["primary"] = open_link("네이버에서 보기", url) if url else goto("자세히", f"#/i/{iid}")
                    item["more"] = [act("todo.dismiss", "다 했어요", {"iid": iid, "key": extra[0].key})]
                if item["detail"]["needs"] and "photos" in item["detail"]["needs"] and not item.get("links"):
                    # 기록 파일에 사진 출처가 없으면(예전 Blog-Auto·아직 안 씀) 알림이 가져온 링크로 (사진 받을 곳)
                    nl = next((r.links for r in rows if r.links), [])
                    if nl:
                        item["links"] = nl[:LINKS_MAX]
                        item["detail"]["photo_request"] = dict(item["detail"]["photo_request"] or {"must": []}, links=nl)
                item["since"] = min(r.opened_at for r in rows)
                item["urgent"] = any(r.urgent for r in rows) or item["urgent"]
                item["sev"] = min((r.sev for r in rows), key=lambda s: SEV_ORDER.get(s, 2))
                out[iid] = item
                continue
            item = self._notice_item(iid, rows, now)
            if item is not None:
                out[iid] = item
        # 기록 파일 posts[] 의 needs (알림이 없어도)
        for blog, rows in posts.items():
            for pid, p in rows.items():
                iid = iidmod.post_iid(blog, pid)
                if iid in queued:
                    continue
                blocked = self._sctx["ok"] and (p.get("schedule") or {}).get("status") == "blocked"
                if iid not in out and (p.get("needs") or blocked) and p.get("status") != "published":
                    out[iid] = self._post_item(blog, p, pid=pid, long=long)
        # 기록 파일 todo (알림 줄이 없는 것: 셈한 일 · 예전 길로 열린 일)
        known = {(n.src, n.key) for n in notices}
        todo = getattr(export, "todo", None)
        for t in (todo.items if todo is not None else []):
            head = t.key.split(":", 1)[0]
            if ("blogauto", t.key) in known or head in ("link", "review", "unsure") or t.group == "link_request":
                continue
            blog = t.blog if t.blog in ("finance", "issue", "hotdeal") else ""
            iid = iidmod.iid_for(t.key, blog, src="blogauto")
            if iid in out or iid in queued or iid.startswith("deal-"):
                continue
            if iidmod.post_of(iid) is not None:
                b, pid = iidmod.post_of(iid)  # type: ignore[misc]
                p = posts[b].get(pid)
                if p is not None and p.get("status") == "published":
                    continue
                out[iid] = self._post_item(b, p, pid=pid, title=t.short, needs=[KIND_NEED[t.kind]] if t.kind in KIND_NEED else [],
                                           long=long)
                continue
            sev = {"⛔[멈춤]": "stop", "🔥[핫]": "hot", "ℹ️": "info"}.get(t.marker, "todo")
            mall = iid.removeprefix("conn-mall-") if iid.startswith("conn-mall-") else ""
            primary = open_link(t.link and ("네이버에서 보기" if "naver.com" in t.link else "열기") or "열기", t.link) if t.link \
                else act("login.remote", "원격 화면으로 확인", {"target": mall}) if mall in REMOTE_TARGETS \
                else goto("스레드·인스타 보기", "#/blog/threads") if t.kind == "threads_todo" \
                else goto("블로그 보기", f"#/blog/{blog}") if blog else goto("자세히", "#/connect")  # threads_todo: 스레드 자동화가 남긴 할 일 (10/23)
            out[iid] = {"iid": iid, "src": "blog", "blog": blog, "kind": t.kind, "sev": sev, "urgent": t.urgent, "title": t.short,
                        "status": truncate(t.what, 60) if t.what and t.what != t.short else (ago(t.since, now_dt) if t.since else ""),
                        "since": _ts(t.since), "expires_at": None, "primary": primary,
                        "more": [act("todo.dismiss", "다 했어요", {"iid": iid, "key": t.key})]
                        if dismissible(iid, t.key, dealbot_row=False) else [], "detail": {"lines": []}}
        # '오늘 들를 이웃 글'은 할 일에 넣지 않는다 (주인 10/23 — 알림 없이, 블로그 화면 '이웃' 칸에서만 본다)
        if (srow := self._search_item(self.search_view(v2))) is not None:
            out[srow["iid"]] = srow
        if (xrow := xview.todo_item(x_stats_rows(self.bot.db), x_handles(self.bot.db), now, goto)) is not None:  # X 숫자 넣기 (주인 10/25 — 주 1회)
            out[xrow["iid"]] = xrow
        from dealbot.dashboard import remote_login

        if (vrow := remote_login.item(self.bot.db, open_link)) is not None:  # 원격 로그인 화면이 열려 있음 (주인 10/23)
            out[vrow["iid"]] = vrow
        hidden = hidden_rows(self.bot.db, now)  # 주인이 '알림 없애기'로 뺀 줄 (주인 10/23)
        items = [it for it in out.values() if not is_hidden(it, hidden)]
        for it in items:
            if not any(a and a["action"] == "todo.dismiss" for a in (it["primary"], *it["more"])):
                it["more"] = [*it["more"], act("todo.hide", "알림 없애기", {"iid": it["iid"], "since": it.get("since")})]
        supported = self._supported(self._ba_status())
        modes = self._modes(v2)
        for it in items:
            it["more"] = self._filter(it["more"], supported)
            for a in (it["primary"], *it["more"]):
                if a is not None:
                    self._mark_publish(a, modes)
            if it["primary"] is not None and supported is not None and it["primary"]["action"].startswith("blog.") \
                    and it["primary"]["action"][5:] not in supported:
                it["primary"] = dict(it["primary"], disabled=True, note="블로그 프로그램 업데이트를 기다리는 중")
            it.setdefault("chip", CHIPS.get(it.get("blog") or "", "딜" if it["src"] == "deal" else ""))
        return rank.order(items, now)  # 중요한·핫한 순 (rank.py)

    # ---------------------------------------------------------- 오늘 들를 이웃 글 (주인 10/18 — Blog-Auto visits)
    @staticmethod
    def _visits(visits: list[dict[str, Any]] | None, blog: str = "") -> list[dict[str, Any]] | None:
        """할 일(todo)인 것만, Blog-Auto 순서 그대로. 기록 파일에 visits 칸이 없으면(예전 Blog-Auto) None."""
        if visits is None:
            return None
        return [{"id": x["id"], "blog": x["blog"], "name": x["blog_name"] or x["target"], "title": x["title"], "url": x["url"],
                 "comment": x["comment"]} for x in visits if x["status"] == "todo" and (not blog or x["blog"] == blog)]

    def _visits_item(self, visits: list[dict[str, Any]] | None) -> dict[str, Any] | None:
        """할 일 한 줄 '오늘 들를 이웃 글 N개' (참고 — 맨 아래 쪽, 급하지 않음) → 누르면 목록 시트. 없으면 None."""
        xs = self._visits(visits) or []
        if not xs:
            return None
        n = {b: sum(x["blog"] == b for x in xs) for b in CHIPS}
        status = " · ".join(f"{CHIPS[b]} {k}" for b, k in n.items() if k) if sum(bool(k) for k in n.values()) > 1 else "댓글 초안 있어요"
        since = max((_iso_ts(x.get("created_at"), self.tz) or 0.0 for x in visits or []), default=0.0) or None
        return {"iid": VISITS_IID, "src": "blog", "blog": "", "kind": "visits", "sev": "info", "urgent": False,
                "title": f"오늘 들를 이웃 글 {len(xs)}개", "status": status, "since": since, "expires_at": None,
                "primary": goto("보기", f"#/i/{VISITS_IID}"), "more": [], "detail": {"visits": xs}}

    # ---------------------------------------------------------- 구글 검색 등록 체크리스트 (주인 10/18 22:20 — searchreg.py)
    def search_reg(self) -> SearchReg:
        return SearchReg(self.bot.db, tz=self.tz, now=lambda: self._now().timestamp())

    def search_view(self, v2: dict[str, Any] | None) -> dict[str, Any] | None:
        try:
            return self.search_reg().view(v2)
        except Exception as e:  # noqa: BLE001 — 못 읽어도 나머지 화면은 그대로
            log.warning("dashboard state: 검색 등록 목록을 못 읽음: %s", e)
            return None

    @staticmethod
    def _search_item(view: dict[str, Any] | None) -> dict[str, Any] | None:
        """할 일 한 줄 '키자드 등록 N개' (주인 10/25 '맨날 놓쳐' — 맨 위 쪽, searchreg 하루 5개) → 누르면 블로그 탭 체크리스트. 다 했으면 None."""
        n = (view or {}).get("left") or 0
        if not n:
            return None
        since = max((x["at"] for g in view["days"] for x in g["items"] if not x["done"]), default=None)  # type: ignore[index]
        return {"iid": SEARCH_IID, "src": "blog", "blog": "", "kind": "search_reg", "sev": "todo", "urgent": False, "n": n,
                "title": f"키자드 등록 {n}개", "status": " · ".join(f"{k} {v}" for k, v in (view.get("per_blog") or {}).items())
                + f" — 블로그마다 하루 {view.get('per_day') or 5}개 · 주소 복사 → 키자드에 붙여넣기", "since": since, "expires_at": None,
                "primary": goto("보기", "#/blog/search"), "more": [], "detail": {"lines": []}}

    def item(self, iid: str) -> dict[str, Any] | None:
        """/api/item/<iid> — 긴 칸까지. 스레드 답글·정리 시트도 여기."""
        if iid == "sns-replies":
            router = getattr(self.bot, "router", None)
            replies = router.store.open_replies(time.time()) if router is not None else []
            return {"iid": iid, "src": "dealbot", "blog": "", "kind": "threads_replies", "sev": "todo", "urgent": False,
                    "title": f"스레드 답글 {len(replies)}개", "status": "답변 초안 있어요" if replies else "답글 없어요",
                    "since": replies[-1]["at"] if replies else None, "expires_at": None,
                    "primary": act("todo.dismiss", "다 했어요", {"iid": iid, "key": "threads_replies:"}), "more": [],
                    "detail": {"replies": [self._reply(r) for r in replies]}}
        for it in self.items(long=True):
            if it["iid"] == iid:
                return it
        post = iidmod.post_of(iid)
        if post is not None:  # 할 일이 없는 기다리는 글도 시트는 열린다 (블로그 → 기다리는 글)
            export = self._export()
            p = next((x for x in (getattr(export, "v2", None) or {}).get("posts", {}).get(post[0], []) if x["pid"] == post[1]), None)
            if p is not None:
                return rank.public(self._post_item(post[0], p, pid=post[1], long=True))
        return None

    @staticmethod
    def _reply(r: dict[str, Any]) -> dict[str, Any]:
        return {"id": r["reply_id"], "post": r.get("post_excerpt") or "", "reply": r.get("reply_text") or "",
                "who": r.get("username") or "", "drafts": r.get("drafts") or [], "open": link_target(r.get("open_url")),
                "at": r.get("at")}

    # ---------------------------------------------------------- 탭별 값
    def _deal_row(self, it: Any, hotdeal_recent: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        p = it.deal.product
        shop = self._shop(p.shop)
        post = self.bot.db.latest_post(p.product_id) or {}
        base = getattr(self.bot.notifier, "channel_post_base", None)
        url = f"{base}/{post['message_id']}" if base and post.get("message_id") and not post.get("dry_run") else None
        kind = "owner" if p.extra.get("owner_photo") else ("verified" if p.extra.get("photo_ok") or p.extra.get("photo_kind") == "verified" else "none")
        until = self.bot.late_photo.until(it.id) if hasattr(self.bot, "late_photo") else None
        at = _iso_ts(post.get("posted_at"), self.tz) or _ts(it.created_at)
        row = {"qid": it.id, "title": _name(p.name), "shop": shop.name if shop else p.shop,
               "price": f"{p.price:,}원" if p.has_price else "", "at": at, "url": url, "photo": kind}
        # 같은 딜이 간 다른 곳 (주인 10/18 '왜 링크가 텔레그램 밖에 없어?'): 핫딜 블로그 글·인스타·스레드 — 아는 주소만
        since = datetime.fromtimestamp(at, UTC) - timedelta(hours=1) if at else self._now() - timedelta(days=2)
        links = {"blog_url": blog_url_for(p.product_id, at, hotdeal_recent, self.tz),
                 "ig_url": sns_url(self.bot.db.ig_permalink_for(it.id, since), IG_HOSTS),
                 "th_url": sns_url(self.bot.db.threads_permalink_for(it.id, since), THREADS_HOSTS)}
        row.update({k: v for k, v in links.items() if v})
        if until is not None and until > self._now() and self.bot.late_photo_waiting(it.id):
            row["photo_wait"] = {"until": _ts(until), "iid": iidmod.deal_iid(it.id)}
        return row

    def deal(self, export: Any = None) -> dict[str, Any]:
        bot = self.bot
        hotdeal = ((getattr(export, "v2", None) or {}).get("recent") or {}).get("hotdeal") or []
        now = self._now()
        tz = ZoneInfo(self.tz)
        start = now.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
        today = bot.db.published_items_between(start, now + timedelta(minutes=1), include_dry_run=bot.state.dry_run)
        recent = sorted(bot.db.published_items_between(now - timedelta(hours=24), now + timedelta(minutes=1),
                                                       include_dry_run=bot.state.dry_run), key=lambda x: x.id, reverse=True)[:10]
        tc = bot.toss_counts(start)  # 주인 10/23: 토스 쉐어링크 딜이 얼마나 모이고 올라가는지 한 줄 (서버가 글까지 만들어 앱은 그대로 그림)
        toss = f"토스 후보 {tc['candidates']} · 링크 대기 {tc['waiting']} · 오늘 올림 {tc['today']}"
        return {"status": {"paused": bot.state.paused, "ok": not bot.state.paused, "today": len(today), "dry_run": bot.state.dry_run,
                           "toss": toss},
                "today": [self._deal_row(it, hotdeal) for it in recent], "queue": bot.queue_view(10), "collectors": bot.collector_names(),
                "actions": [act("deal.post", "새 딜 직접 올리기", input="text", placeholder="몰 주소\n상품명\n가격"),
                            act("bot.run", "지금 수집", input="choice",
                                choices=[{"value": "all", "label": "전부"}] + [{"value": n, "label": bot.notifier.source_label(n)}
                                                                               for n in bot.collector_names()]),
                            act("deal.find", "찾기", input="search", url="/dashboard/api/deal/find"),
                            act("deal.hot", "추천 많은 글", input="search", url="/dashboard/api/deal/hot"),
                            act("bot.resume", "다시 시작") if bot.state.paused else act("bot.pause", "딜봇 멈추기")],
                "nolink": self._nolink_stores()}

    def _nolink_stores(self) -> dict[str, Any] | None:
        """딜 탭 조용한 한 줄 '쇼핑 커넥트 없는 판매처 N곳 — 링크 없이 바로 올려요' → 누르면 하나씩 빼는 시트. 없으면 None."""
        stores = self.bot.sc_store_view()
        if not stores:
            return None
        on = self.bot.settings.autolink.naver.no_link_autopost
        return act("deal.sc_forget", f"쇼핑 커넥트 없는 판매처 {len(stores)}곳 — " + ("링크 없이 바로 올려요" if on else "자동 올리기 꺼짐"),
                   input="choice", title="쇼핑 커넥트 없는 판매처",
                   note="누르면 목록에서 빠지고, 다음 딜부터 다시 링크를 물어요",
                   choices=[{"value": e["key"], "label": e["label"]} for e in stores[:50]])

    def blogs(self, export: Any, todo: list[dict[str, Any]]) -> dict[str, Any]:
        v2 = getattr(export, "v2", None) or {}
        st_blogs = (v2.get("status") or {}).get("blogs") or {}
        supported = self._supported(self._ba_status())
        self._sched_ctx(v2)
        out = {}
        for blog in ("finance", "issue", "hotdeal"):
            st = st_blogs.get(blog) or {}
            waiting = [it for it in todo if it.get("blog") == blog and it["iid"].startswith("post-")]
            have = {it["iid"] for it in waiting}
            planned: list[tuple[float, dict[str, Any]]] = []
            for p in v2.get("posts", {}).get(blog, []):
                iid = iidmod.post_iid(blog, p["pid"])
                if self._sctx["ok"] and self.queued(p):  # 예약된 글은 '예약된 글' 칸에만 (두 번 보이지 않게)
                    at = _iso_ts((p.get("schedule") or {}).get("scheduled_at"), self.tz) or 0.0
                    planned.append((at, rank.public(self._post_item(blog, p, pid=p["pid"]))))
                elif iid not in have and p.get("status") != "published":
                    waiting.append(rank.public(self._post_item(blog, p, pid=p["pid"])))
            acts: list[dict[str, Any]] = []
            if blog == "finance":
                acts += [act("blog.scan", "다시 찾기"), act("blog.write", "새 글 쓰기", input="text", placeholder="어떤 글을 쓸지",
                                                             photos_optional=True, accept="image/*", multiple=True)]
            if blog == "issue":
                acts += [act("blog.issue", "키워드로 쓰기", input="text", optional=True, placeholder="키워드 (비우면 알아서)"),
                         act("blog.info", "키워드 보기", {"what": "trends"}), act("blog.info", "일정 보기", {"what": "plan"}),
                         act("blog.restyle", "Pick 전체 스타일 다시", {"blog": "issue", "pids": "all"})]
            if blog == "hotdeal":
                acts.append(act("blog.hotdeal_now", "지금 한 편 쓰기"))
                if (na := self._notes_act(blog)):
                    acts.append(na)
            mode = st.get("mode") or "draft"
            acts.append(act("blog.mode", "발행 모드로" if mode == "draft" else "초안 모드로",
                            {"blog": blog, "mode": "publish" if mode == "draft" else "draft"}))
            acts.append(act("blog.resume", "다시 시작", {"blog": blog}) if st.get("paused") else act("blog.pause", "멈추기", {"blog": blog}))
            acts.append(act("blog.drafts_cleanup", "임시저장 정리", {"blog": blog}))
            if blog == "finance":
                acts.append(act("blog.ledger", "정리본 올리기", input="file", accept=".md,.txt,text/markdown,text/plain"))
            acts.append(act("blog.info", "크리에이터 키워드", {"what": "creator"}))
            # 소재 넣기 (주인 10/21): 카페 인기글 주제·아이디어를 붙여 넣으면 다음 글감 고르기의 첫 후보 (Blog-Auto 는 카페를 열지 않음)
            acts.append(act("blog.topic_seed", "소재 넣기", {"blog": blog}, input="text",
                            placeholder="카페 인기글 주제나 아이디어 한두 줄 — 사실은 뉴스·공식 자료로만 확인해요"))
            scan = v2.get("scan") if blog == "finance" else None
            out[blog] = {"label": BLOG_LABELS[blog], "chip": CHIPS[blog], "mode": mode, "paused": bool(st.get("paused")),
                         "today": st.get("today") or 0, "login": st.get("login") or "unknown",
                         "waiting": waiting, "recent": [dict(r, at=_iso_ts(r.get("at"), self.tz)) for r in v2.get("recent", {}).get(blog, [])],
                         "scan": ({"sid": scan["sid"], "at": _iso_ts(scan.get("at"), self.tz),
                                   "items": [{"n": x["n"], "title": x["title"], "why": "인기 예상" if x.get("recommend") else "",
                                              "action": act("blog.pick", "이걸로 쓰기", {"sid": scan["sid"], "n": x["n"]})}
                                             for x in scan["items"]]} if scan else None),
                         "actions": self._filter(acts, supported),
                         "visits": self._visits(v2.get("visits"), blog), "neighbors": neighbors_line(st.get("neighbors")),
                         "schedule": self._sched_blog(blog, v2, planned) if self._sctx["ok"] else None}
            if blog == "issue":  # 인기 검색어 (10/20) — 누르면 '키워드로 쓰기'(blog.issue) 그대로 · 그 일이 없으면(예전 Blog-Auto) 보기만
                out[blog]["trends"] = self._trends(v2.get("trends"), out[blog]["actions"])
            out[blog]["learn"] = self._learn(v2.get("learn"), blog, supported)  # 이번 주 개선안·적용 규칙 (10/20)
            for r in out[blog]["recent"]:
                r.update(self._submit_links(r))
                ph = r.pop("photos", None)
                if ph is not None and r.get("pid") and (supported is None or "post_edit" in supported):
                    # 발행 글 고치기 (주인 10/21): 사진마다 빼기·교체 · '이 문장 빼기' 칸 → blog.post_edit (★ 확인)
                    r["edit"] = {"photos": [dict(p, thumb=f"/dashboard/thumb/{p['thumb']}" if p.get("thumb") else None) for p in ph],
                                 "act": act("blog.post_edit", "고치기", {"blog": blog, "pid": r["pid"]})}
                if out[blog]["learn"] is not None and r.get("pid") and (supported is None or "post_feedback" in supported):
                    r["fb"] = {"vote": (v2["learn"]["votes"]).get(str(r["pid"])),  # 👍/👎 (10/20) — 다시 누르면 지움
                               "act": act("blog.post_feedback", "평가", {"blog": blog, "pid": r["pid"]})}
            for a in out[blog]["actions"] + [x["action"] for x in (out[blog]["scan"] or {}).get("items", [])]:
                self._mark_publish(a, self._modes(v2))
        return out

    @staticmethod
    def _learn(lr: dict[str, Any] | None, blog: str, supported: set[str] | None) -> dict[str, Any] | None:
        """블로그 탭 '이번 주 개선안' 카드 (주인 10/20 — Blog-Auto learn.py): 이 블로그(+모든 블로그) 개선안 ✅ 적용 / ❌ 빼기 ·
        적용 중인 규칙 (지우기). 누르기 = blog.learn_decide (되돌릴 수 있어 확인 없음). 칸이 없으면 None (카드 없음)."""
        if not lr:
            return None
        ok = supported is None or "learn_decide" in supported
        props, rules = [], []
        for p in lr["proposals"]:
            if p["blog"] in (blog, "all"):
                x = dict(p)
                if ok and p["state"] == "open":
                    x["apply"] = act("blog.learn_decide", "✅ 적용", {"id": p["id"], "decision": "apply"})
                    x["reject"] = act("blog.learn_decide", "❌ 빼기", {"id": p["id"], "decision": "reject"})
                props.append(x)
        for r in lr["rules"]:
            if r["blog"] in (blog, "all"):
                rules.append(dict(r, remove=act("blog.learn_decide", "지우기", {"id": r["id"], "decision": "remove"}) if ok else None))
        return {"week": lr["week"], "lessons_week": lr["lessons_week"], "auto_apply_days": lr["auto_apply_days"],
                "proposals": props, "rules": rules}

    def _trends(self, tr: dict[str, Any] | None, acts: list[dict[str, Any]]) -> dict[str, Any] | None:
        """Pick 블로그 화면 '인기 검색어' 카드 (주인 10/20): 순위 · 출처 · ▲▼ · 이미 쓴 글 · 패션/연예. 누르기 = blog.issue {keyword}
        (/issue 키워드와 같은 길 — 확인 창 기준도 같음). Blog-Auto 가 칸을 안 주면 None (카드 없음)."""
        if not tr:
            return None
        base = next((a for a in acts if a.get("action") == "blog.issue"), None)
        return {"at": _iso_ts(tr.get("at"), self.tz), "items": tr["items"],
                "write": dict(base, label="이 키워드로 Pick 쓰기") if base else None}

    def _sched_blog(self, blog: str, v2: dict[str, Any], planned: list[tuple[float, dict[str, Any]]]) -> dict[str, Any]:
        """블로그 탭 '예약된 글 N편' (다음 차례 먼저) · 모두 취소(★) · 간격 두고만 발행하는 블로그인지."""
        info = ((v2.get("schedule") or {}).get("blogs") or {}).get(blog) or {}
        items = [it for _, it in sorted(planned, key=lambda x: x[0])]
        return {"spaced_only": bool(info.get("spaced_only")), "count": len(items),
                "next_at": _iso_ts(info.get("next_at"), self.tz), "items": items,
                "cancel_all": act("blog.unschedule_all", "모두 취소", {"blog": blog}, danger=True) if items else None}

    @staticmethod
    def _submit_links(r: dict[str, Any]) -> dict[str, Any]:
        """최근 글 줄의 검색 등록 (주인 10/18 — 구글 자동 등록은 꺼져 있음): 구글 = 글 주소 복사하기 ·
        다음 = 블로그 검색으로 들어갔는지 보기 (다음은 블로그를 한 번 등록해 두면 새 글이 RSS 로 들어감)."""
        from urllib.parse import quote

        url = r.get("url") or ""
        title = (r.get("title") or "").strip()
        return {"google": {"copy": url.replace("https://m.blog.naver.com/", "https://blog.naver.com/"),
                           "url": "https://keyzard.cc/nb"} if url else None,
                "daum": f"https://search.daum.net/search?w=fusion&col=blog&q={quote(title)}" if title else None}

    def sns(self) -> dict[str, Any]:
        bot = self.bot
        now = self._now()
        rows = {"threads": bot.reporter._threads_row(now), "instagram": bot.reporter._instagram_row(now)}
        out: dict[str, Any] = {}
        for name, r in rows.items():
            state = {"ok": "ok", "unlinked": "broken", "noapp": "off", "off": "off"}.get(r.get("state"), "off")
            text = {"ok": "연결됨", "broken": "연결 끊김", "off": "꺼짐"}[state]
            if state == "ok" and r.get("expires"):
                text += f" · {r['expires']} 까지"
            if r.get("last_fail"):
                state, text = ("expiring" if state == "ok" else state), text + " · 최근 실패 있음"
            out[name] = {"state": state, "text": text, "fix": "connect" if state in ("broken", "expiring") else None,
                         "connect_url": f"/dashboard/connect/{name}"}
            if state == "ok":  # 연결이 멀쩡하면 남아 있던 '연결 끊김'·'발행 실패' 할 일은 닫는다 (다시 연결한 뒤에도 남던 것)
                cmd = "threadsauth" if name == "threads" else "instaauth"
                try:
                    bot.notifier.resolve(f"sns:{cmd}", f"sns_fail:{cmd}:")
                except Exception:  # noqa: BLE001
                    pass
        tg = None
        try:
            got = bot.db.metric_latest("telegram", "subscribers")
            tg = int(got["value"]) if got else None
        except Exception:  # noqa: BLE001
            tg = None
        out["telegram"] = {"members": tg, "today": self.deal()["status"]["today"]}
        out["invites"] = self._invites()
        router = getattr(bot, "router", None)
        replies = router.store.open_replies(time.time(), 20) if router is not None else []
        out["replies"] = [self._reply(r) for r in replies]
        # 토큰·코드 직접 넣기는 '다시 연결'(승인 화면)이 안 될 때의 예비 — 끊겼거나 곧 끝나는 쪽만, 이름을 붙여서
        acts: list[dict[str, Any]] = []
        for name, label in (("threads", "스레드"), ("instagram", "인스타")):
            if out[name]["state"] in ("broken", "expiring"):
                acts += [act("sns.token", f"{label} 토큰 넣기", {"sns": name}, input="secret", placeholder=f"{label} 토큰 붙여넣기"),
                         act("sns.code", f"{label} 코드 넣기", {"sns": name}, input="secret", placeholder=f"{label} 승인 코드 붙여넣기")]
        out["actions"] = [*acts, act("sns.test", "스레드 테스트 글", {"sns": "threads", "qid": None}),
                          act("sns.test", "인스타 테스트 글", {"sns": "instagram", "qid": None}),
                          act("sns.invites", "초대 링크 새로 만들기")]
        return out

    def _invites(self) -> list[dict[str, Any]]:
        from dealbot.growth.invites import INVITE_NAMES, LABELS, stored_links

        links = stored_links(self.bot.db)
        where = {"ig_bio": "인스타 프로필 링크", "threads_bio": "스레드 프로필 링크", "deals_page": "핫딜 페이지 버튼",
                 "demiyum": "핫딜 블로그 서명", "qr": "QR 코드"}
        return [{"name": n, "label": LABELS.get(n, n), "where": where.get(n, ""), "url": links[n]} for n in INVITE_NAMES if n in links]

    SC_REASONS = {"login": "아카이브 계정 로그인 필요", "blocked": "네이버가 잠깐 막음", "owner": "직접 멈춤"}

    def _sc_row(self, sc: dict[str, Any] | None, now: float) -> dict[str, Any] | None:
        """연결 탭 '쇼핑커넥트' 한 줄 (주인 10/24 — 실시간 연결 상태): {text, bad, action?}. 상태를 모르면 None (줄 숨김)."""
        if not sc:
            return None
        st = sc["state"]
        if st == "off":
            return {"text": "꺼짐", "bad": False}
        if st == "unknown":
            return {"text": "확인 못 함", "bad": True}
        tz = ZoneInfo(self.tz)
        if st == "stopped":
            why = self.SC_REASONS.get(sc["reason"], sc["reason"] or "멈춤")
            until = sc.get("until")
            head = f"멈춤 — {why}" + (f" · {datetime.fromtimestamp(until, tz):%H:%M}까지" if until and until - now < 86400 else "")
        else:
            head = "정상" if st == "ok" else f"쉬는 시간 (일하는 시간 {sc.get('hours') or '-'})"
        t, lim = sc["today"], sc["limits"]
        parts = [head, f"오늘 링크 {t['issues']}/{lim['issues']} · 검색 {t['searches']}/{lim['searches']}"]
        if sc["queued"]:
            parts.append(f"대기 {sc['queued']}")
        if sc.get("last_ok_at"):
            parts.append(f"마지막 성공 {datetime.fromtimestamp(sc['last_ok_at'], tz):%m/%d %H:%M}")
        row: dict[str, Any] = {"text": " · ".join(parts), "bad": st == "stopped" and sc["reason"] != "owner"}
        if st == "stopped" and sc["reason"] == "login":
            row["action"] = act("login.remote", "원격 로그인", {"target": "finance"})
        return row

    def connect(self, export: Any, session: str) -> dict[str, Any]:
        bot = self.bot
        now = time.time()
        router = getattr(bot, "router", None)
        devices = []
        labels: dict[str, int] = {}
        if router is not None:
            for s in router.store.subs():
                lab = s.get("label") or "기기"
                labels[lab] = labels.get(lab, 0) + 1
                devices.append({"id": s["id"], "label": lab if labels[lab] == 1 else f"{lab} {labels[lab]}", "last_ok": s.get("last_ok"),
                                "this": s.get("session") == session, "ok": not s.get("fails")})
        st = self._ba_status()
        k_state = st.kakao_state if st.fresh(now) else "unknown"
        exp_days = int((st.kakao_expires_at - now) // 86400) if st.kakao_expires_at else None
        if k_state == "ok" and exp_days is not None and exp_days <= 3:
            k_state = "expiring"
        v2 = getattr(export, "v2", None) or {}
        naver = {b: {"login": (v2.get("status", {}).get("blogs", {}).get(b) or {}).get("login") or "unknown",
                     "checked_at": _iso_ts((v2.get("status", {}).get("blogs", {}).get(b) or {}).get("login_checked"), self.tz),
                     "action": act("blog.login_check", "지금 확인", {"blog": b})} for b in ("finance", "issue", "hotdeal")}
        kakao_actions = [act("blog.kakao_test", "테스트 카톡")]
        if k_state in ("down", "expiring") or st.kakao_relink:
            kakao_actions = [act("blog.kakao_auth_url", "다시 연결"),
                             act("blog.kakao_connect", "연결하기", input="secret", placeholder="카카오에서 돌아온 주소",
                                 hint="카카오에서 동의한 뒤 바뀐 주소를 복사해 여기 붙여넣어 주세요"), *kakao_actions]
        oauth = self._take_oauth_note()
        return {
            "push": {"devices": devices, "key_ok": router is not None and router.key_error is None,
                     "actions": [act("push.test", "테스트 알림")]},
            "kakao": {"state": k_state, "expires_in_days": exp_days, "actions": self._filter(kakao_actions, self._supported(st))},
            "naver": naver,
            "sc": self._sc_row((v2.get("status") or {}).get("shopconnect"), now),
            "check": act("blog.server_check", "서버 점검"),  # 읽기만 하는 정해진 점검 (srvcheck.py) — 결과는 한 시트로
            "bots": {"dealbot": {"ok": True, "paused": bot.state.paused, "version": self._version(), "updated": self._updated(),
                                 "collectors": self._collectors()},
                     "blogauto": {"ok": st.fresh(now), "seen_at": st.at, "bridge": st.bridge, "actions": st.actions,
                                  "version": st.version}},
            "errors": bot.errors_view(8),
            "audit": self.audit_lines(20),
            "notice": oauth,
        }

    def _version(self) -> str:
        from dealbot.monitoring.admin import version_label

        try:
            return version_label()
        except Exception:  # noqa: BLE001
            return ""

    @staticmethod
    def _updated() -> str:
        """화면에 보일 코드 시각만 ('10/17 19:24') — 버전 번호·커밋 해시는 보이지 않는다."""
        from dealbot.monitoring.admin import CODE_VERSION

        parts = str(CODE_VERSION or "").split(" ", 1)
        return parts[1] if len(parts) == 2 else ""

    def _collectors(self) -> list[dict[str, Any]]:
        out = []
        now = self._now()
        for name, stt in self.bot.state.collectors.items():
            run = self.bot.db.last_run(name)
            last = run.finished_at or run.started_at if run is not None else None
            ok = run is None or run.status != "error"
            out.append({"name": name, "label": self.bot.notifier.source_label(name), "enabled": bool(stt.enabled and stt.available),
                        "ok": ok, "last_at": _ts(last), "text": ago(last, now) if last else "아직 안 봄"})
        return out

    def _take_oauth_note(self) -> dict[str, Any] | None:
        """스레드·인스타 연결이 끝난 뒤 첫 화면에 한 번 ('스레드 연결됐어요')."""
        raw = self.bot.db.kv_take("dash:oauth_note")
        try:
            note = json.loads(raw or "null")
        except ValueError:
            return None
        if not isinstance(note, dict) or time.time() - float(note.get("at") or 0) > 3600:
            return None
        return {"ok": bool(note.get("ok")), "text": str(note.get("text") or "")[:80]}

    def audit_lines(self, limit: int = 20) -> list[dict[str, Any]]:
        # 로그인 실패는 남긴다 (대시보드가 발행까지 하니 가장 쓸모 있는 보안 신호) — 성공·로그아웃은 뺌
        rows = self.bot.db._q("SELECT ts, action, target, result_code, client8 FROM dash_audit WHERE action NOT IN ('state', 'logout') "
                              "AND NOT (action = 'login' AND result_code != 'failed') ORDER BY ts DESC LIMIT ?", (limit,))
        tz = ZoneInfo(self.tz)
        out = []
        for r in rows:
            when = datetime.fromtimestamp(r["ts"], tz).strftime("%H:%M")
            if r["action"] == "login":
                out.append({"at": r["ts"], "text": f"{when} 로그인 실패 — 접속 {(r['client8'] or '?')[:8]}", "bad": True})
                continue
            what = ACTION_LABELS.get(r["action"], "대시보드 일")
            target = _target_label(r["target"] or "")
            res = RESULT_LABELS.get(r["result_code"] or "", "안 됨")
            out.append({"at": r["ts"], "text": f"{when} {target + ' ' if target else ''}{what} — {res}"})
        return out

    # ---------------------------------------------------------- 전체
    def state(self, *, session: str, csrf: str, logout_t: str = "") -> dict[str, Any]:
        bot = self.bot
        export = self._export()
        todo = self.items(export=export)
        router = getattr(bot, "router", None)
        st = self._ba_status()
        connect = self.connect(export, session)
        banner = None
        kakao_down = connect["kakao"]["state"] == "down"
        if router is not None and router.nowhere(kakao_down=kakao_down):
            banner = {"kind": "nowhere", "text": "알림이 어디로도 가지 않아요 — 연결에서 알림을 켜 주세요"}
        elif router is not None and not any(d["this"] for d in connect["push"]["devices"]):
            banner = {"kind": "push_off", "text": "이 기기에서 알림을 켜면 할 일이 바로 와요"}
        pending = [self._status(r) for r in bot.actions_out.store.open_rows()][:20]
        blogs = self.blogs(export, todo)
        body: dict[str, Any] = {
            "v": STATE_V, "at": time.time(), "app": _app_ver(),  # 화면이 옛 app.js 로 떠 있으면 스스로 새로 열게 (10/23)
            "me": {"csrf": csrf, "push_key": router.public_key if router is not None else None,
                   "public_url": bot.settings.dashboard.public_url, "logout_t": logout_t},
            "banner": banner,
            "counts": {"todo": len(todo), "done_today": today_count(bot, now=time.time(), v2=getattr(export, "v2", None))},  # 오늘 한 일 (목록은 /api/today)
            "todo": todo,
            "deal": self.deal(export),
            "blogs": blogs,
            "sched": schedview.build(blogs, getattr(export, "v2", None) or {}, self.tz, time.time(), self._sctx["ok"]),  # 일정 화면 (10/21)
            "sns": self.sns(),
            "connect": connect,
            "pending": pending,
            "digest": {"latest": router.store.latest_digest() if router is not None else None},
            "blogauto": {"bridge": st.bridge, "fresh": st.fresh()},
            "pics": self.pics_tool(export),
            "photo_trial": self.photo_trial(export),
            "search": self.search_view(getattr(export, "v2", None) or {}),
            "ptrial": self.product_trial(export),  # 제품 테스트 (10/21)
            "thpost": self.thpost(export),  # 스레드 글·통계 (10/21 — thpost.py · Blog-Auto threads 칸이 없으면 None)
            "earnings": self.earnings(export),  # 통계 탭 '제휴 수익' (10/23 — earn.py · 그리기는 처음 열 때만 받는 earn.js)
            "devlog": self.devlog(export),  # 할 일 페이지 맨 아래 '오늘 바뀐 것' (10/23 — 화면이 title·rows 를 그대로 그림)
            "opscheck": self.opscheck(export),  # 그 아래 '직전 AI 점검' (10/25 — 같은 모양 title·rows)
            "x": self.xview(export),  # 블로그 탭 'X(트위터) →' (10/25 — xview.py · 그리기는 처음 열 때만 받는 xpost.js)
        }
        return body

    @staticmethod
    def devlog(export: Any) -> dict[str, Any] | None:
        """할 일 페이지 맨 아래 접힌 '오늘 바뀐 것 · N개' 칸 (주인 10/23 — Blog-Auto devlog 칸). 화면(app.js)은 이 말을 그대로 그린다:
        {title, rows: [{at, repo, text}]}. 칸이 없거나 비었으면 None (칸이 안 보임)."""
        d = (getattr(export, "v2", None) or {}).get("devlog")
        if not d or not d["items"]:
            return None
        rows = [{"at": x["at"], "repo": x["repo"], "text": x["text"] + (f" ({x['count']}번)" if x["count"] > 1 else "")} for x in d["items"]]
        return {"title": f"오늘 바뀐 것 · {d['n']}개", "rows": rows}

    @staticmethod
    def opscheck(export: Any) -> dict[str, Any] | None:
        """할 일 페이지 맨 아래 접힌 '직전 AI 점검' 칸 (주인 10/25 — 매시간 오류 점검 AI 의 바로 전 차례 기록만, Blog-Auto opscheck 칸).
        화면(app.js)은 이 말을 그대로 그린다: {title, rows: [{text, bad?}]} — 첫 줄은 결과 한 줄. 칸이 없으면 None (칸이 안 보임)."""
        d = (getattr(export, "v2", None) or {}).get("opscheck")
        if not d:
            return None
        mark = OPS_MARK[d["result"]]
        rows = [{"text": f"{mark} {d['summary']}", "bad": d["result"] in ("blocked", "owner")}] + [{"text": t} for t in d["lines"]]
        return {"title": f"직전 AI 점검 · {d['at']} · {d['summary']}", "rows": rows}

    def earnings(self, export: Any) -> dict[str, Any] | None:
        """통계 탭 '제휴 수익' 칸: 쿠팡(자동 · DB) + 쇼핑커넥트(Blog-Auto 기록 파일) + 직접 넣은 것. DB 를 못 읽으면 None (칸이 안 보임)."""
        store = getattr(self.bot, "earn_store", None)
        if store is None:
            return None
        v2 = getattr(export, "v2", None) or {}
        sc = (v2.get("earnings") or {}).get("shopconnect")
        try:
            return earn.build(store, self.tz, time.time(), sc, coupang_on=self.bot.coupang is not None,
                              sub_id_set=bool(self.bot.settings.secrets.coupang_sub_id), cm_field=v2.get("creator_mall"),
                              channel_subs=self.bot.settings.links.coupang_sub_ids, default_sub=self.bot.settings.secrets.coupang_sub_id)
        except Exception as e:  # noqa: BLE001 — 수익 칸 하나가 상태 전체를 막지 않게
            log.warning("earnings state failed: %s", type(e).__name__)
            return None

    def thpost(self, export: Any) -> dict[str, Any] | None:
        """스레드 글·통계 (주인 10/21): 주인이 쓴 글 바로·예약 · 예약 취소 · 계정의 최근 글 · 새 답글에 답 · 공식 인사이트."""
        sup = self._supported(self._ba_status())
        ok = sup is None or {"threads_post", "threads_cancel", "threads_reply"} <= sup
        return thpost.build((getattr(export, "v2", None) or {}).get("threads"), ZoneInfo(self.tz), time.time(), act, bridge_ok=ok)

    def xview(self, export: Any) -> dict[str, Any] | None:
        """X(트위터) 화면 (주인 10/25): 진행률(주인이 넣는 숫자) · 데런픽 초안함(Blog-Auto x 칸) · 수익 계정 글감 카드. X API 없음."""
        sup = self._supported(self._ba_status())
        ok = sup is None or {"x_posted", "x_drop", "x_redo"} <= sup
        try:
            db = self.bot.db
            return xview.build((getattr(export, "v2", None) or {}).get("x"), x_stats_rows(db), x_handles(db), ZoneInfo(self.tz),
                               time.time(), act, bridge_ok=ok)
        except Exception as e:  # noqa: BLE001 — X 칸 하나가 상태 전체를 막지 않게
            log.warning("x state failed: %s", type(e).__name__)
            return None

    @staticmethod
    def _status(row: dict[str, Any]) -> dict[str, Any]:
        from dealbot.bridge.actions_out import status_of

        return status_of(row)

    @staticmethod
    def etag(body: dict[str, Any]) -> str:
        """'at' 를 뺀 값의 해시 (304 용)."""
        copy = {k: v for k, v in body.items() if k != "at"}
        raw = json.dumps(copy, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
        return "s_" + hashlib.sha256(raw).hexdigest()[:12]


ACTION_LABELS = {
    "deal.link": "링크 넣기", "deal.skip": "안 올리기", "deal.ok": "올리기", "deal.ok_text": "고쳐서 올리기", "deal.posted": "채널에 있음",
    "deal.repost": "다시 올리기", "deal.late_photo": "사진 올리기", "deal.post": "새 딜 올리기", "bot.pause": "딜봇 멈춤",
    "bot.resume": "딜봇 다시 시작", "bot.run": "지금 수집", "sns.token": "토큰 넣기", "sns.code": "코드 넣기", "sns.test": "테스트 글",
    "sns.invites": "초대 링크 만들기", "push.test": "테스트 알림", "push.subscribe": "알림 켜기", "push.unsubscribe": "알림 끄기",
    "todo.dismiss": "다 했어요", "stats.refresh": "다시 읽기", "stats.input": "직접 넣기", "earn.save": "수익 넣기", "earn.delete": "수익 지우기", "upload": "사진 올림",
    "blog.publish": "발행", "blog.drop": "버리기", "blog.retry": "다시 올리기", "blog.fix": "말로 고치기", "blog.fixpic": "사진도 바꾸기",
    "blog.rewrite": "다시 쓰기", "blog.restyle": "스타일 다시", "blog.photos": "사진 올리기", "blog.photo_clear": "자리 비우기",
    "blog.photos_batch": "사진 한 번에 고치기", "blog.post_notes": "내 후기 쓰기",
    "blog.similar_links": "비슷한 아이템 링크 넣기", "blog.topic_seed": "소재 넣기", "blog.photo_feedback": "사진 피드백",
    "blog.product_feedback": "제품 테스트",
    "blog.post_edit": "발행 글 고치기",
    "blog.answer": "답하기", "blog.noanswer": "답 없이 진행", "blog.oedit_keep": "그대로 두기", "blog.oedit_redo": "봇 글로 덮어쓰기",
    "blog.share": "보내기", "blog.noshare": "보내지 않기", "blog.channel": "채널에 올리기", "blog.scan": "글감 찾기",
    "blog.pick": "글감으로 쓰기", "blog.write": "새 글 쓰기", "blog.issue": "키워드로 쓰기", "blog.hotdeal_now": "한 편 쓰기",
    "blog.mode": "모드 바꾸기", "blog.pause": "블로그 멈춤", "blog.resume": "블로그 다시 시작", "blog.drafts_cleanup": "임시저장 정리",
    "blog.ledger": "정리본 바꾸기", "blog.login_check": "로그인 확인", "blog.server_check": "서버 점검", "blog.info": "보기", "blog.kakao_auth_url": "카톡 다시 연결",
    "blog.kakao_connect": "카톡 연결", "blog.kakao_test": "테스트 카톡", "blog.dismiss": "다 했어요",
    "blog.pics_fetch": "사진 찾기", "blog.pics_clear": "사진 후보 지우기", "search.check": "검색 등록 표시",
    "blog.x_posted": "X 올렸어요", "blog.x_drop": "X 초안 버리기", "blog.x_redo": "X 초안 다시 쓰기", "x.stats": "X 숫자 저장",
    "x.handles": "X 계정 이름 저장",
    "connect": "다시 연결 시작",
}
RESULT_LABELS = {"queued": "요청함", "done": "완료", "linked": "완료", "linked_twin": "완료", "approved": "완료", "skipped": "완료",
                 "posted": "완료", "paused": "완료", "resumed": "완료", "run": "완료", "connected": "완료", "pushed": "완료",
                 "no_device": "카톡으로", "ok": "완료", "redirect": "승인 화면으로"}


TAIL_RE = re.compile(r"\s*(?:→|—|-|·)\s*(?:대시보드|텔레그램|/[a-z]).*$")


IG_HOSTS = ("instagram.com", "www.instagram.com")
THREADS_HOSTS = ("threads.net", "www.threads.net", "threads.com", "www.threads.com")
BLOG_MATCH_SLACK = 15 * 60  # 딜봇이 published_deals.jsonl 에 적은 posted_at 과 채널 글 시각의 차이 (같은 발행)


def sns_url(url: Any, hosts: tuple[str, ...]) -> str | None:
    """딜봇이 남긴 인스타·스레드 글 주소 — https 이고 그 서비스 주소일 때만."""
    from urllib.parse import urlsplit

    u = link_target(url)
    return u if u is not None and (urlsplit(u).hostname or "").lower() in hosts else None


def blog_url_for(product_id: str, at: float | None, recent: list[dict[str, Any]] | None, tz: str) -> str | None:
    """이 딜이 들어간 핫딜 블로그 글 (기록 파일 recent.hotdeal[].deals 의 product_id 로만 — 제목으로는 잇지 않음).
    posted_at 이 있으면 채널 글 시각과 BLOG_MATCH_SLACK 안이어야 같은 발행 (같은 상품을 다시 올린 딜에 예전 글을 잇지 않게),
    없으면 블로그 글이 그 딜보다 뒤에 쓰였어야 함. url 은 parse_v2 가 네이버 블로그 주소만 남긴 것. 최근 글부터."""
    for post in recent or []:
        if not post.get("url"):
            continue
        for d in post.get("deals") or []:
            if d.get("product_id") != product_id:
                continue
            t = _iso_ts(d.get("posted_at"), tz)
            if t is not None:
                ok = at is None or abs(t - at) <= BLOG_MATCH_SLACK
            else:
                wrote = _iso_ts(post.get("at"), tz)
                ok = at is None or (wrote is not None and wrote >= at - BLOG_MATCH_SLACK)
            if ok:
                return str(post["url"])
    return None


def neighbors_line(n: dict[str, Any] | None) -> str | None:
    """블로그 탭 조용한 한 줄 (주인 10/18): '이웃 신청 오늘 18건까지 · 받은 신청 수락 3건 · 줄인 기간'. 칸이 없으면 None."""
    if not n:
        return None
    bits = []
    if n.get("cap_today") is not None:
        bits.append(f"이웃 신청 오늘 {n['cap_today']}건까지")
    elif n.get("cap_range"):
        lo, hi = n["cap_range"]
        bits.append(f"이웃 신청 오늘 {lo}~{hi}건" if lo != hi else f"이웃 신청 오늘 {lo}건까지")
    if n.get("accepted_today") is not None:
        bits.append(f"받은 신청 수락 {n['accepted_today']}건")
    if n.get("reduced"):
        bits.append("줄인 기간")
    return " · ".join(bits) or None


def notice_status(body: str, title: str = "") -> str:
    """알림 줄의 상태 한 줄: 본문 첫 줄에서 '→ 대시보드 …' 꼬리를 떼고 40자 (제목과 같으면 빈 글 — 그때는 시각)."""
    line = next((ln.strip() for ln in (body or "").splitlines() if ln.strip()), "")
    line = TAIL_RE.sub("", line).strip()
    if not line or line == (title or "").strip():
        return ""
    return truncate(line, 40)


def _target_label(target: str) -> str:
    if (q := iidmod.deal_of(target)) is not None:
        return f"#{q}"
    if (p := iidmod.post_of(target)) is not None:
        return f"{CHIPS[p[0]]} #{p[1]}"
    if target.startswith("blog-"):
        return BLOG_NAMES.get(target.split("-")[1], "")
    return {"threads": "스레드", "instagram": "인스타"}.get(target, "")
