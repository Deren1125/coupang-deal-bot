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

from dealbot.dashboard import rank
from dealbot.dashboard.actions import (
    BLOG_NAMES,
    MAY_PUBLISH,
    dismissible,
    needs_confirm,
    publish_blog,
)
from dealbot.dashboard.todo import ago, left, link_target
from dealbot.notify import iid as iidmod
from dealbot.utils.text import clean_name, truncate

log = logging.getLogger(__name__)

STATE_V = 1
BLOG_LABELS = {"finance": "Deren의 아카이브", "issue": "Deren's Pick", "hotdeal": "오늘의 핫딜"}
CHIPS = {"finance": "아카이브", "issue": "Pick", "hotdeal": "핫딜"}
SEV_ORDER = {"stop": 0, "hot": 1, "todo": 2, "info": 3}
UNSURE_DAYS = 7
NAME_MAX = 40
# 알림 종류 → 글의 할 일 (기록 파일 posts[] 가 아직 없을 때)
KIND_NEED = {"draft_ready": "publish", "ask": "answers", "pick_photos": "photos", "hot": "photos", "upload_fail": "retry",
             "owner_edit": "owner_edit", "share_confirm": "share", "channel_ask": "channel", "channel_review": "channel"}
NEED_ORDER = ("owner_edit", "answers", "photos", "retry", "publish", "share", "channel")  # 하나만 고를 때 앞의 것
NEED_STATUS = {"owner_edit": "직접 고친 글 · 건드리지 않음", "answers": "질문에 답해 주세요", "photos": "사진 필요",
               "retry": "업로드 실패", "publish": "발행 대기", "share": "직접 발행 · 채널·카톡 보낼까요",
               "channel": "채널에 아직 안 올림"}


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

    @staticmethod
    def _mark_publish(a: dict[str, Any], modes: dict[str, str]) -> None:
        """자동 발행 모드 블로그의 MAY_PUBLISH 일은 확인 창이 있다고 표시 (서버 ActionDesk 가 같은 기준으로 409)."""
        if a.get("action") in MAY_PUBLISH:
            # (blog.fix 처럼 원래 ★ 인 일은 임시저장 모드여도 ★ 그대로)
            a["confirm"] = bool(a.get("confirm")) or modes.get(publish_blog(a["action"], a.get("args") or {}) or "") != "draft"

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
            primary = act("deal.link", "링크 넣기", {"qid": it.id}, input="url", url=prod, placeholder="만든 링크 붙여넣기",
                          photo_optional=photo or None, accept="image/*" if photo else None)
            more = [act("deal.skip", "안 올리기", {"qid": it.id})]
            if (post := link_target(p.extra.get("post_url"))) is not None:
                more.append(open_link("원문 글 보기", post))
            detail: dict[str, Any] = {"how": (shop.manual_hint if shop and shop.manual_hint else "그 몰 앱에서 내 링크를 만들어 넣어 주세요"),
                                      "open": [{"label": "상품 열기", "url": prod}] if prod else []}
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
                detail["how"] = "쇼핑 커넥트 '상품 찾기'에서 아래 검색어로 찾아 링크를 만들어 주세요 · 상품 사진도 같이 넣으면 채널·블로그에 써요"
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
            base(it, "photo_need", f"사진 필요 · {self._hhmm(until)}까지",
                 act("deal.late_photo", "사진 올리기", {"qid": qid}, input="photos", accept="image/*", multiple=False), [],
                 {"how": "상품이 잘 보이는 사진 한 장 (블로그·인스타에 써요)"}, expires=until)
        return items

    def _channel_url(self) -> str | None:
        base = getattr(self.bot.notifier, "channel_post_base", None)
        return link_target(base) if base else None

    # ---------------------------------------------------------- 글 (기록 파일)
    def _post_actions(self, blog: str, pid: int, needs: list[str], post: dict[str, Any] | None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
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
        order = [n for n in NEED_ORDER if n in needs]
        primary = acts[order[0]] if order else goto("자세히", f"#/i/{iidmod.post_iid(blog, pid)}")
        more = [acts[n] for n in order[1:]]
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
        if first == "photos" and post and post.get("photo_request"):
            n = len(post["photo_request"].get("must") or [])
            status = f"사진 {n}장 필요" if n else status
        if first == "answers" and post and post.get("questions"):
            status = f"질문 {len(post['questions'])}개"
        if first == "publish" and post and post.get("popular"):
            status = "발행 대기 · 인기 예상"
        # 끝나는 시간 (질문 마감·사진 부탁 마감) → 줄에 '· 18:00까지' + 정렬·경고색 (딜 줄과 같은 기준으로 앞에)
        pr = (post or {}).get("photo_request") if isinstance((post or {}).get("photo_request"), dict) else {}
        exp = (_iso_ts((post or {}).get("deadline"), self.tz) or _iso_ts(pr.get("deadline"), self.tz)) if needs else None
        if exp is not None and first in ("answers", "photos", "publish"):
            when = datetime.fromtimestamp(exp, ZoneInfo(self.tz))
            same_day = when.date() == datetime.now(ZoneInfo(self.tz)).date()
            status = f"{status} · {when:%H:%M}까지" if same_day else f"{status} · {when.month}/{when.day} {when:%H:%M}까지"
        # 질문은 자르지 않는다 — 답하려면 전체 문장이 보여야 함 (주인 10/18: '질문이 잘려')
        questions = list((post or {}).get("questions") or [])
        slots = [dict(s, thumb=f"/dashboard/thumb/{s['thumb']}" if s.get("thumb") else None) for s in (post or {}).get("slots") or []]
        iid = iidmod.post_iid(blog, pid)
        return {"iid": iid, "src": "blog", "blog": blog, "kind": first or "waiting", "sev": "todo", "urgent": bool(needs),
                "title": (post or {}).get("title") or title or f"{CHIPS[blog]} #{pid}", "chip": CHIPS[blog], "status": status,
                "since": _iso_ts((post or {}).get("saved_at"), self.tz), "expires_at": exp, "primary": primary, "more": more,
                "detail": {"needs": needs, "photo_request": (post or {}).get("photo_request"), "slots": slots,
                           "questions": questions, "deadline": (post or {}).get("deadline"),
                           "owner_edited": bool((post or {}).get("owner_edited")),
                           "open": [{"label": "네이버에서 보기", "url": post["naver_url"]}] if post and post.get("naver_url") else []},
                "_hot": rank.post_hot(post)}

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
                if iid not in out and p.get("needs") and p.get("status") != "published":
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
            if iid in out or iid.startswith("deal-"):
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
            primary = open_link(t.link and ("네이버에서 보기" if "naver.com" in t.link else "열기") or "열기", t.link) if t.link \
                else goto("블로그 보기", f"#/blog/{blog}") if blog else goto("자세히", "#/connect")
            out[iid] = {"iid": iid, "src": "blog", "blog": blog, "kind": t.kind, "sev": sev, "urgent": t.urgent, "title": t.short,
                        "status": truncate(t.what, 60) if t.what and t.what != t.short else (ago(t.since, now_dt) if t.since else ""),
                        "since": _ts(t.since), "expires_at": None, "primary": primary,
                        "more": [act("todo.dismiss", "다 했어요", {"iid": iid, "key": t.key})]
                        if dismissible(iid, t.key, dealbot_row=False) else [], "detail": {"lines": []}}
        items = list(out.values())
        supported = self._supported(self._ba_status())
        modes = self._modes(v2)
        for it in items:
            it["more"] = self._filter(it["more"], supported)
            for a in (it["primary"], *it["more"]):
                self._mark_publish(a, modes)
            if supported is not None and it["primary"]["action"].startswith("blog.") and it["primary"]["action"][5:] not in supported:
                it["primary"] = dict(it["primary"], disabled=True, note="블로그 프로그램 업데이트를 기다리는 중")
            it.setdefault("chip", CHIPS.get(it.get("blog") or "", "딜" if it["src"] == "deal" else ""))
        return rank.order(items, now)  # 중요한·핫한 순 (rank.py)

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
    def _deal_row(self, it: Any) -> dict[str, Any]:
        p = it.deal.product
        shop = self._shop(p.shop)
        post = self.bot.db.latest_post(p.product_id) or {}
        base = getattr(self.bot.notifier, "channel_post_base", None)
        url = f"{base}/{post['message_id']}" if base and post.get("message_id") and not post.get("dry_run") else None
        kind = "owner" if p.extra.get("owner_photo") else ("verified" if p.extra.get("photo_ok") or p.extra.get("photo_kind") == "verified" else "none")
        until = self.bot.late_photo.until(it.id) if hasattr(self.bot, "late_photo") else None
        row = {"qid": it.id, "title": _name(p.name), "shop": shop.name if shop else p.shop,
               "price": f"{p.price:,}원" if p.has_price else "", "at": _iso_ts(post.get("posted_at"), self.tz) or _ts(it.created_at),
               "url": url, "photo": kind}
        if until is not None and until > self._now() and self.bot.late_photo_waiting(it.id):
            row["photo_wait"] = {"until": _ts(until), "iid": iidmod.deal_iid(it.id)}
        return row

    def deal(self) -> dict[str, Any]:
        bot = self.bot
        now = self._now()
        tz = ZoneInfo(self.tz)
        start = now.astimezone(tz).replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC)
        today = bot.db.published_items_between(start, now + timedelta(minutes=1), include_dry_run=bot.state.dry_run)
        recent = sorted(bot.db.published_items_between(now - timedelta(hours=24), now + timedelta(minutes=1),
                                                       include_dry_run=bot.state.dry_run), key=lambda x: x.id, reverse=True)[:10]
        return {"status": {"paused": bot.state.paused, "ok": not bot.state.paused, "today": len(today), "dry_run": bot.state.dry_run},
                "today": [self._deal_row(it) for it in recent], "queue": bot.queue_view(10), "collectors": bot.collector_names(),
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
        out = {}
        for blog in ("finance", "issue", "hotdeal"):
            st = st_blogs.get(blog) or {}
            waiting = [it for it in todo if it.get("blog") == blog and it["iid"].startswith("post-")]
            have = {it["iid"] for it in waiting}
            for p in v2.get("posts", {}).get(blog, []):
                iid = iidmod.post_iid(blog, p["pid"])
                if iid not in have and p.get("status") != "published":
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
            mode = st.get("mode") or "draft"
            acts.append(act("blog.mode", "발행 모드로" if mode == "draft" else "초안 모드로",
                            {"blog": blog, "mode": "publish" if mode == "draft" else "draft"}))
            acts.append(act("blog.resume", "다시 시작", {"blog": blog}) if st.get("paused") else act("blog.pause", "멈추기", {"blog": blog}))
            acts.append(act("blog.drafts_cleanup", "임시저장 정리", {"blog": blog}))
            if blog == "finance":
                acts.append(act("blog.ledger", "정리본 올리기", input="file", accept=".md,.txt,text/markdown,text/plain"))
            acts.append(act("blog.info", "크리에이터 키워드", {"what": "creator"}))
            scan = v2.get("scan") if blog == "finance" else None
            out[blog] = {"label": BLOG_LABELS[blog], "chip": CHIPS[blog], "mode": mode, "paused": bool(st.get("paused")),
                         "today": st.get("today") or 0, "login": st.get("login") or "unknown",
                         "waiting": waiting, "recent": [dict(r, at=_iso_ts(r.get("at"), self.tz)) for r in v2.get("recent", {}).get(blog, [])],
                         "scan": ({"sid": scan["sid"], "at": _iso_ts(scan.get("at"), self.tz),
                                   "items": [{"n": x["n"], "title": x["title"], "why": "인기 예상" if x.get("recommend") else "",
                                              "action": act("blog.pick", "이걸로 쓰기", {"sid": scan["sid"], "n": x["n"]})}
                                             for x in scan["items"]]} if scan else None),
                         "actions": self._filter(acts, supported)}
            for a in out[blog]["actions"] + [x["action"] for x in (out[blog]["scan"] or {}).get("items", [])]:
                self._mark_publish(a, self._modes(v2))
        return out

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
        body: dict[str, Any] = {
            "v": STATE_V, "at": time.time(),
            "me": {"csrf": csrf, "push_key": router.public_key if router is not None else None,
                   "public_url": bot.settings.dashboard.public_url, "logout_t": logout_t},
            "banner": banner,
            "counts": {"todo": len(todo)},
            "todo": todo,
            "deal": self.deal(),
            "blogs": self.blogs(export, todo),
            "sns": self.sns(),
            "connect": connect,
            "pending": pending,
            "digest": {"latest": router.store.latest_digest() if router is not None else None},
            "blogauto": {"bridge": st.bridge, "fresh": st.fresh()},
        }
        return body

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
    "todo.dismiss": "다 했어요", "stats.refresh": "다시 읽기", "stats.input": "직접 넣기", "upload": "사진 올림",
    "blog.publish": "발행", "blog.drop": "버리기", "blog.retry": "다시 올리기", "blog.fix": "말로 고치기", "blog.fixpic": "사진도 바꾸기",
    "blog.rewrite": "다시 쓰기", "blog.restyle": "스타일 다시", "blog.photos": "사진 올리기", "blog.photo_clear": "자리 비우기",
    "blog.answer": "답하기", "blog.noanswer": "답 없이 진행", "blog.oedit_keep": "그대로 두기", "blog.oedit_redo": "봇 글로 덮어쓰기",
    "blog.share": "보내기", "blog.noshare": "보내지 않기", "blog.channel": "채널에 올리기", "blog.scan": "글감 찾기",
    "blog.pick": "글감으로 쓰기", "blog.write": "새 글 쓰기", "blog.issue": "키워드로 쓰기", "blog.hotdeal_now": "한 편 쓰기",
    "blog.mode": "모드 바꾸기", "blog.pause": "블로그 멈춤", "blog.resume": "블로그 다시 시작", "blog.drafts_cleanup": "임시저장 정리",
    "blog.ledger": "정리본 바꾸기", "blog.login_check": "로그인 확인", "blog.info": "보기", "blog.kakao_auth_url": "카톡 다시 연결",
    "blog.kakao_connect": "카톡 연결", "blog.kakao_test": "테스트 카톡", "blog.dismiss": "다 했어요",
    "connect": "다시 연결 시작",
}
RESULT_LABELS = {"queued": "요청함", "done": "완료", "linked": "완료", "linked_twin": "완료", "approved": "완료", "skipped": "완료",
                 "posted": "완료", "paused": "완료", "resumed": "완료", "run": "완료", "connected": "완료", "pushed": "완료",
                 "no_device": "카톡으로", "ok": "완료", "redirect": "승인 화면으로"}


TAIL_RE = re.compile(r"\s*(?:→|—|-|·)\s*(?:대시보드|텔레그램|/[a-z]).*$")


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
