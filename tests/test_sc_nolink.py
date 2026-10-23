"""'쇼핑 커넥트에 없음' 한 번에 빼기 + 그 판매처는 다음부터 묻지 않고 링크 없이 (주인 10/18).

주인 말: "'쇼핑 커넥트에 없음' 버튼 한 번으로 바로 빠지게 하기 … 없을 것 같은 제품은 요청 안 하고 그냥 올려주는 기능".
- 네이버 링크 요청(할 일 '링크 넣기')의 더보기·자세히 시트에 '쇼핑 커넥트에 없음' → 작은 시트 '링크 없이 올리기'(주) · '안 올리기'
- 링크 없이 올리기 = 원본 상품 주소로, 제휴 고지 없이 (수수료가 없으니). 가격 확인·사진 규칙은 여느 딜처럼
- 어느 쪽이든 그 판매처(스토어 주소의 가게 이름)를 기억 → 그 판매처의 새 네이버 딜은 묻지 않고 링크 없이 (설정으로 끔)
- 주인이 그 판매처 딜에 쇼핑 커넥트 링크(naver.me)를 넣으면 기억에서 뺀다. 봇이 스스로 적지 않는다
가짜만 쓴다 (네트워크·텔레그램·카톡 없음).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from dash_helpers import post_json, push_settings, session_headers
from dealbot import sc_stores
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.dashboard.state import StateBuilder
from dealbot.models import Deal, DealVerdict, Product
from dealbot.shops import deal_disclosure

STORE = "paulham"
URL = f"https://smartstore.naver.com/{STORE}/products/1001"
NAME = "[네이버] 폴햄 여성 에코베로 라운드 긴팔티 (9,900원/무료)"
NAVER_DISCLOSURE = "이 포스팅은 네이버 쇼핑 커넥트 활동의 일환으로, 판매 발생 시 수수료를 제공받습니다."


@pytest.fixture
def bot(settings: Settings):  # type: ignore[no-untyped-def]
    push_settings(settings)
    settings.publish.min_interval_seconds = 0
    settings.publish.dry_run = False
    settings.autolink.naver.enabled = False  # 이 파일은 링크 요청 흐름만 본다 (자동 링크는 test_autolink — config.yaml 은 10/20 부터 켬)
    b = DealBot(settings)
    b.state.dry_run = False
    sent: list[str] = []

    async def send_with_id(text: str, *, silent: bool = False, **_kw: object) -> int:
        sent.append(text)
        return 70 + len(sent)

    async def available(url: str) -> bool | None:
        return None

    b.notifier.send_with_id = send_with_id  # type: ignore[method-assign]
    b.enricher.check_available = available  # type: ignore[method-assign]
    b.sent = sent  # type: ignore[attr-defined]
    yield b
    b.db.close()


def _product(no: int = 1001, *, url: str | None = None, auth: str = "ok", seller: str | None = "폴햄 공식스토어",
             shop: str = "naver", **kw: Any) -> Product:
    base: dict[str, Any] = dict(
        source="ppomppu", product_id=f"{shop}:{no}", shop=shop, name=NAME, price=9900,
        url=url or f"https://smartstore.naver.com/{STORE}/products/{no}", recommend_count=9,
        extra={"auth": {"status": auth, "reason": "공식 판매처", "seller": seller}},
    )
    base.update(kw)
    return Product(**base)


def _enqueue(bot: DealBot, p: Product) -> int:
    assert bot.db.enqueue(Deal(p, DealVerdict(is_deal=True, score=5)), score=5)
    return next(it.id for it in bot.db.pending_items(50) if it.product_id == p.product_id)


def _waiting(bot: DealBot, p: Product | None = None) -> int:
    qid = _enqueue(bot, p or _product())
    bot.db.update_queue_item(qid, status="awaiting_link", error="manual link required")
    return qid


async def _act(bot: DealBot, action: str, args: dict) -> dict:
    status, body, _ = await post_json(bot, "/dashboard/api/action", {"action": action, "args": args}, session_headers(bot))
    assert status == 200, body
    return body


def _link_requests(settings: Settings) -> list[dict]:
    p = settings.data_dir / "blogauto_inbox.jsonl"
    rows = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines()] if p.exists() else []
    return [r["owner_notice"] for r in rows if r.get("owner_notice", {}).get("kind") == "link_request"]


def _item(bot: DealBot, qid: int) -> dict:
    return next(x for x in StateBuilder(bot).items() if x["iid"] == f"deal-{qid}")


# ---------------------------------------------------------------- 1. 화면: 네이버 링크 요청에만
def test_naver_link_request_offers_not_in_shopping_connect(bot: DealBot) -> None:
    qid = _waiting(bot)
    it = _item(bot, qid)
    a = it["more"][0]
    assert a["action"] == "deal.nolink" and a["label"] == "쇼핑 커넥트에 없음" and a["input"] == "choice"
    assert not a["confirm"] and not a["danger"]  # 고르는 시트가 곧 확인 (두 번 묻지 않음)
    assert [(c["value"], c["label"], c.get("primary", False)) for c in a["choices"]] == [
        ("post", "링크 없이 올리기", True), ("skip", "안 올리기", False)]
    assert "다음 딜부터" in a["note"]
    assert [m["action"] for m in it["more"]][:2] == ["deal.nolink", "deal.skip"]
    # 토스·쿠팡 링크 요청엔 없음
    toss = _waiting(bot, _product(2, shop="toss", url="https://toss.im/_m/abc"))
    assert all(m["action"] != "deal.nolink" for m in _item(bot, toss)["more"])


# ---------------------------------------------------------------- 2. 링크 없이 올리기
async def test_post_without_link_uses_the_product_url_and_no_disclosure(bot: DealBot, settings: Settings) -> None:
    qid = _waiting(bot)
    body = await _act(bot, "deal.nolink", {"qid": qid, "name": "post"})
    assert body["status"] == "ok" and body["code"] == "nolink" and "다음부터 묻지 않고" in body["text"]
    item = bot.db.get_queue_item(qid)
    assert item.status == "pending" and item.deal.affiliate_url == URL  # type: ignore[union-attr]
    assert item.deal.product.extra["raw_link"] == "owner_no_sc"  # type: ignore[union-attr]
    assert deal_disclosure(bot.registry.get("naver"), item.deal.product.extra) is None  # type: ignore[union-attr]
    assert [e["label"] for e in sc_stores.entries(bot.db)] == ["폴햄 공식스토어"]
    # 발행: 여느 딜처럼 (원본 주소 · 고지·광고 표시·수수료 말 없음)
    assert await bot.process_queue_once()
    assert bot.db.get_queue_item(qid).status == "published"  # type: ignore[union-attr]
    rows = [json.loads(x) for x in (settings.data_dir / "preview_deals.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["link"] == URL and rows[-1]["disclosure"] == ""
    text = bot.publisher.render(item.deal)  # type: ignore[union-attr]
    assert URL in text and "수수료" not in text and "광고" not in text


async def test_skip_choice_skips_and_also_remembers_the_store(bot: DealBot) -> None:
    qid = _waiting(bot)
    body = await _act(bot, "deal.nolink", {"qid": qid, "name": "skip"})
    assert body["code"] == "skipped"
    assert bot.db.get_queue_item(qid).status == "skipped"  # type: ignore[union-attr]
    assert sc_stores.known(bot.db, _product(9)) is not None


async def test_nolink_refuses_wrong_items_and_bad_args(bot: DealBot) -> None:
    status, body, _ = await post_json(bot, "/dashboard/api/action", {"action": "deal.nolink", "args": {"qid": 1, "name": "x"}},
                                      session_headers(bot))
    assert status == 400 and body["code"] == "bad_args"
    toss = _waiting(bot, _product(3, shop="toss", url="https://toss.im/_m/abc"))
    assert (await _act(bot, "deal.nolink", {"qid": toss, "name": "post"}))["code"] == "wrong_state"
    pending = _enqueue(bot, _product(4))
    assert (await _act(bot, "deal.nolink", {"qid": pending, "name": "post"}))["code"] == "wrong_state"
    assert (await _act(bot, "deal.nolink", {"qid": 999, "name": "post"}))["code"] == "not_found"
    assert sc_stores.entries(bot.db) == []  # 아무 것도 적지 않음


# ---------------------------------------------------------------- 3. 기억한 판매처: 묻지 않고 바로
async def test_remembered_store_posts_without_asking(bot: DealBot, settings: Settings) -> None:
    first = _waiting(bot)
    await _act(bot, "deal.nolink", {"qid": first, "name": "skip"})
    asked = len(_link_requests(settings))
    qid = _enqueue(bot, _product(2002))
    assert await bot.process_queue_once()
    item = bot.db.get_queue_item(qid)
    assert item.status == "pending" and item.deal.product.extra["raw_link"] == "sc_store"  # type: ignore[union-attr]
    assert item.deal.affiliate_url == f"https://smartstore.naver.com/{STORE}/products/2002"  # type: ignore[union-attr]
    assert len(_link_requests(settings)) == asked and bot.sent == []  # type: ignore[attr-defined]  # 링크 요청 없음
    assert await bot.process_queue_once()
    assert bot.db.get_queue_item(qid).status == "published"  # type: ignore[union-attr]
    # 다른 판매처는 예전처럼 링크 요청
    other = _enqueue(bot, _product(3003, url="https://smartstore.naver.com/otherstore/products/3003", seller="다른가게"))
    assert await bot.process_queue_once()
    assert bot.db.get_queue_item(other).status == "awaiting_link"  # type: ignore[union-attr]


async def test_remembered_store_still_goes_through_the_price_gate(bot: DealBot) -> None:
    sc_stores.remember(bot.db, _product(), via="post")
    p = _product(2002, extra={"auth": {"status": "ok", "reason": "", "seller": "폴햄 공식스토어"},
                              "page_price": {"status": "unverified", "title_price": 9900, "reason": "페이지를 못 읽음"}})
    held: list[str] = []

    async def hold(item: Any, deal: Any, *, when: str = "") -> None:
        held.append(when)
        bot.db.update_queue_item(item.id, status="held", error=f"held ({when})")

    async def read_price(prod: Product) -> None:
        prod.extra["page_price"] = {"status": "hold", "title_price": 9900, "reason": "페이지 가격 다름"}

    bot._hold_item = hold  # type: ignore[method-assign]
    bot._read_unverified_price = read_price  # type: ignore[method-assign]
    qid = _enqueue(bot, p)
    assert await bot.process_queue_once()
    assert held == ["before link"] and bot.db.get_queue_item(qid).status == "held"  # type: ignore[union-attr]


async def test_unverified_seller_is_still_asked(bot: DealBot, settings: Settings) -> None:
    sc_stores.remember(bot.db, _product(), via="post")
    settings.autolink.naver.unverified = "manual"  # 운영 설정은 auto (주인 10/23) — manual 일 때의 흐름을 본다
    qid = _enqueue(bot, _product(2002, auth="unknown"))
    assert await bot.process_queue_once()
    assert bot.db.get_queue_item(qid).status == "awaiting_link"  # type: ignore[union-attr]  # 판매처 확인 안 됨 → 주인이 봄
    settings.autolink.naver.unverified = "auto"
    q2 = _enqueue(bot, _product(2003, auth="unknown"))
    assert await bot.process_queue_once()
    assert bot.db.get_queue_item(q2).deal.product.extra.get("raw_link") == "sc_store"  # type: ignore[union-attr]


async def test_flag_off_asks_as_before(bot: DealBot, settings: Settings) -> None:
    sc_stores.remember(bot.db, _product(), via="post")
    settings.autolink.naver.no_link_autopost = False
    qid = _enqueue(bot, _product(2002))
    assert await bot.process_queue_once()
    assert bot.db.get_queue_item(qid).status == "awaiting_link"  # type: ignore[union-attr]
    assert "자동 올리기 꺼짐" in StateBuilder(bot).deal()["nolink"]["label"]
    assert "note" not in _item(bot, qid)["more"][0]  # '다음부터 묻지 않아요' 는 켜 있을 때만


# ---------------------------------------------------------------- 4. 기억에서 빼기
async def test_owner_shopping_connect_link_removes_the_store(bot: DealBot) -> None:
    sc_stores.remember(bot.db, _product(), via="skip")
    plain = _waiting(bot, _product(5))
    await bot.attach_link(plain, f"https://smartstore.naver.com/{STORE}/products/5")  # 원본 주소는 쇼핑 커넥트 링크가 아님
    assert sc_stores.entries(bot.db)
    qid = _waiting(bot, _product(6))
    body = await _act(bot, "deal.link", {"qid": qid, "url": "https://naver.me/xAbC12"})
    assert body["code"] == "linked"
    assert sc_stores.entries(bot.db) == []  # 판매처가 쇼핑 커넥트에 들어옴
    deal = bot.db.get_queue_item(qid).deal  # type: ignore[union-attr]
    assert "raw_link" not in deal.product.extra and NAVER_DISCLOSURE in bot.publisher.render(deal)


async def test_deal_tab_lists_stores_and_clears_one(bot: DealBot) -> None:
    assert StateBuilder(bot).deal()["nolink"] is None  # 없으면 줄도 없음
    sc_stores.remember(bot.db, _product(), via="post")
    sc_stores.remember(bot.db, _product(7, url="https://brand.naver.com/dyson/products/7", seller=None), via="skip")
    line = StateBuilder(bot).deal()["nolink"]
    assert line["label"] == "쇼핑 커넥트 없는 판매처 2곳 — 링크 없이 바로 올려요"
    assert line["action"] == "deal.sc_forget" and line["input"] == "choice" and line["title"] == "쇼핑 커넥트 없는 판매처"
    assert sorted(c["label"] for c in line["choices"]) == ["dyson", "폴햄 공식스토어"]
    key = next(c["value"] for c in line["choices"] if c["label"] == "dyson")
    body = await _act(bot, "deal.sc_forget", {"name": key})
    assert body["code"] == "done"
    assert [e["label"] for e in sc_stores.entries(bot.db)] == ["폴햄 공식스토어"]
    assert (await _act(bot, "deal.sc_forget", {"name": key}))["code"] == "not_found"


# ---------------------------------------------------------------- 5. 판매처 알아보기
def test_store_identity_rules(bot: DealBot) -> None:
    assert sc_stores.store_of(_product()) == (f"s:{STORE}", "폴햄 공식스토어")
    assert sc_stores.store_of(_product(url="https://m.brand.naver.com/dyson/products/9", seller=None)) == ("s:dyson", "dyson")
    # 가게를 안 밝힌 주소: 상품 페이지의 판매자 이름으로 (덜 믿을 만함)
    main = _product(url="https://smartstore.naver.com/main/products/9", seller="폴햄 공식스토어")
    assert sc_stores.store_of(main) == ("n:폴햄공식스토어", "폴햄 공식스토어")
    assert sc_stores.store_of(_product(url="https://smartstore.naver.com/main/products/9", seller=None)) is None
    assert sc_stores.store_of(_product(shop="coupang", url="https://www.coupang.com/vp/products/1")) is None
    # 주소로 적은 판매처도 같은 판매자 이름이면 알아본다
    sc_stores.remember(bot.db, _product(), via="post")
    assert sc_stores.known(bot.db, main) is not None
    assert sc_stores.known(bot.db, _product(url="https://smartstore.naver.com/main/products/9", seller="다른가게")) is None


async def test_unknown_store_still_posts_but_is_not_remembered(bot: DealBot) -> None:
    qid = _waiting(bot, _product(8, url="https://smartstore.naver.com/main/products/8", seller=None))
    body = await _act(bot, "deal.nolink", {"qid": qid, "name": "post"})
    assert body["code"] == "nolink" and "다음부터" not in body["text"]
    assert bot.db.get_queue_item(qid).status == "pending" and sc_stores.entries(bot.db) == []  # type: ignore[union-attr]
