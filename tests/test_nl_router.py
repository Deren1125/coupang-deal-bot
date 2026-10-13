"""관리자 챗 말 명령 (주인 요청 10/11): 말 → 기존 명령. 진짜 AI·텔레그램은 부르지 않는다 (가짜 AI·가짜 메시지)."""

from __future__ import annotations

import asyncio
import itertools
import json
import logging
import re
import time
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from dealbot.config import NLRouterConfig, Settings
from dealbot.llm_json import JsonAsker, LLMError
from dealbot.manual import parse_manual_post
from dealbot.models import Deal, DealVerdict, Product
from dealbot.monitoring import admin as admin_mod
from dealbot.monitoring import nl as nlmod
from dealbot.monitoring.admin import AdminNotifier, register_admin_handlers
from dealbot.monitoring.nl import NLRouter
from dealbot.storage.db import Database
from dealbot.utils.timeutil import utcnow

ADMIN = 42
BOT_ID = 999
OWNER = 7
_ids = itertools.count(5000)


# ------------------------------------------------------------------ 가짜들
class FakeLLM:
    """대본대로 답하는 가짜 AI. 부른 횟수·넘긴 글을 기록한다."""

    def __init__(self, *answers: dict[str, Any] | Exception) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, str]] = []
        self.last_backend = "api"

    async def ask(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((system, user))
        a = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(a, Exception):
            raise a
        return a


def ans(intent: str, post: int | None = None, args: str = "", *, conf: float = 0.95, **kw: Any) -> dict[str, Any]:
    return {"intent": intent, "post": post, "args": args, "confidence": conf, "needs_confirm": False,
            "echo": "", "question": "", "candidates": [], "also": [], "blog_text": ""} | kw


class FakeController:
    def __init__(self) -> None:
        self.calls: list[tuple[Any, ...]] = []
        self.state = SimpleNamespace(paused=False)

    def pause(self) -> None:
        self.calls.append(("pause",))

    def resume(self) -> None:
        self.calls.append(("resume",))

    async def attach_link(self, q: int, url: str) -> str:
        self.calls.append(("attach_link", q, url))
        return f"✅ 상품 링크 완료 #{q}"

    def skip_item(self, q: int) -> str:
        self.calls.append(("skip_item", q))
        return f"⏭ #{q} 건너뜀"

    async def mark_posted(self, q: int, link: str = "") -> str:
        self.calls.append(("mark_posted", q, link))
        return f"✅ #{q} 기록"

    def approve_item(self, q: int, text: str | None = None) -> str:
        self.calls.append(("approve_item", q, text))
        return f"✅ #{q} 올릴 차례"

    async def refresh_pending_notice(self) -> None:
        self.calls.append(("refresh",))

    async def submit_manual(self, text: str) -> str:
        self.calls.append(("submit_manual", text))
        return "📣 올림"

    def relay_to_blog(self, update: dict) -> None:
        self.calls.append(("relay_to_blog", update))

    def request_run(self, name: str | None) -> str:
        self.calls.append(("request_run", name))
        return "🔄 곧 확인"

    async def screenshot(self, url: str) -> tuple[bytes | None, str]:
        self.calls.append(("screenshot", url))
        return None, "📸 찍음"

    def named(self, name: str) -> list[tuple[Any, ...]]:
        return [c for c in self.calls if c[0] == name]


class FakeReporter:
    def queue_text(self) -> str:
        return "🗂 <b>올릴 차례를 기다리는 글</b>"

    def status_text(self) -> str:
        return "STATUS"


class FakeApp:
    def __init__(self) -> None:
        self.handlers: list[tuple[Any, int]] = []
        self.error_handlers: list[Any] = []

    def add_handler(self, handler: Any, group: int = 0) -> None:
        self.handlers.append((handler, group))

    def add_error_handler(self, handler: Any) -> None:
        self.error_handlers.append(handler)

    def cb(self, name: str) -> Any:
        return next(h.callback for h, _ in self.handlers if getattr(h.callback, "__name__", "") == name)

    def names(self) -> list[str]:
        return [getattr(h.callback, "__name__", "") for h, _ in self.handlers]


class Chat(SimpleNamespace):
    pass


def user(uid: int, *, is_bot: bool) -> SimpleNamespace:
    return SimpleNamespace(id=uid, is_bot=is_bot, to_dict=lambda: {"id": uid, "is_bot": is_bot})


class Msg:
    """가짜 텔레그램 메시지. 답장(reply_text)은 env.out 에 쌓인다."""

    def __init__(self, env: Env, text: str | None = None, *, reply_to: Msg | None = None, caption: str | None = None,
                 photo: bool = False, document: dict[str, str] | None = None, group: str | None = None,
                 bot: bool = False, date: Any = None, mid: int | None = None) -> None:
        self.env = env
        self.message_id = mid or next(_ids)
        self.text, self.caption = text, caption
        self.reply_to_message = reply_to
        self.photo = [SimpleNamespace(file_id=f"F{self.message_id}")] if photo else []
        self.document = SimpleNamespace(**document) if document else None
        self.media_group_id = group
        self.from_user = user(BOT_ID, is_bot=True) if bot else user(OWNER, is_bot=False)
        self.chat = Chat(id=ADMIN, username=None)
        self.date = date or utcnow()

    async def reply_text(self, text: str = "", **kw: Any) -> SimpleNamespace:
        sent = Msg(self.env, text, bot=True)
        self.env.out.append((text, kw.get("reply_markup")))
        return sent

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"message_id": self.message_id, "date": int(self.date.timestamp()), "chat": {"id": ADMIN},
                             "from": self.from_user.to_dict()}
        if self.text is not None:
            d["text"] = self.text
        if self.caption is not None:
            d["caption"] = self.caption
        if self.photo:
            d["photo"] = [{"file_id": p.file_id} for p in self.photo]
        if self.media_group_id:
            d["media_group_id"] = self.media_group_id
        if self.reply_to_message is not None:
            d["reply_to_message"] = self.reply_to_message.to_dict()
        return d


