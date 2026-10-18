"""쇼핑 커넥트 짧은 검색어 (주인 10/18: '검색어 칸에 짧은 검색어(브랜드 + 핵심 단어)를 같이 보여주기').

쇼핑 커넥트 '상품 찾기'는 판매처가 붙인 상품명으로 찾아서, 게시판 제목 그대로(긴 이름)는 0건이 잦다
(10/18 '폴햄 여성 에코베로 라운드 긴팔티' → 0건). 규칙으로만 브랜드 + 핵심 낱말 2~4개를 만든다 (AI 안 부름).
대시보드 자세히 시트 '짧게' 줄 · 텔레그램 링크 요청 · 카톡(=앱 푸시) 한 통에 같이 보인다. 가짜만 씀 (네트워크 없음).
"""

from __future__ import annotations

import json
import time

import pytest

from dealbot.config import Settings
from dealbot.models import Deal, DealVerdict, Product
from dealbot.monitoring.admin import naver_search_help, naver_short_query
from dealbot.naver_shop import short_query


@pytest.mark.parametrize(
    ("name", "want"),
    [
        ("폴햄 여성 에코베로 라운드 긴팔티", "폴햄 에코베로 긴팔티"),  # 10/18 0건이던 이름
        ("[네이버] 폴햄 여성 에코베로 라운드 긴팔티 (9,900원/무료)", "폴햄 에코베로 긴팔티"),  # 쇼핑몰 꼬리표·괄호
        ("[폴햄] 여성 에코베로 라운드 긴팔티 블랙 M 3종", "폴햄 에코베로 긴팔티"),  # 앞머리 괄호 안 브랜드 · 색 · 크기 · 개수
        ("[네이버] 데켓 주물 IH 오발쿡플레이트 31cm (무료배송)", "데켓 주물 IH 오발쿡플레이트"),
        ("삼성전자 갤럭시 버즈3 프로 블루투스 이어폰 SM-R630 화이트", "삼성전자 갤럭시 버즈3 이어폰"),  # 모델 번호 · 4낱말까지
        ("[무배] 마이 샴푸 1000ml 2개 (~9/13)", "마이 샴푸"),
        ("농심 신라면 120g x 40봉", "농심 신라면"),
        ("코멧 3겹 데코 화장지 30m 30롤 x 2팩", "코멧 데코 화장지"),
        ("나이키 남성 에어맥스 운동화 FD1234-100", "나이키 에어맥스 운동화"),
        ("[브랜드스토어] 노스페이스 키즈 따뜻한 패딩 점퍼 NJ1DQ50", "노스페이스 패딩 점퍼"),  # 꾸미는 말
        ("1+1 쿠쿠 6인용 전기밥솥 CRP-P0660FD", "쿠쿠 전기밥솥"),  # 덤 · 인용 · 모델 번호
        ("특가 정품 무료배송", ""),  # 남는 낱말이 없으면 '' (짧게 줄을 안 보임)
    ],
)
def test_short_query_rules(name: str, want: str) -> None:
    got = short_query(name)
    assert got == want
    assert not got or 2 <= len(got.split()) <= 4


def test_category_leaf_fills_a_lonely_brand() -> None:
    assert short_query("폴햄 블랙 M") == ""
    assert short_query("폴햄 블랙 M", "패션의류>여성의류>티셔츠") == "폴햄 티셔츠"


def test_short_query_only_for_naver_and_only_when_shorter() -> None:
    assert naver_short_query("폴햄 여성 에코베로 라운드 긴팔티", "coupang") == ""
    assert naver_short_query("폴햄 여성 에코베로 라운드 긴팔티", "naver") == "폴햄 에코베로 긴팔티"
    assert naver_short_query("농심 신라면", "naver") == ""  # 긴 검색어와 같으면 두 번 안 보임
    help_text = naver_search_help("폴햄 여성 에코베로 라운드 긴팔티", "https://smartstore.naver.com/paulham/products/1", "naver")
    assert "<code>폴햄 여성 에코베로 라운드 긴팔티</code>" in help_text and "짧게: <code>폴햄 에코베로 긴팔티</code>" in help_text


@pytest.fixture
def bot(settings: Settings):  # type: ignore[no-untyped-def]
    from dealbot.app import DealBot

    settings.collectors = []
    b = DealBot(settings)
    yield b
    b.db.close()


def _waiting(bot, name: str = "[네이버] 폴햄 여성 에코베로 라운드 긴팔티 (9,900원)") -> int:  # type: ignore[no-untyped-def]
    p = Product(source="ppomppu", product_id="naver:111", shop="naver", name=name, price=9900,
                url="https://smartstore.naver.com/paulham/products/111")
    assert bot.db.enqueue(Deal(p, DealVerdict(is_deal=True)), score=1)
    qid = bot.db.pending_items()[0].id
    bot.db.update_queue_item(qid, status="awaiting_link", error="manual link required")
    return qid


async def test_link_request_kakao_and_telegram_carry_the_short_query(bot, settings: Settings) -> None:  # type: ignore[no-untyped-def]
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "blogauto_status.json").write_text(
        json.dumps({"at": int(time.time()), "kakao_ok": True, "kakao_checked": 0, "outbox": 0}), encoding="utf-8")
    sent: list[str] = []

    async def fake_send(text: str, **_kw: object) -> int:
        sent.append(text)
        return len(sent)

    bot.notifier.send_with_id = fake_send
    qid = _waiting(bot)
    await bot.notifier.notify_manual_link(bot.db.get_queue_item(qid), bot.registry.get("naver"))
    assert "짧게: <code>폴햄 에코베로 긴팔티</code>" in sent[-1]
    rows = [json.loads(x) for x in (settings.data_dir / "blogauto_inbox.jsonl").read_text(encoding="utf-8").splitlines()]
    text = [r["owner_notice"] for r in rows if "owner_notice" in r][-1]["text"]
    assert "🔎 짧게: 폴햄 에코베로 긴팔티" in text and len(text) <= 200  # 카톡(=앱 푸시) 한 통 둘째 줄 (잘리지 않는 쪽)


def test_dashboard_detail_has_a_short_row(bot) -> None:  # type: ignore[no-untyped-def]
    from dealbot.dashboard.state import StateBuilder

    qid = _waiting(bot)
    it = next(x for x in StateBuilder(bot).items() if x["iid"].endswith(str(qid)) and x["kind"] == "link_request")
    assert it["detail"]["search"] == "폴햄 여성 에코베로 라운드 긴팔티"
    assert it["detail"]["short"] == "폴햄 에코베로 긴팔티"
