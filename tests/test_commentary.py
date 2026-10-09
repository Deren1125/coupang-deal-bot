"""AI 한줄평(commentary): 규칙 검사, 캐시, CLI 출력 처리, 실패 신호. 실제 claude CLI 대신 가짜 실행 파일을 쓴다."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from dealbot.commentary import (
    SYSTEM,
    Commentator,
    check_comment,
    check_hook,
    check_short_name,
    check_take,
    drop_repeats,
)
from dealbot.models import Deal, DealVerdict, Product
from dealbot.storage.db import Database
from dealbot.utils.text import unit_price

HOLLYS = "할리스 바닐라 딜라이트 로우슈거, 24개"
WATER = "스파클 생수 무라벨, 500ml, 40병"
STANLEY = "[샘플] 스탠리 텀블러 퀜처 H2.0 플로우스테이트 1.18L"


def _deal(pid: str = "toss:1", name: str = HOLLYS, price: int = 24900, **verdict: float) -> Deal:
    p = Product(source="toss", product_id=pid, shop="toss", name=name, price=price, url="https://toss.im/x")
    return Deal(product=p, verdict=DealVerdict(is_deal=True, **verdict))


def _ok(**fields: str) -> str:
    return json.dumps({"type": "result", "is_error": False, "result": "", "structured_output": fields}, ensure_ascii=False)


GOOD = {"comment": "사무실 간식으로 두기 괜찮은 구성이에요.", "thread_hook": "오후만 되면 단 거 당김",
        "thread_take": "단 거 줄이는 중이면 이 정도가 딱임", "short_name": "할리스 바닐라 24개"}


class FakeCli:
    """claude 대신 쓰는 가짜 실행 파일. stdout/stderr/종료 코드를 바꿔 가며 쓰고, 불린 횟수를 센다."""

    def __init__(self, tmp_path: Path) -> None:
        self.dir = tmp_path
        self.calls = tmp_path / "calls"
        self.path = tmp_path / "claude"
        self.set(_ok(**GOOD))

    def set(self, stdout: str, *, rc: int = 0, stderr: str = "", sleep: float = 0) -> None:
        (self.dir / "out").write_text(stdout, encoding="utf-8")
        (self.dir / "err").write_text(stderr, encoding="utf-8")
        self.path.write_text(
            "#!/bin/sh\ncat > /dev/null\n"
            f"echo x >> '{self.calls}'\n"
            + (f"exec sleep {sleep}\n" if sleep else "")
            + f"cat '{self.dir / 'err'}' >&2\ncat '{self.dir / 'out'}'\nexit {rc}\n"
        )
        self.path.chmod(0o755)

    @property
    def count(self) -> int:
        return len(self.calls.read_text().splitlines()) if self.calls.exists() else 0


@pytest.fixture
def cli(tmp_path: Path) -> FakeCli:
    return FakeCli(tmp_path)


def _commentator(cli: FakeCli, db: Database | None = None, **kw: float) -> Commentator:
    c = Commentator(enabled=True, db=db, **kw)
    c.bin = str(cli.path)
    return c


# ---------------------------------------------------------------- 정보줄과 같은 말 되풀이 (#10, #39)


def test_prompt_no_longer_invites_unit_price() -> None:
    assert "단가 하나 정도만" not in SYSTEM
    assert "단가" in SYSTEM and "배송" in SYSTEM and "되풀이" in SYSTEM


def test_drop_repeats_keeps_the_judgement() -> None:
    assert drop_repeats("개당 1,038원이라 박스로 쟁여두기 편해요.", ["개당 1,038원 꼴"]) == "박스로 쟁여두기 편해요."
    assert drop_repeats("병당 148원이고 무배라 물 많이 마시는 집에 좋아요.", ["병당 148원 꼴", "무료배송"]) == "물 많이 마시는 집에 좋아요."
    assert drop_repeats("병당 148원에 무료배송이라 무라벨 생수 찾던 분께 맞아요.", ["병당 148원 꼴", "무료 로켓배송"]) == \
        "무라벨 생수 찾던 분께 맞아요."
    two = "로우슈거라 단 음료 부담스러운 분들한테 괜찮아요. 개당 1,038원이면 부담 적네요."
    assert drop_repeats(two, ["개당 1,038원 꼴"]) == "로우슈거라 단 음료 부담스러운 분들한테 괜찮아요."
    assert drop_repeats("개당 1,038원이면 부담 적네요.", ["개당 1,038원 꼴"]) is None  # 남는 말이 판단이 아니면 통째로 뺌
    assert drop_repeats("쿠팡보다 29%나 싸서 좋아요.", ["쿠팡보다 29%↓"]) is None
    # 같은 숫자가 아니면 그대로 (1,148원 ≠ 148원), 배송이 정보줄에 없으면 배송 얘기도 그대로
    assert drop_repeats("1,148원이면 괜찮아요.", ["병당 148원 꼴"]) == "1,148원이면 괜찮아요."
    assert drop_repeats("무배라 부담 없어요.", ["병당 148원 꼴"]) == "무배라 부담 없어요."


async def test_write_strips_unit_price_already_on_info_line(cli: FakeCli, db: Database) -> None:
    each = unit_price(HOLLYS, 24900).split(" (")[0]  # '개당 1,038원'
    cli.set(_ok(**(GOOD | {"comment": f"{each}이라 박스로 쟁여두기 편해요."})))
    got = await _commentator(cli, db).write(_deal(), f"쿠팡 최저가 35,100원보다 29% 싸요. / {each}", shown=[f"{each} 꼴"])
    assert got["comment"] == "박스로 쟁여두기 편해요."


async def test_channel_post_shows_unit_price_once(settings, tmp_path: Path) -> None:  # noqa: ANN001
    from dealbot.app import DealBot

    settings.collectors = []
    bot = DealBot(settings)
    try:
        cli = FakeCli(tmp_path)
        bot.commentator.enabled, bot.commentator.bin = True, str(cli.path)
        deal = _deal(name=WATER, price=5900)
        each = unit_price(WATER, 5900).split(" (")[0]  # '병당 148원'
        cli.set(_ok(**(GOOD | {"comment": f"{each}이고 무배라 물 많이 마시는 집에 좋아요.", "short_name": "스파클 생수 40병"})))
        deal.product.is_free_shipping = True
        await bot.add_commentary(deal)
        text = bot.publisher.render(deal)
        amount = each.split()[-1]
        assert text.count(amount) == 1, text
        assert deal.product.extra["comment"] == "물 많이 마시는 집에 좋아요."
    finally:
        await bot.close()


# ---------------------------------------------------------------- 스레드 판단 한 줄은 반말만 (#13)


def test_thread_take_must_stay_casual() -> None:
    assert check_take("자취생한테 딱 좋은 양이에요") is None
    assert check_take("자취생한테 딱 좋은 양이에요. 근데 맛은 호불호") is None
    assert check_take("필요한 분만 사시면 됩니다") is None
    assert check_take("자취하면 이 정도는 쟁여둘 만함") == "자취하면 이 정도는 쟁여둘 만함"
    assert check_take("자취생이면 이 정도 양 필요") == "자취생이면 이 정도 양 필요"  # '필요'는 존댓말 아님
    assert check_take("박스로 쟁여두기 편함") == "박스로 쟁여두기 편함"
    assert check_take("가" * 46) is None  # 스레드 판단은 짧게
    assert check_hook("휴지 떨어지면 곤란하죠") is None and check_hook("휴지 떨어지면 곤란해요") is None
    assert check_hook("휴지 떨어진 거 꼭 샤워 끝나고 알게 됨")


async def test_write_drops_polite_thread_take(cli: FakeCli) -> None:
    cli.set(_ok(**(GOOD | {"thread_take": "자취생한테 딱 좋은 양이에요"})))
    got = await _commentator(cli).write(_deal(), "개당 1,038원")
    assert "thread_take" not in got and got["comment"] == GOOD["comment"]


# ---------------------------------------------------------------- 금지어 오탐·등급 낱말 (#23, #45)


def test_banned_words_without_false_positives() -> None:
    assert check_hook("강추위 오기 전에 생수 쟁여둠") == "강추위 오기 전에 생수 쟁여둠"
    assert check_hook("물 마시는 거 자꾸 놓치는 사람") == "물 마시는 거 자꾸 놓치는 사람"
    assert check_comment("강추해요.") is None and check_comment("놓치지 마세요.") is None
    assert check_comment("놓치면 후회해요.") is None and check_comment("강력 추천해요.") is None
    assert check_comment("평소보다 확실히 싸게 나왔어요.") is None  # 주인이 싫어하는 AI 말투
    assert check_comment("역대급 가격이에요.") is None


async def test_tier_labels_are_not_sent_as_facts(settings) -> None:  # noqa: ANN001
    from dealbot.app import DealBot
    from dealbot.cli import sample_deal

    settings.collectors = []
    bot = DealBot(settings)
    seen: list[tuple[str, tuple]] = []

    async def fake_write(deal: Deal, facts: str = "", shown=()) -> dict:  # noqa: ANN001
        seen.append((facts, tuple(shown)))
        return {}

    try:
        bot.commentator.write = fake_write  # type: ignore[method-assign]
        for avg in (70000, 120000):  # 평소보다 57%↓ 강추(must) · 75%↓ 초특가(top)
            deal = sample_deal()
            deal.verdict.avg_price, deal.verdict.below_avg_pct = avg, round((1 - 29900 / avg) * 100, 1)
            assert {"강력 추천", "역대급"} & set(bot.publisher.renderer.deal_facts(deal)["labels"])
            await bot.add_commentary(deal)
        for facts, shown in seen:
            assert "강력 추천" not in facts and "역대급" not in facts
            assert "29,900원" in shown  # 첫 줄 가격은 '이미 나가는 것'으로 넘긴다
    finally:
        await bot.close()


# ---------------------------------------------------------------- 캐시: 다른 상품·옵션·가격이면 다시 묻는다 (#34, #35)


def test_cache_key_keeps_full_product_id_and_facts() -> None:
    c = Commentator(enabled=False)
    keys = {
        c._key(_deal("naver:url:abcd1234"), "f"), c._key(_deal("naver:url:ffff0000"), "f"),
        c._key(_deal("coupang:123:456"), "f"), c._key(_deal("coupang:123:789"), "f"),
        c._key(_deal("coupang:123:456"), "개당 829원"), c._key(_deal("coupang:123:456", price=19900), "f"),
    }
    assert len(keys) == 6
    assert c._key(_deal("toss:1"), "f") == c._key(_deal("toss:1"), "f")


async def test_other_product_never_gets_cached_lines(cli: FakeCli, db: Database) -> None:
    c = _commentator(cli, db)
    a = await c.write(_deal("naver:url:aaaa", HOLLYS), "개당 1,038원")
    assert a["short_name"] == "할리스 바닐라 24개" and cli.count == 1
    cli.set(_ok(comment="물 많이 마시는 집에 좋아요.", thread_hook="물은 꼭 무거울 때 떨어짐",
                thread_take="물 많이 마시면 박스가 편함", short_name="스파클 생수 40병"))
    b = await c.write(_deal("naver:url:bbbb", WATER, 5900), "병당 148원")
    assert b["short_name"] == "스파클 생수 40병" and cli.count == 2


async def test_cache_hit_is_rechecked_and_partial_results_are_retried(cli: FakeCli, db: Database) -> None:
    c = _commentator(cli, db)
    deal = _deal()
    # 네 칸이 다 통과한 답만 저장 → 같은 상품·같은 정보면 다시 안 묻는다
    assert len(await c.write(deal, "개당 1,038원")) == 4 and cli.count == 1
    assert len(await c.write(deal, "개당 1,038원")) == 4 and cli.count == 1
    # 가격(정보)이 바뀌면 새로 묻는다
    await c.write(deal, "개당 829원")
    assert cli.count == 2
    # 저장된 값도 지금 규칙으로 다시 검사: 정보에 없는 숫자가 들어 있으면 버리고 다시 묻는다
    key = c._key(deal, "개당 500원")
    db.kv_set(key, json.dumps(GOOD | {"comment": "개당 1,038원이라 좋아요."}, ensure_ascii=False))
    got = await c.write(deal, "개당 500원")
    assert cli.count == 3 and got["comment"] == GOOD["comment"]
    # 일부만 통과한 답은 저장하지 않는다 (다음 글에서 다시 시도)
    other = _deal("toss:2")
    cli.set(_ok(**(GOOD | {"thread_hook": "3시만 되면 단 거 당김"})))  # 첫 줄에 숫자 → 탈락
    assert "thread_hook" not in await c.write(other, "개당 1,038원")
    await c.write(other, "개당 1,038원")
    assert cli.count == 5


# ---------------------------------------------------------------- CLI 출력 모양이 달라도 터지지 않는다 (#36, #37)


async def test_verbose_list_output_is_understood(cli: FakeCli) -> None:
    cli.set(json.dumps([{"type": "system", "subtype": "init"}, {"type": "assistant"},
                        {"type": "result", "is_error": False, "structured_output": GOOD}], ensure_ascii=False))
    got = await _commentator(cli).write(_deal(), "개당 1,038원")
    assert got["comment"] == GOOD["comment"]


@pytest.mark.parametrize("stdout", [
    json.dumps([{"type": "system"}]), json.dumps({"is_error": False, "result": "null"}),
    json.dumps({"is_error": False, "result": "123"}), json.dumps({"is_error": False, "result": "[1, 2]"}),
    json.dumps({"is_error": False, "structured_output": {"comment": 5, "thread_hook": ["x"]}}), "123", "not json",
])
async def test_odd_cli_output_never_raises(cli: FakeCli, stdout: str) -> None:
    cli.set(stdout)
    assert await _commentator(cli).write(_deal(), "개당 1,038원") == {}


async def test_prose_answer_is_never_posted(cli: FakeCli, db: Database) -> None:
    cli.set(json.dumps({"is_error": False, "result": "정보가 부족해서 이 상품은 소개하기 어려워요."}, ensure_ascii=False))
    c = _commentator(cli, db)
    assert await c.write(_deal(), "개당 1,038원") == {}
    assert db.kv_get(c._key(_deal(), "개당 1,038원")) is None


async def test_add_commentary_survives_writer_crash(settings) -> None:  # noqa: ANN001
    from dealbot.app import DealBot
    from dealbot.cli import sample_deal

    settings.collectors = []
    bot = DealBot(settings)

    async def boom(*a: object, **k: object) -> dict:
        raise AttributeError("'list' object has no attribute 'get'")

    try:
        bot.commentator.write = boom  # type: ignore[method-assign]
        deal = sample_deal()
        await bot.add_commentary(deal)
        assert "comment" in deal.product.extra and deal.product.extra["comment"] is None  # 발행 큐가 같은 글에 다시 묻지 않음
    finally:
        await bot.close()


# ---------------------------------------------------------------- 실패가 조용히 묻히지 않는다 (#38, #43)


def test_missing_cli_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="dealbot.commentary"):
        Commentator(enabled=True)
    assert "claude CLI" in caplog.text
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="dealbot.commentary"):
        Commentator(enabled=False)
    assert caplog.text == ""


async def test_empty_output_and_timeout_are_logged(cli: FakeCli, caplog: pytest.LogCaptureFixture) -> None:
    cli.set("", rc=127, stderr="/usr/bin/env: 'node': No such file or directory")
    with caplog.at_level(logging.WARNING, logger="dealbot.commentary"):
        assert await _commentator(cli).write(_deal(), "") == {}
    assert "127" in caplog.text and "node" in caplog.text
    caplog.clear()
    cli.set(_ok(**GOOD), sleep=3)
    with caplog.at_level(logging.WARNING, logger="dealbot.commentary"):
        assert await _commentator(cli, timeout=0.5).write(_deal(), "") == {}
    assert "TimeoutError" in caplog.text


async def test_breaker_skips_after_repeated_failures_and_alerts_once(cli: FakeCli) -> None:
    cli.set(json.dumps({"is_error": True, "result": "Not logged in · Please run /login"}))
    c = _commentator(cli)
    for _ in range(3):
        assert await c.write(_deal(), "") == {}
    assert cli.count == 3
    alert = c.take_alert()
    assert alert and "login" in alert and c.take_alert() is None  # 한 번만
    assert await c.write(_deal(), "") == {} and cli.count == 3  # 잠깐 쉬는 동안은 CLI 를 안 부른다
    c._skip_until = 0  # 쉬는 시간이 지나면 다시 시도, 성공하면 초기화
    cli.set(_ok(**GOOD))
    assert (await c.write(_deal(), "개당 1,038원"))["comment"] and c.fail_streak == 0


async def test_add_commentary_alerts_admin_when_ai_keeps_failing(settings, tmp_path: Path) -> None:  # noqa: ANN001
    from dealbot.app import DealBot
    from dealbot.cli import sample_deal

    settings.collectors = []
    bot = DealBot(settings)
    sent: list[tuple[str, str]] = []

    async def notify_error(kind: str, message: str) -> None:
        sent.append((kind, message))

    try:
        cli = FakeCli(tmp_path)
        cli.set("", rc=1, stderr="boom")
        bot.commentator.enabled, bot.commentator.bin = True, str(cli.path)
        bot.notifier.notify_error = notify_error  # type: ignore[method-assign]
        for _ in range(4):
            await bot.add_commentary(sample_deal())
        assert len(sent) == 1 and sent[0][0] == "commentary" and "한줄평" in sent[0][1]
    finally:
        await bot.close()


# ---------------------------------------------------------------- 숫자 규칙: 상품명 숫자는 허용, 지어낸 숫자는 차단 (#40, #41)


def test_numbers_from_the_product_name_are_allowed() -> None:
    facts = "쿠팡 최저가 42,000원보다 29% 싸요."
    assert check_comment("1.18L 대용량이라 차에 두고 쓰기 좋아요.", facts, STANLEY)
    assert check_comment("24개 한 박스라 사무실에 두기 좋아요.", "개당 1,038원", HOLLYS)
    assert check_comment("500ml라 들고 다니기 좋아요.", "병당 148원", WATER)
    assert check_comment("1+1 구성이라 나눠 쓰기 좋아요.", "", "샴푸 1+1")
    assert check_comment("24시간 들고 다녀도 돼요.", "개당 1,038원", HOLLYS) is None  # 상품명의 24는 '개'
    assert check_comment("2L라 넉넉해요.", facts, STANLEY) is None  # 상품명에 없는 숫자


def test_invented_quantities_are_blocked() -> None:
    facts = "쿠팡 최저가 35,100원보다 29% 싸요. / 개당 1,038원"
    assert check_comment("29일 동안 한 박스면 충분해요.", facts) is None  # 29 는 % 였음
    assert check_comment("1,038일 마셔도 되겠네요.", facts) is None
    assert check_comment("두 박스 사면 한 달은 버텨요.", facts) is None
    assert check_comment("한 달은 거뜬해요.", facts) is None
    assert check_comment("반값이라 지금 사두면 좋아요.", facts) is None
    assert check_comment("반값이라 지금 사두면 좋아요.", "평소보다 55% 싸요.")  # 실제로 반값 이상이면 허용
    # 평범한 말은 그대로
    assert check_comment("한 병씩 꺼내 마시기 좋아요.", facts)
    assert check_comment("한 박스 들여놓기 좋아요.", facts)
    assert check_comment("간편한 달걀 요리용으로 좋아요.", facts)
    assert check_comment("개당 1,038원이라 박스로 쟁여두기 편해요.", facts)


# ---------------------------------------------------------------- 짧은 이름은 상품명 낱말 그대로 (#44)


def test_short_name_uses_whole_words_and_keeps_pack_count() -> None:
    assert check_short_name("스 1", "스탠리 텀블러 1.18L") is None
    assert check_short_name("스파클 라벨 생수 40병", WATER) is None  # '무라벨' → '라벨' 은 뜻이 뒤집힘
    assert check_short_name("스파클 생수 500ml", WATER) is None  # 40병 묶음인데 개수가 빠짐
    assert check_short_name("스파클 생수 40병", WATER) == "스파클 생수 40병"
    assert check_short_name("코카콜라 350ml 24캔", "코카콜라 350 ml x 24캔") == "코카콜라 350ml 24캔"
    assert check_short_name("할리스 바닐라딜라이트 24개", HOLLYS) == "할리스 바닐라딜라이트 24개"
    assert check_short_name("스탠리 퀜처 1.18L", STANLEY) == "스탠리 퀜처 1.18L"
    assert check_short_name("크리넥스 30롤", "크리넥스 3겹 데코앤소프트 30m 30롤") == "크리넥스 30롤"
    assert check_short_name("크리넥스 프리미엄", "크리넥스 3겹 30롤") is None
    assert check_short_name("비비고 왕교자 2봉", "비비고 왕교자 1.05kg x 2봉") == "비비고 왕교자 2봉"


# ---------------------------------------------------------------- /threadstest 샘플도 실제 글처럼 AI 문구 (#19)


async def test_threads_test_sample_runs_the_ai_step(settings) -> None:  # noqa: ANN001
    from dealbot.app import DealBot

    settings.collectors = []
    settings.threads.enabled = True
    bot = DealBot(settings)
    calls: list[str] = []

    async def fake_add(deal: Deal) -> None:
        calls.append(deal.product.product_id)
        deal.product.extra.update(comment=None, thread_hook="텀블러는 꼭 출근길에 두고 나옴", short_name="스탠리 퀜처 1.18L")

    try:
        bot.add_commentary = fake_add  # type: ignore[method-assign]
        bot.threads.stored_token = lambda: object()  # type: ignore[method-assign]
        bot.threads.dry_run = True
        preview = await bot.threads_test()
        assert calls == ["coupang:0000000"]
        assert "텀블러는 꼭 출근길에 두고 나옴" in preview and "근데 스탠리 퀜처 1.18L 29,900원\n" in preview and "원임" not in preview
    finally:
        await bot.close()