class Env:
    def __init__(self, settings: Settings, db: Database, llm: FakeLLM | None, *, enabled: bool = True) -> None:
        self.out: list[tuple[str, Any]] = []
        self.relayed: list[dict[str, Any]] = []
        self.db = db
        registry = settings.shop_registry()
        self.nl = NLRouter(
            NLRouterConfig(enabled=enabled), db, llm, relay=self.relayed.append,
            parse_post=lambda t: parse_manual_post(t, registry),
            shop_of=lambda url: (s.key if (s := registry.by_url(url)) else None),
            collectors=lambda: ["ppomppu"],
        )
        self.nl.bot_id = BOT_ID
        self.llm = llm
        self.ctrl = FakeController()
        self.app = FakeApp()
        register_admin_handlers(self.app, admin_chat_id=ADMIN, reporter=FakeReporter(), controller=self.ctrl, nl=self.nl)  # type: ignore[arg-type]

    # 보내기
    def msg(self, text: str | None = None, **kw: Any) -> Msg:
        return Msg(self, text, **kw)

    def bot_msg(self, text: str, *, record: bool = True, date: Any = None, kind: str = "", q: int | None = None) -> Msg:
        m = Msg(self, text, bot=True, date=date)
        if record:
            self.nl.record(m.message_id, text, kind, q)
        return m

    async def text(self, text: str, **kw: Any) -> Msg:
        m = self.msg(text, **kw)
        await self.app.cb("on_text")(update(m), SimpleNamespace(args=[], application=None))
        return m

    async def media(self, **kw: Any) -> Msg:
        m = self.msg(**kw)
        await self.app.cb("on_media")(update(m), SimpleNamespace(args=[], application=None))
        return m

    async def command(self, text: str) -> None:
        await self.app.cb("on_other_command")(update(self.msg(text)), SimpleNamespace(args=[], application=None))

    async def press(self, data: str, on: Msg | None = None) -> None:
        q = SimpleNamespace(data=data, answered=False)

        async def answer(*_a: Any, **_k: Any) -> None:
            q.answered = True

        async def edit_markup(**_k: Any) -> None:
            return None

        q.answer, q.edit_message_reply_markup = answer, edit_markup
        target = on or self.msg("버튼 메시지", bot=True)
        u = SimpleNamespace(effective_message=target, effective_chat=target.chat, callback_query=q,
                            to_dict=lambda: {"callback_query": {"data": data, "message": target.to_dict()}})
        if data.startswith("dnl:"):
            await self.app.cb("on_nl_callback")(u, SimpleNamespace(args=[], application=None))
        else:
            await self.app.cb("relay")(u, SimpleNamespace())

    # 보기
    @property
    def texts(self) -> list[str]:
        return [t for t, _ in self.out]

    def buttons(self, i: int = -1) -> list[tuple[str, str]]:
        kb = self.out[i][1]
        return [(b.text, b.callback_data) for row in kb.inline_keyboard for b in row] if kb else []

    def press_data(self, label_part: str, i: int = -1) -> str:
        return next(d for t, d in self.buttons(i) if label_part in t)


def update(m: Msg) -> SimpleNamespace:
    return SimpleNamespace(effective_message=m, effective_chat=m.chat, callback_query=None, to_dict=lambda: {"message": m.to_dict()})


def add_item(db: Database, name: str, *, shop: str = "toss", status: str = "awaiting_link", price: int = 14890,
             url: str = "https://toss.im/_m/abc", kind: str = "hotdeal") -> int:
    p = Product(source="fake", product_id=f"{shop}:{name}", shop=shop, name=name, price=price, url=url, deal_kind=kind)
    assert db.enqueue(Deal(product=p, verdict=DealVerdict(is_deal=True)), score=1.0)
    qid = max(it.id for it in db.pending_items(100))
    db.update_queue_item(qid, status=status)
    return qid


@pytest.fixture
def env(settings: Settings, db: Database) -> Env:
    return Env(settings, db, FakeLLM(ans("none")))


def with_llm(env: Env, *answers: dict[str, Any] | Exception) -> FakeLLM:
    llm = FakeLLM(*answers)
    env.nl.asker = llm
    env.llm = llm
    return llm


# ------------------------------------------------------------------ 정해진 흐름 (AI 안 부름)
async def test_batch_lines_are_direct(env: Env) -> None:
    llm = with_llm(env, ans("/status"))
    await env.text("675 https://toss.im/_m/x\n677 ok")
    assert env.ctrl.named("attach_link") == [("attach_link", 675, "https://toss.im/_m/x")]
    assert env.ctrl.named("approve_item") == [("approve_item", 677, None)]
    assert llm.calls == []


async def test_url_reply_to_link_request_attaches_directly(env: Env) -> None:
    llm = with_llm(env, ans("/status"))
    req = env.bot_msg("🔗 <b>내 링크가 필요합니다 #675</b> [토스쇼핑]", kind="link", q=675)
    await env.text("https://toss.im/_m/abc", reply_to=req)
    assert env.ctrl.named("attach_link") == [("attach_link", 675, "https://toss.im/_m/abc")]
    assert ("refresh",) in env.ctrl.calls and llm.calls == []


