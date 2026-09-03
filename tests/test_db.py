from __future__ import annotations

from datetime import timedelta

from dealbot.models import Deal, DealVerdict, Product
from dealbot.storage.db import Database
from dealbot.utils.timeutil import utcnow


def _p(pid: str = "1", price: int = 10000, source: str = "s") -> Product:
    return Product(source=source, product_id=pid, name=f"상품{pid}", price=price, url=f"https://www.coupang.com/vp/products/{pid}")


def _deal(pid: str = "1", price: int = 10000, score: float = 40) -> Deal:
    return Deal(product=_p(pid, price), verdict=DealVerdict(is_deal=True, reasons=["r"], score=score), detected_at=utcnow())


def test_price_history_and_stats(db: Database) -> None:
    now = utcnow()
    for i, price in enumerate([10000, 11000, 12000]):
        db.record_observation(_p(price=price), now - timedelta(days=10 - i))
    db.record_observation(_p(price=50000), now - timedelta(days=40))  # 창 밖

    stats = db.price_stats("1", 30, now)
    assert stats.count == 3 and stats.avg == 11000 and stats.min == 10000 and stats.max == 12000
    assert stats.last_price == 12000
    assert db.product_count() == 1 and db.price_history_count() == 4

    # 현재 시각의 관측은 통계에서 제외 (판정 후 저장하는 흐름)
    db.record_observation(_p(price=1), now)
    assert db.price_stats("1", 30, now).count == 3
    assert db.price_stats("nope", 30, now).count == 0


def test_posts_dedup_and_counts(db: Database) -> None:
    now = utcnow()
    assert not db.posted_within("1", 7, now)
    db.record_post(_deal(), channel_id="@c", message_id=5, now=now - timedelta(days=3))
    assert db.posted_within("1", 7, now)
    assert not db.posted_within("1", 2, now)
    assert db.count_posts_since(now - timedelta(days=4)) == 1
    assert db.count_posts_since(now - timedelta(days=1)) == 0
    assert db.last_post_time() == now - timedelta(days=3)
    assert db.recent_posts()[0]["product_id"] == "1"


def test_queue_lifecycle(db: Database) -> None:
    now = utcnow()
    assert db.enqueue(_deal("a", score=10), score=10, now=now)
    assert db.enqueue(_deal("b", score=50), score=50, now=now)
    assert not db.enqueue(_deal("a"), score=99, now=now)  # pending 중복 방지

    item = db.next_pending()
    assert item is not None and item.product_id == "b"  # 점수 높은 것 먼저
    assert item.deal.product.name == "상품b"

    db.update_queue_item(item.id, status="pending", error="boom", increment_attempts=True)
    again = db.next_pending()
    assert again is not None and again.id == item.id and again.attempts == 1 and again.last_error == "boom"

    db.update_queue_item(item.id, status="published")
    assert db.next_pending().product_id == "a"  # type: ignore[union-attr]
    assert db.queue_counts() == {"pending": 1, "published": 1}

    # 만료
    assert db.expire_queue(now + timedelta(seconds=1), now) == 1
    assert db.queue_counts()["expired"] == 1
    assert db.enqueue(_deal("a"), score=1, now=now)  # 만료 후 재등록 가능


def test_seen_runs_events_kv_summary(db: Database) -> None:
    assert not db.is_seen("ppomppu", "123")
    db.mark_seen("ppomppu", "123", "coupang:1", url="https://www.coupang.com/vp/products/1")
    assert db.is_seen("ppomppu", "123")
    assert db.seen_item("ppomppu", "123") == ("coupang:1", "https://www.coupang.com/vp/products/1")
    assert db.seen_item("ppomppu", "nope") is None

    run_id = db.start_run("goldbox")
    db.finish_run(run_id, status="ok", collected=10, deals=2, queued=1)
    last = db.last_run("goldbox")
    assert last and last.status == "ok" and last.collected == 10
    run2 = db.start_run("ppomppu")
    db.finish_run(run2, status="error", error="boom")

    db.log_event("ERROR", "collector:ppomppu", "boom")
    db.log_event("INFO", "publish", "ok")
    assert db.last_error()["kind"] == "collector:ppomppu"  # type: ignore[index]
    assert len(db.recent_events(10)) == 2

    db.kv_set("k", "v")
    db.kv_set("k", "v2")
    assert db.kv_get("k") == "v2" and db.kv_get("x", "d") == "d"

    db.record_post(_deal("z"), channel_id=None, message_id=None)
    s = db.summary(utcnow() - timedelta(hours=1))
    assert s.runs == 2 and s.run_errors == 1 and s.collected == 10 and s.deals_found == 2
    assert s.published == 1 and s.errors == 1 and s.top_posts and s.top_posts[0]["product_id"] == "z"


def test_prune(db: Database) -> None:
    now = utcnow()
    db.record_observation(_p(), now - timedelta(days=200))
    db.record_observation(_p(), now)
    db.log_event("INFO", "x", "old", now - timedelta(days=40))
    res = db.prune(price_history_days=180, events_days=30, now=now)
    assert res["price_history"] == 1 and res["events"] == 1
    assert db.price_history_count() == 1


def test_queue_awaiting_link_flow(db: Database) -> None:
    now = utcnow()
    assert db.enqueue(_deal("t"), score=5, now=now)
    item = db.next_pending()
    assert item is not None
    db.update_queue_item(item.id, status="awaiting_link", error="manual link required")
    assert db.next_pending() is None
    assert not db.enqueue(_deal("t"), score=9, now=now)  # 링크 대기 중에도 중복 등록 불가
    assert [i.id for i in db.awaiting_items()] == [item.id]
    assert db.get_queue_item(item.id).status == "awaiting_link"  # type: ignore[union-attr]

    updated = db.set_queue_link(item.id, "https://toss.im/_m/MINE")
    assert updated is not None and updated.status == "pending" and updated.deal.affiliate_url == "https://toss.im/_m/MINE"
    assert db.set_queue_link(999, "x") is None

    db.update_queue_item(item.id, status="awaiting_link")
    assert db.expire_queue(now - timedelta(hours=1), now, awaiting_older_than=now + timedelta(seconds=1)) == 1
    assert db.queue_counts() == {"expired": 1}
    s = db.summary(now - timedelta(hours=1))
    assert s.awaiting == 0 and s.expired == 1


def test_market_quotes_and_community_stats(db: Database) -> None:
    now = utcnow()
    assert db.get_market_quote("toss:1", 24) is None
    db.set_market_quote("toss:1", price=17900, source="coupang", title="t", url="u", now=now - timedelta(hours=2))
    q = db.get_market_quote("toss:1", 24, now)
    assert q and q["price"] == 17900
    assert db.get_market_quote("toss:1", 1, now) is None  # 만료

    db.mark_seen("ppomppu", "1", "coupang:1", title="a", recommend=0, views=10, now=now)
    db.mark_seen("ppomppu", "2", "toss:1", title="b", recommend=6, views=10, now=now)
    db.mark_seen("ppomppu", "3", None, title="c", recommend=12, views=10, now=now)
    db.touch_seen("ppomppu", "1", recommend=3, views=50, now=now)
    stats = db.community_stats(now - timedelta(hours=1))
    assert stats["ppomppu"]["posts"] == 3
    assert stats["ppomppu"]["rec_ge"] == {1: 3, 3: 3, 5: 2, 10: 1, 20: 0}
    assert db.summary(now - timedelta(hours=1)).community["ppomppu"]["posts"] == 3  # type: ignore[index]