async def test_url_reply_to_blog_auto_message_is_relayed_not_attached(env: Env) -> None:
    llm = with_llm(env, ans("/status"))
    blog = env.bot_msg("✅ [오늘의 핫딜] #12 임시저장 완료", record=False)  # Blog-Auto 가 같은 봇으로 보낸 것 (기록 없음)
    await env.text("https://toss.im/_m/abc 이걸로 바꿔", reply_to=blog)
    assert env.ctrl.named("attach_link") == [] and llm.calls == []
    line = env.relayed[-1]
    assert line["nl"]["why"] == "reply_blog" and line["nl"]["v"] == 1
    assert line["message"]["reply_to_message"]["text"].startswith("✅ [오늘의 핫딜] #12")
    assert abs(line["nl"]["relayed_at"] - time.time()) < 2


async def test_legacy_replies_use_text_hints(env: Env) -> None:
    with_llm(env, ans("/status"))
    old = utcnow() - timedelta(days=3)
    env.db.kv_set("admin_msgs_since", (utcnow() - timedelta(days=1)).isoformat())
    blog = env.bot_msg("📝 [오늘의 핫딜] #12 발행 대기", record=False, date=old)
    await env.text("https://x.example/a", reply_to=blog)
    assert env.relayed and env.relayed[-1]["nl"]["why"] == "reply_blog"
    deal = env.bot_msg("🔗 내 링크가 필요합니다 #675", record=False, date=old)
    await env.text("https://toss.im/_m/abc", reply_to=deal)
    assert env.ctrl.named("attach_link") == [("attach_link", 675, "https://toss.im/_m/abc")]
    naver = env.bot_msg("🔗 <b>내 링크가 필요합니다 #676</b> [네이버 스마트스토어]", record=False, date=old)
    await env.text("https://naver.me/abc", reply_to=naver)  # 몰 이름 '네이버' 가 있어도 딜봇 링크 요청
    assert env.ctrl.named("attach_link")[-1] == ("attach_link", 676, "https://naver.me/abc")


async def test_url_reply_to_edited_pinned_notice_uses_current_number(env: Env) -> None:
    llm = with_llm(env, ans("/status"))
    pin = env.bot_msg("📌 🔗 내가 링크를 만들어 줘야 하는 글 (1건)\n• #675 애슐리")  # 보낼 때는 #675 하나
    pin.text = "📌 🔗 내가 링크를 만들어 줘야 하는 글 (1건)\n• #680 비비고"  # 같은 메시지를 고쳐 씀 (675 는 끝남)
    await env.text("https://toss.im/_m/abc", reply_to=pin)
    assert env.ctrl.named("attach_link") == [("attach_link", 680, "https://toss.im/_m/abc")] and llm.calls == []


async def test_old_unrecorded_deal_message_is_not_taken_for_blog(env: Env) -> None:
    """기록은 30일 뒤 지워진다 — 한 달 넘게 고쳐 쓰는 고정 공지에 답해도 Blog-Auto 로 넘기지 않음."""
    with_llm(env, ans("/status"))
    env.db.kv_set("admin_msgs_since", (utcnow() - timedelta(days=90)).isoformat())
    pin = env.bot_msg("📌 🔗 내가 링크를 만들어 줘야 하는 글 (1건)\n• #680 비비고 [네이버]", record=False,
                      date=utcnow() - timedelta(days=nlmod.REGISTRY_DAYS + 5))
    await env.text("https://toss.im/_m/abc", reply_to=pin)
    assert env.relayed == [] and env.ctrl.named("attach_link") == [("attach_link", 680, "https://toss.im/_m/abc")]
    old = env.bot_msg("✅ #681 처리했습니다", record=False, date=utcnow() - timedelta(days=nlmod.REGISTRY_DAYS + 5))
    await env.text("https://toss.im/_m/def", reply_to=old)  # 글 짐작에 걸리는 말이 없어도 예전처럼 딜 메시지로
    assert env.relayed == [] and env.ctrl.named("attach_link")[-1] == ("attach_link", 681, "https://toss.im/_m/def")


async def test_typed_number_of_another_action_does_not_move_the_reply_deal(env: Env) -> None:
    a = add_item(env.db, "애슐리 핫도그")
    b = add_item(env.db, "비비고 만두", status="awaiting_approval")
    req = env.bot_msg(f"🔗 <b>내 링크가 필요합니다 #{a}</b>", kind="link", q=a)
    with_llm(env, ans("/skip", a, also=[{"intent": "/ok", "post": b, "args": ""}]))
    await env.text(f"이건 패스, {b}번은 올려", reply_to=req)
    assert f"/skip {a}\n/ok {b}" in env.texts[-1]
    await env.press(env.press_data("실행"))
    assert env.ctrl.named("skip_item") == [("skip_item", a)] and env.ctrl.named("approve_item") == [("approve_item", b, None)]


async def test_slow_ai_is_cut_off(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    class Slow:
        last_backend = "api"

        async def ask(self, *_a: Any) -> dict[str, Any]:
            await asyncio.sleep(5)
            return ans("/status")

    monkeypatch.setattr(nlmod, "ASK_GRACE", 0)
    env.nl.cfg.timeout_seconds = 0.05
    env.nl.asker = Slow()
    t0 = time.monotonic()
    await env.text("상태 어때")
    assert time.monotonic() - t0 < 2 and env.texts[-1].startswith("🤖 지금 말을 알아듣는 AI가 응답하지 않아요")


async def test_info_review_long_body_short_ok_and_short_edit(env: Env) -> None:
    review = env.bot_msg("📝 <b>정보 글 확인 #31</b> — 이대로 올릴까요?", kind="review_info", q=31)
    body = "네이버페이 5천원 적립\n· 기간 10/11~10/13\n· 조건 첫 결제"
    with_llm(env, ans("/status"))
    await env.text(body, reply_to=review)
    assert env.ctrl.named("approve_item") == [("approve_item", 31, body)]

    env.ctrl.calls.clear()
    item = add_item(env.db, "정보 글", status="awaiting_approval", kind="info")
    review2 = env.bot_msg(f"📝 <b>정보 글 확인 #{item}</b>", kind="review_info", q=item)
    with_llm(env, ans("/ok", item))
    await env.text("좋아 올려", reply_to=review2)
    assert env.ctrl.named("approve_item") == []  # 짧은 말은 본문이 아님 → 확인 버튼
    assert env.texts[-1].startswith("이렇게 할까요?") and f"/ok {item}" in env.texts[-1]
    await env.press(env.press_data("실행"))
    assert env.ctrl.named("approve_item") == [("approve_item", item, None)]

    env.ctrl.calls.clear()
    with_llm(env, ans("ask", question=nlmod.INFO_BODY_ASK))
    await env.text("제목 짧게", reply_to=review2)
    assert env.texts[-1] == nlmod.INFO_BODY_ASK and env.ctrl.named("approve_item") == []


async def test_reply_to_blog_auto_review_mark_is_relayed(env: Env) -> None:
    with_llm(env, ans("/ok", 12))
    blog = env.bot_msg("📝 [오늘의 핫딜] #12 발행 대기", record=False)
    await env.text("좋아 이거 발행해", reply_to=blog)
    assert env.ctrl.named("approve_item") == [] and env.relayed[-1]["nl"]["why"] == "reply_blog"


# ------------------------------------------------------------------ 해석 → 실행·확인·되묻기
async def test_url_with_words_confirms_post_then_submits_once(env: Env) -> None:
    text = "이거 올려줘\n상품: 애슐리 핫도그 8개\n가격: 14,890원\nhttps://toss.im/_m/abc"
    with_llm(env, ans("/post"))
    await env.text(text)
    assert env.ctrl.named("submit_manual") == []
    confirm = env.texts[-1]
    assert confirm.startswith("채널에 이 딜을 올릴까요?") and "애슐리 핫도그 8개" in confirm and "14,890원" in confirm and "toss.im" in confirm
    data = env.press_data("올리기")
    await env.press(data)
    await env.press(data)  # 두 번 눌러도 한 번
    assert env.ctrl.named("submit_manual") == [("submit_manual", text)]
    assert env.texts[-1] == nlmod.HANDLED

    with_llm(env, ans("/post"))
    await env.text(text)
    await env.press(env.press_data("취소"))
    assert len(env.ctrl.named("submit_manual")) == 1 and env.texts[-1] == nlmod.CANCELLED


async def test_url_with_words_not_posted_when_ai_down(env: Env) -> None:
    with_llm(env, LLMError("시간 초과"))
    await env.text("상품: 애슐리 핫도그\n가격: 14,890원\nhttps://toss.im/_m/abc 올려")
    assert env.ctrl.named("submit_manual") == []
    assert env.texts[-1].startswith("🤖 지금 말을 알아듣는 AI가 응답하지 않아요") and "/post" in env.texts[-1]


async def test_skip_by_words_confirms_then_skips(env: Env) -> None:
    q = add_item(env.db, "애슐리 핫도그")
    with_llm(env, ans("/skip", q))
    await env.text(f"{q}번 올리지 마")
    assert env.ctrl.named("skip_item") == [] and f"/skip {q}" in env.texts[-1]
    await env.press(env.press_data("실행"))
    assert env.ctrl.named("skip_item") == [("skip_item", q)] and ("refresh",) in env.ctrl.calls


async def test_read_command_echoes_then_runs(env: Env) -> None:
    with_llm(env, ans("/queue"))
    await env.text("대기열 보여줘")
    assert env.texts[0].startswith("이렇게 이해했어요: 올릴 차례를 기다리는 딜 보기 (/queue)")
    assert env.texts[1].startswith("🗂")


async def test_pause_ambiguity_asks_deal_or_blog(env: Env) -> None:
    with_llm(env, ans("/pause"))
    await env.text("멈춰")
    assert env.texts[-1] == nlmod.PAUSE_ASK
    labels = [t for t, _ in env.buttons()]
    assert labels == ["딜봇 (게시판 확인·채널 올리기)", "핫딜 블로그 자동 작업", "취소"]
    await env.press(env.press_data("딜봇"))
    assert env.ctrl.named("pause") == [("pause",)]

    await env.text("멈춰")
    await env.press(env.press_data("핫딜 블로그"))
    alias = env.relayed[-1]["message"]
    assert alias["text"] == "/blogpause" and abs(alias["date"] - time.time()) < 2 and "nl" not in env.relayed[-1]
    assert len(env.ctrl.named("pause")) == 1

    with_llm(env, ans("/pause"))
    await env.text("딜봇 멈춰")
    assert env.texts[-1].startswith("이렇게 할까요?") and "/pause" in env.texts[-1]


async def test_blog_words_are_relayed_with_original_text(env: Env) -> None:
    with_llm(env, ans("blog"))
    await env.text("블로그 상태 어때?")
    line = env.relayed[-1]
    assert line["message"]["text"] == "블로그 상태 어때?" and line["nl"]["why"] == "router"
    assert env.ctrl.calls == [] and env.texts == []


async def test_mixed_message_relays_blog_part_and_confirms_deal_part(env: Env) -> None:
    q = add_item(env.db, "애슐리 핫도그")
    with_llm(env, ans("/skip", q, blog_text="블로그 12번 발행해"))
    await env.text(f"{q}번 빼고 블로그 12번 발행해")
    assert env.relayed[-1]["message"]["text"] == "블로그 12번 발행해" and env.relayed[-1]["nl"]["partial"]
    assert f"/skip {q}" in env.texts[-1]


async def test_queue_id_outside_candidates_asks(env: Env) -> None:
    add_item(env.db, "애슐리 핫도그")
    with_llm(env, ans("/skip", 99999))
    await env.text("그거 올리지 마")
    assert env.texts[-1] == nlmod.WHICH and env.ctrl.named("skip_item") == []


async def test_standalone_link_matches_single_waiting_shop(env: Env) -> None:
    q = add_item(env.db, "애슐리 핫도그")
    add_item(env.db, "네이버 상품", shop="naver", url="https://smartstore.naver.com/a/products/1")
    with_llm(env, ans("/link", None, "https://toss.im/_m/xyz"))
    await env.text("토스 링크 이거야 https://toss.im/_m/xyz")
    assert env.texts[-1].startswith("이렇게 할까요?") and f"/link {q} https://toss.im/_m/xyz" in env.texts[-1]
    await env.press(env.press_data("실행"))
    assert env.ctrl.named("attach_link") == [("attach_link", q, "https://toss.im/_m/xyz")]


async def test_standalone_link_with_two_waiting_gives_candidates(env: Env) -> None:
    a = add_item(env.db, "애슐리 핫도그")
    b = add_item(env.db, "비비고 만두")
    with_llm(env, ans("/link", None, "https://toss.im/_m/xyz"))
    await env.text("토스 링크 https://toss.im/_m/xyz")
    assert env.texts[-1] == nlmod.WHICH
    labels = [t for t, _ in env.buttons()]
    assert labels[0].startswith(f"#{a} ") and labels[1].startswith(f"#{b} ") and labels[-1] == "취소"
    await env.press(env.press_data(f"#{b} "))  # 버튼이 곧 확인 (다시 묻지 않음)
    assert env.ctrl.named("attach_link") == [("attach_link", b, "https://toss.im/_m/xyz")]


async def test_url_must_be_in_message(env: Env) -> None:
    q = add_item(env.db, "애슐리 핫도그")
    with_llm(env, ans("/link", q, "https://made-up.example/x"))
    await env.text(f"{q}번에 링크 붙여")
    assert env.texts[-1] == nlmod.NEED_URL and env.ctrl.calls == []


async def test_low_confidence_asks_with_the_reading_as_button(env: Env) -> None:
    q = add_item(env.db, "애슐리 핫도그")
    with_llm(env, ans("/skip", q, conf=0.5, question="이 딜을 빼라는 뜻인가요?"))
    await env.text(f"{q} 별로네")
    assert env.texts[-1] == "이 딜을 빼라는 뜻인가요?" and env.ctrl.named("skip_item") == []
    await env.press(env.press_data("올리지 않기"))
    assert env.ctrl.named("skip_item") == [("skip_item", q)]


async def test_needs_confirm_raises_risk_of_read_command(env: Env) -> None:
    with_llm(env, ans("/run", needs_confirm=True))
    await env.text("게시판 지금 돌려")
    assert env.texts[-1].startswith("이렇게 할까요?") and env.ctrl.named("request_run") == []


# ------------------------------------------------------------------ 버튼
async def test_dnl_callbacks_handled_locally_others_relayed(env: Env) -> None:
    await env.press("pub:12")
    await env.press("nlok:abc")
    relayed = [c[1]["callback_query"]["data"] for c in env.ctrl.named("relay_to_blog")]
    assert relayed == ["pub:12", "nlok:abc"]
    env.ctrl.calls.clear()
    relay = env.app.cb("relay")
    await relay(SimpleNamespace(effective_chat=Chat(id=ADMIN, username=None), callback_query=SimpleNamespace(data="dnl:ok:x"),
                                to_dict=lambda: {}), None)
    assert env.ctrl.named("relay_to_blog") == []
    # dnl 처리기는 relay(전부 받는 것)보다 먼저 등록
    names = env.app.names()
    catch_all = len(names) - 1 - names[::-1].index("relay")
    assert names.index("on_nl_callback") < catch_all


async def test_confirm_expires_after_30_minutes(env: Env) -> None:
    q = add_item(env.db, "애슐리 핫도그")
    with_llm(env, ans("/skip", q))
    await env.text(f"{q}번 올리지 마")
    data = env.press_data("실행")
    token = data.split(":")[2]
    raw = json.loads(env.db.kv_get(f"dnl:{token}") or "{}")
    env.db.kv_set(f"dnl:{token}", json.dumps(raw | {"at": time.time() - 31 * 60}))
    await env.press(data)
    assert env.texts[-1] == nlmod.EXPIRED and env.ctrl.named("skip_item") == []


async def test_drop_stale_keeps_old_buttons_but_drops_old_text(env: Env) -> None:
    from telegram.ext import ApplicationHandlerStop

    drop = next(h.callback for h, g in env.app.handlers if g == -1)
    old = utcnow() - timedelta(minutes=10)
    btn = SimpleNamespace(callback_query=SimpleNamespace(data="pub:1"), effective_message=SimpleNamespace(date=old, text=""))
    await drop(btn, None)  # 버튼은 지나가야 함
    txt = SimpleNamespace(callback_query=None, effective_message=SimpleNamespace(date=old, text="/run"))
    with pytest.raises(ApplicationHandlerStop):
        await drop(txt, None)


def _stale_token_msg(text: str, *, chat_id: int = ADMIN, deletable: bool = True) -> tuple[SimpleNamespace, list[str], list[str]]:
    """봇이 멈춰 있던 동안 온 (30분 전) 명령. 지운 기록·봇이 보낸 말을 함께 돌려준다."""
    deleted: list[str] = []
    sent: list[str] = []

    async def delete() -> None:
        if not deletable:
            raise RuntimeError("Message can't be deleted")
        deleted.append(text)

    async def send_message(note: str, **_kw: Any) -> None:
        sent.append(note)

    m = SimpleNamespace(date=utcnow() - timedelta(minutes=30), text=text, delete=delete,
                        chat=Chat(id=chat_id, username=None, send_message=send_message))
    return m, deleted, sent


@pytest.mark.parametrize("cmd", ["/threadstoken", "/instatoken", "/InstaToken@oneul_bot"])
async def test_stale_token_command_is_not_run_but_its_message_is_deleted(
    env: Env, cmd: str, caplog: pytest.LogCaptureFixture
) -> None:
    """재배포·재시작 중에 보낸 토큰 명령은 5분이 지나 실행되지 않지만, 토큰이 든 메시지는 그래도 지운다 (채팅에 남지 않게)."""
    from telegram.ext import ApplicationHandlerStop

    drop = next(h.callback for h, g in env.app.handlers if g == -1)
    secret = "IGAAT-secret-value-123"
    m, deleted, sent = _stale_token_msg(f"{cmd} {secret}")
    with caplog.at_level(logging.DEBUG), pytest.raises(ApplicationHandlerStop):
        await drop(SimpleNamespace(callback_query=None, effective_message=m), None)
    assert len(deleted) == 1 and len(sent) == 1 and "지웠어요" in sent[0] and "다시 보내" in sent[0]
    assert secret not in caplog.text and secret not in sent[0] and env.ctrl.calls == []


async def test_stale_token_command_that_cannot_be_deleted_tells_the_owner(env: Env, caplog: pytest.LogCaptureFixture) -> None:
    from telegram.ext import ApplicationHandlerStop

    drop = next(h.callback for h, g in env.app.handlers if g == -1)
    m, deleted, sent = _stale_token_msg("/threadstoken THQV-secret", deletable=False)
    with caplog.at_level(logging.DEBUG), pytest.raises(ApplicationHandlerStop):
        await drop(SimpleNamespace(callback_query=None, effective_message=m), None)
    assert deleted == [] and len(sent) == 1 and "직접 지우고" in sent[0] and "THQV-secret" not in caplog.text


@pytest.mark.parametrize(("text", "chat_id"), [("/run ppomppu", ADMIN), ("/threadstoken", ADMIN), ("/threadstoken abc", 777)])
async def test_other_stale_messages_are_only_dropped(env: Env, text: str, chat_id: int) -> None:
    """토큰이 없는 옛 명령, 관리자 챗이 아닌 곳의 토큰 명령은 예전처럼 버리기만 한다 (지우지도 답하지도 않음)."""
    from telegram.ext import ApplicationHandlerStop

    drop = next(h.callback for h, g in env.app.handlers if g == -1)
    m, deleted, sent = _stale_token_msg(text, chat_id=chat_id)
    with pytest.raises(ApplicationHandlerStop):
        await drop(SimpleNamespace(callback_query=None, effective_message=m), None)
    assert deleted == [] and sent == []



async def test_stale_plain_text_is_not_logged(env: Env, caplog: pytest.LogCaptureFixture) -> None:
    """명령이 아닌 옛 메시지(토큰만 붙여넣은 글 등)는 첫 낱말도 로그에 남기지 않는다. 명령은 이름만 남긴다."""
    from telegram.ext import ApplicationHandlerStop

    drop = next(h.callback for h, g in env.app.handlers if g == -1)
    for text in ("IGAAT-bare-secret-456", "/run ppomppu"):
        m, _, _ = _stale_token_msg(text)
        with caplog.at_level(logging.DEBUG), pytest.raises(ApplicationHandlerStop):
            await drop(SimpleNamespace(callback_query=None, effective_message=m), None)
    assert "IGAAT-bare-secret-456" not in caplog.text and "IGAAT" not in caplog.text
    assert "/run" in caplog.text and "ppomppu" not in caplog.text


# ------------------------------------------------------------------ 사진·파일·명령
async def test_photo_caption_for_blog_is_relayed(env: Env) -> None:
    with_llm(env, ans("blog"))
    await env.media(caption="9번에 넣어줘", photo=True)
    line = env.relayed[-1]
    assert line["nl"]["why"] == "media" and line["message"]["photo"][0]["file_id"].startswith("F")


async def test_album_follows_first_decision_including_earlier_sibling(env: Env) -> None:
    llm = with_llm(env, ans("blog"))
    await env.media(photo=True, group="g1")  # 설명 없는 앞 사진: 일단 잡아 둠
    assert env.relayed == []
    await env.media(caption="12번 2번 자리 이걸로", photo=True, group="g1")
    await env.media(photo=True, group="g1")
    assert len(env.relayed) == 3 and all(r["message"]["media_group_id"] == "g1" for r in env.relayed)
    assert len(llm.calls) == 1


async def test_dealish_photo_caption_and_bare_photo(env: Env) -> None:
    q = add_item(env.db, "애슐리 핫도그")
    with_llm(env, ans("/ok", q))
    await env.media(caption="이 딜 올려줘", photo=True)
    assert env.texts[-1] == nlmod.DEAL_PHOTO and env.relayed == [] and env.ctrl.calls == []
    env.out.clear()
    await env.media(photo=True)  # 설명도 답장도 없는 사진: 무시
    assert env.out == [] and env.relayed == []


async def test_photo_reply_to_blog_message_and_slash_caption_relayed_without_ai(env: Env) -> None:
    llm = with_llm(env, ans("none"))
    req = env.bot_msg("📸 #12 사진 요청", record=False)
    await env.media(photo=True, reply_to=req)
    await env.media(photo=True, caption="/photo 12 자리3")
    assert [r["nl"]["why"] for r in env.relayed] == ["reply_blog", "passthrough"] and llm.calls == []


async def test_unregistered_commands_pass_through(env: Env) -> None:
    await env.command("/fix 12 제목 짧게")
    await env.command("/drop 12")
    assert [r["message"]["text"] for r in env.relayed] == ["/fix 12 제목 짧게", "/drop 12"]
    assert all(r["nl"]["why"] == "passthrough" for r in env.relayed)
    await env.command("/foo")
    assert env.texts[-1] == nlmod.UNKNOWN_COMMAND and len(env.relayed) == 2
    await env.text("/사진 12 자리3")  # 한글 명령은 텔레그램이 명령으로 표시하지 않음
    assert env.relayed[-1]["message"]["text"] == "/사진 12 자리3"


async def test_chitchat_prefilter_and_rate_limit(env: Env) -> None:
    llm = with_llm(env, ans("/status"))
    await env.text("ㅋㅋ 고마워")
    assert env.texts[-1] == nlmod.CHAT_OK and llm.calls == []
    env.nl.cfg.rate_10min = 2
    await env.text("상태 어때")
    await env.text("상태 어때")
    await env.text("상태 어때")
    assert env.texts[-1] == nlmod.RATE_LIMITED and len(llm.calls) == 2


async def test_ai_down_suggests_commands(env: Env) -> None:
    with_llm(env, LLMError("x"))
    req = env.bot_msg("🔎 <b>정품 확인 필요 #675</b>", kind="review_auth", q=675)
    await env.text("이거 품절이야 올리지 마", reply_to=req)
    assert "/skip 675" in env.texts[-1] and env.ctrl.calls == []
    await env.text("블로그 글 어떻게 됐어")
    assert env.relayed[-1]["nl"]["why"] == "router"  # 블로그 얘기는 AI 없이도 넘김


async def test_kill_switch_restores_old_on_text(settings: Settings, db: Database) -> None:
    env = Env(settings, db, FakeLLM(ans("/status")), enabled=False)
    assert "on_media" not in env.app.names() and "on_other_command" not in env.app.names()
    await env.text("상품: 애슐리\n가격: 14,890원\nhttps://toss.im/_m/abc")
    assert env.ctrl.named("submit_manual")  # 예전처럼 바로 올림
    await env.text("안녕")
    assert env.texts[-1].startswith("무엇을 할지 모르겠습니다") and env.llm is not None and env.llm.calls == []


# ------------------------------------------------------------------ 기록·로그·도움말
async def test_send_with_id_records_every_chunk(settings: Settings, db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[int] = []

    async def no_sleep(_s: float) -> None:
        return None

    monkeypatch.setattr(admin_mod.asyncio, "sleep", no_sleep)

    class Bot:
        async def send_message(self, **kw: Any) -> SimpleNamespace:
            sent.append(len(sent) + 100)
            return SimpleNamespace(message_id=sent[-1])

    router = NLRouter(NLRouterConfig(), db, None, relay=lambda u: None)
    n = AdminNotifier(Bot(), ADMIN, settings.monitoring, None, "Asia/Seoul")  # type: ignore[arg-type]
    n.msg_log = router.record
    long = "🔗 <b>내 링크가 필요합니다 #675</b>\n" + "\n".join("줄" * 50 for _ in range(200))
    await n.send_with_id(long, kind="link", queue_id=675)
    assert len(sent) > 1
    for mid in sent:
        row = db.admin_msg(mid)
        assert row is not None and row["kind"] == "link" and row["queue_id"] == 675


async def test_reply_records_and_kind_from_text(env: Env) -> None:
    with_llm(env, ans("/queue"))
    await env.text("대기열")
    # 답장으로 보낸 메시지도 기록돼서, 거기에 다시 답장하면 딜봇 메시지로 본다
    rows = env.db._q("SELECT kind FROM admin_msgs")
    assert {r["kind"] for r in rows} >= {"notice", "queue"}


def test_all_replies_in_admin_are_tracked() -> None:
    src = Path(admin_mod.__file__).read_text(encoding="utf-8")
    bad = [ln.strip() for ln in src.splitlines()
           if re.search(r"\.reply_(text|photo|document)\(", ln) and "_track(" not in ln and "기록 안 함" not in ln]
    assert bad == []


async def test_log_line_has_intent_act_and_no_url_path(env: Env, caplog: pytest.LogCaptureFixture) -> None:
    with_llm(env, ans("/shot", None, "https://www.algumon.com/n/deal?secret=1"))
    with caplog.at_level(logging.INFO, logger="dealbot.monitoring.nl"):
        await env.text("이 페이지 화면 찍어줘 https://www.algumon.com/n/deal?secret=1 " + "가" * 80)
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("nl deal"))
    assert "intent=/shot" in line and "act=exec" in line and re.search(r"msg=\d+", line)
    text = line.split('text="', 1)[1].rstrip('"')
    assert len(text) <= 60 and "secret" not in line and "/n/deal" not in line and "www.algumon.com" in line


def test_help_mentions_words_and_catalog_is_consistent() -> None:
    assert "말로" in admin_mod.HELP_TEXT
    names = {c for c, _ in admin_mod.BOT_COMMANDS}
    for cmd in nlmod.DEAL_CATALOG:  # 목록의 명령은 모두 딜봇 명령 (메뉴에 있거나 숨은 명령)
        assert cmd.name.lstrip("/") in names | {"naverlogin", "naverlink", "shot", "html", "find", "hot"}, cmd.name
    s = nlmod.schema()
    assert s["additionalProperties"] is False and set(s["required"]) == set(s["properties"])
    assert "blog" in s["properties"]["intent"]["enum"]
    for c in nlmod.DEAL_CATALOG:
        assert c.name in nlmod.SYSTEM


async def test_posted_command_passes_the_number_and_channel_link(env: Env) -> None:
    """'채널에 올라갔는지 모릅니다 #번호' 에 대한 답: /posted 번호 [채널 글 링크] — 딜봇 명령이라 Blog-Auto 로 넘기지 않는다."""
    cmd = env.app.cb("cmd_posted")
    await cmd(update(env.msg("/posted #12 https://t.me/oneul_hotdeal/321")), SimpleNamespace(args=["#12", "https://t.me/oneul_hotdeal/321"]))
    await cmd(update(env.msg("/posted 13")), SimpleNamespace(args=["13"]))
    await cmd(update(env.msg("/posted")), SimpleNamespace(args=[]))
    assert env.ctrl.named("mark_posted") == [("mark_posted", 12, "https://t.me/oneul_hotdeal/321"), ("mark_posted", 13, "")]
    assert env.texts[:2] == ["✅ #12 기록", "✅ #13 기록"] and "/posted 12" in env.texts[-1] and not env.relayed


def test_every_catalog_command_has_a_registered_handler(env: Env) -> None:
    """목록에 넣은 명령은 모두 딜봇의 실제 명령 처리 함수로 실행된다 (/post 는 주인의 원래 글로 submit_manual)."""
    registered = {c for h, _ in env.app.handlers for c in getattr(h, "commands", ())}
    for c in nlmod.DEAL_CATALOG:
        assert c.name.lstrip("/") in registered, c.name


def test_registry_seeding_from_old_notices(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    d = Database(path)
    d._conn.execute("DELETE FROM kv WHERE key = 'admin_msgs_since'")
    d.kv_set("link_notice:675", "4321")
    d.kv_set("review_notice:31", "4322")
    d.close()
    d = Database(path)
    try:
        assert d.admin_msg(4321)["queue_id"] == 675
        assert d.admin_msg(4322)["kind"] == "" and d.kv_get("admin_msgs_since")
        assert d.kv_take("link_notice:675") == "4321" and d.kv_take("link_notice:675") is None
    finally:
        d.close()


# ------------------------------------------------------------------ JSON 호출기
async def test_json_asker_api_path(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, Any] = {}

    class Messages:
        async def create(self, **kw: Any) -> SimpleNamespace:
            seen.update(kw)
            return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text='{"intent": "/status"}')])

    asker = JsonAsker("key", model="m")
    monkeypatch.setattr(asker, "_get_client", lambda: SimpleNamespace(messages=Messages()))
    assert await asker.ask("S", "U", {"type": "object"}) == {"intent": "/status"}
    assert seen["output_config"] == {"format": {"type": "json_schema", "schema": {"type": "object"}}, "effort": "low"}
    assert seen["system"][0]["cache_control"] == {"type": "ephemeral"}

    class Refuse:
        async def create(self, **kw: Any) -> SimpleNamespace:
            return SimpleNamespace(stop_reason="refusal", content=[])

    monkeypatch.setattr(asker, "_get_client", lambda: SimpleNamespace(messages=Refuse()))
    with pytest.raises(LLMError):
        await asker.ask("S", "U", {})


def test_json_asker_needs_an_api_model_name_for_the_api() -> None:
    """API 모델 이름(서버 .env)이 없으면 키가 있어도 API 를 쓰지 않는다: CLI 가 있으면 CLI, 없으면 꺼짐."""
    assert JsonAsker("key", model="test-model").backend == "api"
    assert JsonAsker("key", cli_bin="/opt/fake/claude").backend == "cli"
    assert JsonAsker("key").backend is None  # 테스트에서는 claude CLI 를 못 찾음


async def test_json_asker_without_backend_raises() -> None:
    asker = JsonAsker(None, model="m")  # 테스트에서는 claude CLI 를 못 찾음
    assert asker.backend is None
    with pytest.raises(LLMError):
        await asker.ask("S", "U", {})
