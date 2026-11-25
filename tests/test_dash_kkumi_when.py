"""'꾸미기' 책갈피 v6 — 예약 시각 자동 채움 (주인 11/14 — 글마다 발행 설정에서 날짜·시각을 고르는 수고를 없앰 · 발행 단추는 주인이 직접).

1) kkumi.parse_when · pick_when · when_plan: Blog-Auto 가 고른 추천 시각 그대로 (분이 10·20·40·50 아니면 다음 자리로) · 없거나 다가왔으면 규칙
   (주인 11/24 예약 시각 원칙 — autopub 계획기: 섞은 간격 · 분 10·20·40·50 · 07:30~23:00 · 같은 블로그 40분 · 다른 블로그 20분).
2) kkumi.stamp: 글 끝 태그 줄 가운데에 '@YYYY-MM-DDTHH:MM' — 옛 책갈피(v5)·Blog-Auto 가 읽는 첫 칸(카테고리)·끝 칸(태그)은 그대로.
3) routine.view: 예약 줄에 when + 스탬프한 html · 발행 줄은 그대로 · routine.js 안내 한 마디.
4) 가짜 발행 설정창(진짜 크롬, playwright 가 있을 때만): '예약'을 고르고 날짜(달력 · 다음 달)·시·분(10분 단위로 내림)을 채운 뒤 멈춤 —
   발행 단추는 한 번도 누르지 않음 · 예약 칸이 없거나 모양이 다르면 아무것도 바꾸지 않고 '못 채움 — 직접 골라요' 안내만 · 꾸미기 본 일은 그대로.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from dash_helpers import push_settings
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.dashboard import kkumi, routine
from test_dash_kkumi import INNER, NODE, RUN, _playwright_env, _tags_line, code
from test_dash_routine import SLOTS, at, item, with_routine

STATIC = Path(__file__).resolve().parents[1] / "src" / "dealbot" / "dashboard" / "static"
D = datetime


# ---------------------------------------------------------------- 1) 시각 계산
def test_parse_when_reads_blogauto_labels() -> None:
    day = "2026-11-14"
    assert kkumi.parse_when("19:30", day) == D(2026, 11, 14, 19, 30)
    assert kkumi.parse_when("9:00", day) == D(2026, 11, 14, 9, 0)
    assert kkumi.parse_when("내일 09:00", day) == D(2026, 11, 15, 9, 0)
    assert kkumi.parse_when("11/16(월) 07:30", day) == D(2026, 11, 16, 7, 30)
    assert kkumi.parse_when("11/16 07:30", day) == D(2026, 11, 16, 7, 30)
    assert kkumi.parse_when("1/2(토) 09:00", "2026-12-30") == D(2027, 1, 2, 9, 0)  # 해 넘김
    for bad in ("", None, "바로", "25:00", "13/40(월) 09:00", "오늘 저녁", 7):
        assert kkumi.parse_when(bad, day) is None, bad
    assert kkumi.parse_when("19:30", "날짜 아님") is None


def _ok_rule(ds: list[datetime]) -> None:
    for d in ds:
        assert d.minute in (10, 20, 40, 50) and (7, 30) <= (d.hour, d.minute) <= (23, 0), d  # 주인 11/24 — 정각·30분 없음 · 사람 시간 (토·일도 같게)


def test_pick_when_follows_the_owner_reserve_principle() -> None:
    now = D(2026, 11, 14, 10, 7)
    for seed in range(40):
        mine, others = [D(2026, 11, 14, 11, 20), D(2026, 11, 14, 14, 40)], [D(2026, 11, 14, 12, 10)]
        ds = kkumi.pick_when("issue", now, mine, others, 4, seed=str(seed))
        _ok_rule(ds)
        assert ds == sorted(ds) and ds[0] >= D(2026, 11, 14, 10, 50)  # '지금'을 10:30 으로 올리고 20~45분 뒤부터
        assert all(abs(a - b) > timedelta(minutes=40) for a in ds for b in [*mine, *(x for x in ds if x != a)])  # 같은 블로그 40분 넘게
        assert all(a.date() != b.date() or abs(a - b) > timedelta(minutes=20) for a in ds for b in others)  # 다른 블로그 20분 넘게
        assert all(a.minute != b.minute for a, b in zip(ds, ds[1:], strict=False))  # 이웃 글과 같은 분 아님
        assert kkumi.pick_when("issue", now + timedelta(minutes=15), mine, others, 4, seed=str(seed)) == ds  # 30분 안에는 같은 값
    # 들쭉날쭉 (글 묶음마다 다른 모양) · 늦은 밤이면 다음 날 아침 07:30~ · 토요일도 그날 (주인 11/24)
    assert len({tuple(kkumi.pick_when("issue", now, [], [], 3, seed=str(s))) for s in range(10)}) > 5
    late = kkumi.pick_when("issue", D(2026, 11, 14, 22, 50), [], [], 2)
    _ok_rule(late)
    assert all(d.date() == D(2026, 11, 15).date() for d in late)
    sat = kkumi.pick_when("issue", D(2026, 11, 28, 10, 0), [], [], 3)
    assert all(d.weekday() == 5 for d in sat)
    assert not hasattr(kkumi, "WHEN_TIMES") and not hasattr(kkumi, "WHEN_GAP_MIN") and not hasattr(kkumi, "humanize")  # 옛 둥근 후보표는 지움


def row(i: str, when: str | None, *, blog: str = "issue", **kw: object) -> dict:
    copy = [{"label": "글 제목", "text": "제목"}] + ([{"label": "예약 시각", "text": when}] if when else [])
    return {"id": i, "group": "예약", "blog": blog, "title": f"데런픽 예약 · {i}", "copy": copy, "done": False,
            "paste": {"stale": False, "html": "<p>글</p>", "text": "글"}, **kw}


def near(at: str, base: str | None = None, rule: bool | None = None, got: dict | None = None) -> bool:
    """주인 11/24 원칙의 시각: 분 10·20·40·50 · 07:30~23:00 · 초 없음 · base 가 있으면 그 시각 그대로."""
    d = D.fromisoformat(at)
    ok = d.minute in (10, 20, 40, 50) and (7, 30) <= (d.hour, d.minute) <= (23, 0) and len(at) == 16 and (base is None or at == base)
    return ok and (got is None or (got["at"] == at and got["label"] == f"{d.month}/{d.day} {d:%H:%M}" and got["rule"] is rule))


def test_when_plan_keeps_the_recommended_time_and_falls_back_to_the_rule() -> None:
    now = D(2026, 11, 14, 10, 0)
    rows = [row("rsv:issue:1", "12:20"), row("rsv:issue:2", "16:00"), row("rsv:issue:3", "내일 09:10"),
            row("rsv:issue:4", None),  # 추천 시각이 없음 → 규칙
            row("rsv:issue:5", "09:00"),  # 이미 지남 → 규칙
            row("rsv:finance:6", None, blog="finance"),  # 다른 블로그는 따로 셈 (같은 날 20분 넘게)
            row("rsv:finance:8", "11:50", blog="finance"),
            {**row("pub:issue:7", "12:20"), "group": "발행"}]  # 발행 줄(바로 올리는 글)은 채우지 않음
    got = kkumi.when_plan(rows, "2026-11-14", now)
    # Blog-Auto 가 고른 시각(11/24 원칙 — 이미 들쭉날쭉)은 그대로 · 옛 둥근 시각(16:00)은 다음 10·20·40·50 자리로
    for k, want in (("rsv:issue:1", "2026-11-14T12:20"), ("rsv:issue:2", "2026-11-14T16:10"), ("rsv:issue:3", "2026-11-15T09:10"), ("rsv:finance:8", "2026-11-14T11:50")):
        assert near(got[k]["at"], want, False, got[k]), (k, got[k])
    for k in ("rsv:issue:4", "rsv:issue:5", "rsv:finance:6"):
        assert near(got[k]["at"], None, True, got[k]), (k, got[k])
    assert "pub:issue:7" not in got and len(got) == 7
    assert kkumi.when_plan(rows, "2026-11-14", now + timedelta(minutes=1)) == kkumi.when_plan(rows, "2026-11-14", now + timedelta(minutes=29))  # 30분 안에는 같은 값
    ts = {k: D.fromisoformat(v["at"]) for k, v in got.items()}
    iss = sorted(v for k, v in ts.items() if ":issue:" in k)
    assert all(b - a > timedelta(minutes=40) for a, b in zip(iss, iss[1:], strict=False)), iss  # 같은 블로그 40분 넘게
    assert all(a.date() != b.date() or abs(a - b) > timedelta(minutes=20) for a in iss for k, b in ts.items() if ":finance:" in k)  # 블로그끼리 20분 넘게
    assert ts["rsv:issue:4"] < ts["rsv:issue:5"] and ts["rsv:issue:4"] >= now + timedelta(minutes=20)


def test_unstamp_takes_the_time_out_again() -> None:
    # 지금 발행할 글: 태그 줄의 시각 칸을 뺌 (unstamp) — 다시 실으면 그대로
    line = kkumi.MARK_TAGS + kkumi._bits("셀럽 소식\x01가 나")
    st = kkumi.stamp("<p>글</p>" + line, "2026-11-16T07:10")
    assert kkumi.stamped(st) == "2026-11-16T07:10" and kkumi.unstamp(st) == "<p>글</p>" + line and kkumi.unstamp("<p>글</p>" + line) == "<p>글</p>" + line


def test_when_plan_skips_rows_that_cannot_be_pasted_but_avoids_their_times() -> None:
    now = D(2026, 11, 14, 10, 0)
    rows = [row("rsv:issue:1", "12:20", done=True),  # 이미 예약한 글 — 다시 채우지 않지만 그 시각은 피함
            row("rsv:issue:2", "16:10", paste={"stale": True, "html": "", "text": ""}),  # 만드는 중
            row("rsv:issue:3", "19:40", paste=None),
            row("rsv:issue:4", None),
            {**row("rsv:issue:5", "08:20"), "title": "라커룸 붙여넣기 + 바로 발행 · 글"},  # 시각이 지난 새 채널 글 — 바로 발행
            row("rsv:issue:6", "10:10")]  # 20분 안으로 다가옴 → 규칙
    for seed_now in (now, now + timedelta(hours=3, minutes=5), now + timedelta(hours=8, minutes=40)):
        got = kkumi.when_plan(rows, "2026-11-14", seed_now)
        assert set(got) == {"rsv:issue:4", "rsv:issue:6"}
        taken = [D(2026, 11, 14, 12, 20), D(2026, 11, 14, 16, 10), D(2026, 11, 14, 19, 40)]
        for k in ("rsv:issue:4", "rsv:issue:6"):
            d = D.fromisoformat(got[k]["at"])
            assert near(got[k]["at"], None, True, got[k]) and all(abs(d - x) > timedelta(minutes=40) for x in taken), (seed_now, got)
    assert kkumi.when_plan([], "2026-11-14", now) == {} and kkumi.when_plan([{"id": "x"}, "글"], "2026-11-14", now) == {}  # type: ignore[list-item]


# ---------------------------------------------------------------- 2) 태그 줄에 싣기
def _old_reader(line_html: str) -> tuple[str, list[str]]:
    """옛 책갈피(v5) tagsOf · Blog-Auto paste.tags_from_line/category_from_line 이 읽는 법: 첫 칸 = 카테고리 · 끝 칸 = 태그."""
    data = kkumi._unbits(line_html) or ""
    a = data.split("\x01")
    tags = a.pop()
    return (a[0] if a else ""), [t for t in tags.replace(",", " ").replace("#", " ").split() if t]


def test_stamp_puts_the_time_between_category_and_tags() -> None:
    body = "<p>첫 글</p>"
    with_cat = body + _tags_line(["아일릿", "키링"], "셀럽 소식")
    got = kkumi.stamp(with_cat, "2026-11-15T19:00")
    assert got != with_cat and got.startswith(body + "<p>⁤") and got.endswith("</p>") and kkumi.stamped(got) == "2026-11-15T19:00"
    assert _old_reader(got) == ("셀럽 소식", ["아일릿", "키링"]) == _old_reader(with_cat)  # 옛 책갈피·Blog-Auto 는 그대로 읽음
    assert kkumi._unbits(got) == "셀럽 소식\x01@2026-11-15T19:00\x01아일릿 키링"
    again = kkumi.stamp(got, "2026-11-16T09:00")  # 다시 실으면 바꿔 씀 (칸이 늘지 않음)
    assert kkumi.stamped(again) == "2026-11-16T09:00" and kkumi._unbits(again) == "셀럽 소식\x01@2026-11-16T09:00\x01아일릿 키링"
    # 카테고리 없는 옛 태그 줄 ('태그들'만)
    bits = "".join(f"{b:08b}" for b in "가 나".encode()).replace("0", "‌").replace("1", "‍")
    plain = kkumi.stamp(f"<p>글</p><p>⁤{bits}</p>", "2026-11-15T19:00")
    assert kkumi._unbits(plain) == "\x01@2026-11-15T19:00\x01가 나" and _old_reader(plain) == ("", ["가", "나"])
    # 태그 줄이 없거나 시각 모양이 틀리면 그대로 (새 줄을 만들지 않음)
    assert kkumi.stamp(body, "2026-11-15T19:00") == body and kkumi.stamped(body) == "" and kkumi.stamped(with_cat) == ""
    for bad in ("", "19:00", "2026-11-15 19:00", "2026-11-15T19:00:00", "내일"):
        assert kkumi.stamp(with_cat, bad) == with_cat, bad
    assert kkumi.stamp("⁤‌‍‍", "2026-11-15T19:00").startswith("⁤")  # 깨진 줄도 예외 없이


# ---------------------------------------------------------------- 3) 할 일 칸
@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    settings.secrets.public_domain = "bot.example.com"
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


def _paste(title: str, tags: bool = True) -> dict:
    return {"title": title, "html": "<p>본문이에요.</p>" + (_tags_line(["태그"], "셀럽 소식") if tags else ""), "tags": ["태그"], "cards": 0, "photos": 0}


def test_view_stamps_reserve_rows_only(bot: DealBot, tmp_path: Path) -> None:
    day = "2026-11-14"
    when = [{"label": "글 제목", "text": "글"}, {"label": "예약 시각", "text": "19:40"}]
    with_routine(tmp_path, {"day": day, "slots": SLOTS, "overflow": 0, "items": [
        item("rsv:issue:11", "s1", group="예약", blog="issue", title="데런픽 예약 · 글", copy=when, paste=_paste("글 11")),
        item("rsv:issue:12", "s1", group="예약", blog="issue", title="데런픽 예약 · 둘", copy=when[:1], paste=_paste("글 12")),
        item("rsv:issue:13", "s1", group="예약", blog="issue", title="데런픽 예약 · 셋", copy=when, paste=_paste("글 13", tags=False)),
        item("pub:finance:14", "s1", group="발행", title="아카이브 발행 · 글", copy=when, paste=_paste("글 14"))]})
    v = routine.view(bot, now=at(bot, day, "10:00"))
    got = {i["id"]: i for s in v["slots"] for i in s["items"]}
    a, b, c, d = (got[k] for k in ("rsv:issue:11", "rsv:issue:12", "rsv:issue:13", "pub:finance:14"))
    assert near(a["when"]["at"], "2026-11-14T19:40", False, a["when"])  # Blog-Auto 추천 시각 그대로 (11/24 원칙 시각)
    assert kkumi.stamped(a["paste"]["html"]) == a["when"]["at"] == kkumi.stamped(a["paste"]["text"])
    bd = D.fromisoformat(b["when"]["at"])
    assert near(b["when"]["at"], None, True, b["when"]) and D(2026, 11, 14, 10, 20) <= bd and abs(bd - D(2026, 11, 14, 19, 40)) > timedelta(minutes=40)  # 규칙
    assert kkumi.stamped(b["paste"]["html"]) == b["when"]["at"]
    assert "when" not in c and kkumi.stamped(c["paste"]["html"]) == ""  # 태그 줄이 없는 글은 실을 곳이 없음 — 줄에도 안 보임
    assert "when" not in d and kkumi.stamped(d["paste"]["html"]) == ""  # 발행 줄은 그대로
    # 다음에 읽어도 같은 값 (기록 파일에서 읽은 줄을 고쳐 쓰지 않음 — 스탬프가 겹쳐 쌓이지 않음)
    v2 = routine.view(bot, now=at(bot, day, "10:01"))
    a2 = next(i for s in v2["slots"] for i in s["items"] if i["id"] == "rsv:issue:11")
    assert a2["paste"]["html"] == a["paste"]["html"] and kkumi._unbits(a2["paste"]["html"]).count("@") == 1
    # 추천 시각이 지난 뒤에 보면 규칙으로 (다음 빈 칸)
    late = {i["id"]: i for s in routine.view(bot, now=at(bot, day, "19:25"))["slots"] for i in s["items"]}
    l1, l2 = (late[k]["when"] for k in ("rsv:issue:11", "rsv:issue:12"))
    assert l1["rule"] is True and near(l1["at"]) and near(l2["at"]) and D.fromisoformat(l1["at"]) >= D(2026, 11, 14, 19, 45)
    assert abs(D.fromisoformat(l1["at"]) - D.fromisoformat(l2["at"])) > timedelta(minutes=40)


@pytest.mark.skipif(shutil.which("node") is None, reason="node 없음")
def test_routine_js_tells_the_time_the_bookmarklet_will_fill(bot: DealBot, tmp_path: Path) -> None:
    from test_dash_paste import _run

    day = "2026-11-14"
    with_routine(tmp_path, {"day": day, "slots": SLOTS, "overflow": 0, "items": [
        item("rsv:issue:11", "s1", group="예약", blog="issue", title="데런픽 예약 · 글", copy=[{"label": "예약 시각", "text": "19:30"}], paste=_paste("글 11")),
        item("rsv:issue:12", "s1", group="예약", blog="issue", title="데런픽 예약 · 둘", paste=_paste("글 12")),
        item("pub:finance:14", "s1", group="발행", title="아카이브 발행 · 글", paste=_paste("글 14"))]})
    v = routine.view(bot, now=at(bot, day, "10:00"))
    w = {i["id"]: i["when"]["label"] for s in v["slots"] for i in s["items"] if i.get("when")}
    got = _run(shutil.which("node"), v, "rich")
    hints = sorted(h for hs in got["hint"] for h in hs)
    assert len(hints) == 3 and sum("예약" in h for h in hints) == 2, hints
    assert any(h.endswith(f"(태그도 저절로) · 예약 {w['rsv:issue:11']} 도 채워 줘요 — 발행 단추만 직접") for h in hints), hints
    assert any(h.endswith(f"(태그도 저절로) · 예약 {w['rsv:issue:12']} (새로 고름) 도 채워 줘요 — 발행 단추만 직접") for h in hints), hints


def test_dashboard_texts_mention_the_auto_fill() -> None:
    rt = (STATIC / "routine.js").read_text(encoding="utf-8")
    assert "it.when ?" in rt and "도 채워 줘요" in rt and "발행 단추만 직접" in rt and "새로 고름" in rt
    assert "예약 시각 자동 채움" in (STATIC / "logins.js").read_text(encoding="utf-8")
    assert "예약 날짜·시각도 채워 줘요" in kkumi.USE and "발행 단추는 직접" in kkumi.USE
    assert kkumi.version() == 13


def test_bookmarklet_never_names_or_presses_the_final_publish_button() -> None:
    js = code()
    book = js[js.index("p2=n=>"):js.index("tagsOf=t=>")]  # 예약 채움 코드
    # 발행을 끝내는 단추를 찾는 선택자가 없고, 누르는 곳은 모두 tap 을 거친다 — 누를 것이나 그것을 감싼 단추·링크의 이름에
    # confirm·publish·submit 이 들었거나 종류가 submit 이거나 글자에 발행·확인·등록이 있으면 멈춤
    for word in ("confirm_btn", "seOnePublishBtn", "button[class*='confirm']", "requestSubmit", ".submit(", ".click("):
        assert word not in js, word
    assert ("let b=e.closest('button,a')||e;if(/confirm|publish|submit/i.test(b.className+b.getAttribute('data-testid')+b.type)"
            "||/발행|확인|등록/.test(b.innerText))throw 0;") in book
    assert book.count("press(") == 2 and "press(e);" in book and "press(radio(box,'현재'))" in book  # tap 안 한 번 + 되돌릴 때 '현재' 한 번
    assert "'예약'" in book and "'현재'" in book and "fetch(" not in book
    assert "await S(400+Math.random()*400);" in book  # 클릭 사이 사람 속도


# ---------------------------------------------------------------- 4) 가짜 발행 설정창
# 발행 설정창에 예약 칸을 더한다 (공개 자료로 아는 모양: 현재/예약 라디오 · 날짜 칸(읽기 전용, 누르면 달력) · 시 00~23 · 분 10분 단위 · 맨 아래 발행 단추).
# MODE: full = 그대로 · late = 예약 칸·달력이 늦게 그려짐 · nosel = 시·분이 고르기 칸(select)이 아님 · nocal = 날짜 칸을 눌러도 달력이 안 뜸 ·
#   trap = 날짜 칸보다 먼저 날짜 글자가 든 단추가 있는데 누르면 발행됨 (이름에는 표시가 없고 글자에만 '발행').
# v9 (11/18 다음 날 예약 '칸 못 찾음') — jQuery UI 달력처럼: jq = 달력이 발행창 밖(문서 끝 #ui-datepicker-div)에 붙고 날짜 칸에 커서가
#   가면(focus) 뜸 · 누름은 칸(td)이 받음 · 다른 달 칸은 빈칸 · 날짜를 고르면 발행창이 시·분 고르기 칸과 라디오 글자를 새로 그림(시는 0) ·
#   toggle = jq 인데 날짜 칸을 누르면 열림/닫힘이 바뀜 (커서로 열린 달력을 누름이 닫음) · wrongday = jq 인데 고른 날 다음 날이 들어감.
RSV = r"""<script>
window.rsvLog = {confirm: 0, radio: 0};
document.querySelector('.publish_btn').addEventListener('click', () => {
  const tg = document.getElementById('tg');
  if (!tg || tg.querySelector('.option_time__x')) return;
  const MODE = '__MODE__', now = new Date(), w = document.createElement('div');
  let sel = [now.getFullYear(), now.getMonth() + 1, now.getDate()], view = [sel[0], sel[1]];
  w.className = 'option_time__x';
  w.innerHTML = '<span><input type="radio" id="radio_time1" name="radio_time" checked><label for="radio_time1" class="radio_label__m">현재</label></span>'
    + '<span><input type="radio" id="radio_time2" name="radio_time"><label for="radio_time2" class="radio_label__m">예약</label></span>'
    + '<div class="time_setting__v" style="display:none">'
    + (MODE === 'trap' ? '<button type="button" class="date_view__k"></button>' : '') + '<input class="input_date__Q" readonly>'
    + (MODE === 'nosel' ? '<div class="hour_drop">09시</div><div class="min_drop">00분</div>' : '<select class="hour_option__J"></select>시 <select class="minute_option__V"></select>분')
    + '</div><div class="ui-datepicker" style="display:none"></div><button class="confirm_btn__W" data-testid="seOnePublishBtn">발행</button>';
  tg.append(w);
  const jq = /jq|toggle|wrongday/.test(MODE), q = s => w.querySelector(s), p2 = n => String(n).padStart(2, '0'), inp = q('.input_date__Q');
  const cal = jq ? document.body.appendChild(Object.assign(document.createElement('div'), {id: 'ui-datepicker-div', className: 'ui-datepicker', style: 'display:none;position:absolute'})) : q('.ui-datepicker');
  const show = () => { inp.value = sel[0] + '. ' + p2(sel[1]) + '. ' + p2(sel[2]); if (MODE === 'trap') q('.date_view__k').innerHTML = inp.value + ' <b>에 발행</b>'; };
  if (MODE === 'trap') q('.date_view__k').onclick = () => { window.rsvLog.confirm++; };
  show();
  if (MODE !== 'nosel') {
    q('.hour_option__J').innerHTML = [...Array(24)].map((_, i) => '<option value="' + i + '">' + p2(i) + '</option>').join('');
    q('.minute_option__V').innerHTML = [0, 10, 20, 30, 40, 50].map(i => '<option value="' + i + '">' + p2(i) + '</option>').join('');
    q('.hour_option__J').value = '9';
  }
  // late (하루치 v5 · 주인 11/23 14:00 '예약 11/24 08:50 못 채움 (날짜 칸 못 찾음)' — 발행창은 '현재'): '예약'을 골라도 날짜·시·분 칸이 1.2초 뒤에 그려지고 달력도 0.9초 뒤에 뜸
  for (const r of w.querySelectorAll('input[type=radio]')) r.addEventListener('change', () => { window.rsvLog.radio++; const on = q('#radio_time2').checked, f = () => { q('.time_setting__v').style.display = on ? 'block' : 'none'; }; if (MODE === 'late' && on) setTimeout(f, 1200); else f(); });
  q('.confirm_btn__W').onclick = () => { window.rsvLog.confirm++; };
  const opts = () => {
    q('.hour_option__J').innerHTML = [...Array(24)].map((_, i) => '<option value="' + i + '">' + p2(i) + '</option>').join('');
    q('.minute_option__V').innerHTML = [0, 10, 20, 30, 40, 50].map(i => '<option value="' + i + '">' + p2(i) + '</option>').join('');
  };
  // jq: 날짜를 고르면 시·분 고르기 칸을 새 칸으로 바꾸고(시 0) 라디오 글자도 새로 그림 (옛 칸을 붙잡고 있으면 못 씀)
  const redraw = () => {
    for (const c of w.querySelectorAll('select')) c.replaceWith(Object.assign(document.createElement('select'), {className: c.className}));
    opts();
    for (const l of w.querySelectorAll('label')) l.replaceWith(l.cloneNode(true));
  };
  const jqDraw = () => {
    const [y, m] = view, first = new Date(y, m - 1, 1), start = new Date(y, m - 1, 1 - first.getDay()), today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    let h = '<div class="ui-datepicker-header"><a class="ui-datepicker-prev" data-handler="prev" title="이전달"><span>이전달</span></a><a class="ui-datepicker-next" data-handler="next" title="다음달"><span>다음달</span></a>'
      + '<div class="ui-datepicker-title"><span class="ui-datepicker-year">' + y + '</span>년&nbsp;<span class="ui-datepicker-month">' + m + '월</span></div></div>'
      + '<table class="ui-datepicker-calendar"><thead><tr>' + '일월화수목금토'.split('').map(x => '<th>' + x + '</th>').join('') + '</tr></thead><tbody>';
    for (let r = 0; r < 6; r++) {
      h += '<tr>';
      for (let c = 0; c < 7; c++) {
        const d = new Date(start.getFullYear(), start.getMonth(), start.getDate() + r * 7 + c);
        h += d.getMonth() !== m - 1 ? '<td class="ui-datepicker-other-month ui-datepicker-unselectable ui-state-disabled">&nbsp;</td>'
          : d < today ? '<td class="ui-datepicker-unselectable ui-state-disabled"><span class="ui-state-default">' + d.getDate() + '</span></td>'
          : '<td class=" " data-handler="selectDay" data-event="click" data-month="' + (m - 1) + '" data-year="' + y + '"><a class="ui-state-default" href="#">' + d.getDate() + '</a></td>';
      }
      h += '</tr>';
    }
    cal.innerHTML = h + '</tbody></table>';
    cal.querySelector('.ui-datepicker-next').onclick = () => { view = m === 12 ? [y + 1, 1] : [y, m + 1]; jqDraw(); };
    for (const td of cal.querySelectorAll('td[data-handler]')) td.onclick = e => {
      e.preventDefault();
      sel = [y, m, +td.textContent + (MODE === 'wrongday' ? 1 : 0)]; show(); cal.style.display = 'none'; redraw();
    };
  };
  const draw = () => {
    const [y, m] = view, first = new Date(y, m - 1, 1), start = new Date(y, m - 1, 1 - first.getDay()), today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    let h = '<div class="ui-datepicker-header"><a class="ui-datepicker-prev" title="이전">‹</a><div class="ui-datepicker-title"><span class="ui-datepicker-year">' + y + '</span>년 <span class="ui-datepicker-month">' + m + '월</span></div><a class="ui-datepicker-next" title="다음">›</a></div><table class="ui-datepicker-calendar"><tbody>';
    for (let r = 0; r < 6; r++) {
      h += '<tr>';
      for (let c = 0; c < 7; c++) {
        const d = new Date(start.getFullYear(), start.getMonth(), start.getDate() + r * 7 + c), other = d.getMonth() !== m - 1;
        h += other || d < today ? '<td class="' + (other ? 'ui-datepicker-other-month ' : '') + 'ui-state-disabled"><span>' + d.getDate() + '</span></td>' : '<td><a class="ui-state-default" data-d="' + d.getDate() + '">' + d.getDate() + '</a></td>';
      }
      h += '</tr>';
    }
    cal.innerHTML = h + '</tbody></table>';
    cal.querySelector('.ui-datepicker-next').onclick = () => { view = m === 12 ? [y + 1, 1] : [y, m + 1]; draw(); };
    cal.querySelector('.ui-datepicker-prev').onclick = () => { view = m === 1 ? [y - 1, 12] : [y, m - 1]; draw(); };
    for (const a of cal.querySelectorAll('a[data-d]')) a.onclick = () => { sel = [y, m, +a.dataset.d]; show(); cal.style.display = 'none'; };
  };
  // v13 (11/25 하루치 '예약 11/26 08:10 못 채움 (날짜 칸 못 찾음)' — 진짜 달력 모양은 아직 못 봄): 다른 모양 넷
  //   nj = 문서 끝 position:fixed 표 · 칸 안 button · 머리 '2026.11' · aria-disabled="false" · 다른 달 날도 숫자(ui-datepicker-other-month)
  //   div = 표 없는 div 격자 · 머리 '2026년 11월' · 지난날은 disabled 단추 · 다른 달 날은 표시 없는 숫자 (앞·뒤) · 다음 단추는 aria-label '다음 달'
  //   ysel = jQuery UI changeMonth/changeYear (머리에 월 고르기 칸 12개·년 고르기 칸 2016~2036) · edit = 달력 없이 손으로 쓰는 날짜 칸 (change 로 읽음)
  if (/^(nj|div|ysel|edit)$/.test(MODE)) {
    const c2 = document.body.appendChild(Object.assign(document.createElement('div'), {className: MODE === 'div' ? 'cal_wrap__z' : 'ui-datepicker', style: 'display:none;' + (MODE === 'nj' ? 'position:fixed;top:0;left:0;background:#fff' : '')}));
    if (MODE === 'edit') { inp.readOnly = false; inp.addEventListener('change', () => { const a = /(\d{4})\D+(\d+)\D+(\d+)/.exec(inp.value); if (a) { sel = [+a[1], +a[2], +a[3]]; redraw(); } }); }
    const d2 = () => {
      const [y, m] = view, start = new Date(y, m - 1, 1 - new Date(y, m - 1, 1).getDay()), today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
      let h = MODE === 'div' ? '<div class="head__z"><button type="button" class="nav__z" aria-label="이전 달">‹</button><strong>' + y + '년 ' + m + '월</strong><button type="button" class="nav__z nx" aria-label="다음 달">›</button></div><div class="grid__z">'
        : MODE === 'nj' ? '<div class="ui-datepicker-header"><a class="ui-datepicker-prev" title="Prev">‹</a><a class="ui-datepicker-next nx" title="Next">›</a><div class="ui-datepicker-title"><span class="ui-datepicker-year">' + y + '</span>.<span class="ui-datepicker-month">' + p2(m) + '</span></div></div><table class="ui-datepicker-calendar"><tbody>'
        : '<div class="ui-datepicker-header"><a class="ui-datepicker-next nx" title="다음달"><span>다음달</span></a><div class="ui-datepicker-title"><select class="ui-datepicker-month">' + [...Array(12)].map((_, i) => '<option value="' + i + '"' + (i === m - 1 ? ' selected' : '') + '>' + (i + 1) + '월</option>').join('')
          + '</select><select class="ui-datepicker-year">' + [...Array(21)].map((_, i) => '<option value="' + (2016 + i) + '"' + (2016 + i === y ? ' selected' : '') + '>' + (2016 + i) + '</option>').join('') + '</select></div></div><table class="ui-datepicker-calendar"><tbody>';
      for (let r = 0; r < 6; r++) {
        h += MODE === 'div' ? '<div class="week__z">' : '<tr>';
        for (let c = 0; c < 7; c++) {
          const d = new Date(start.getFullYear(), start.getMonth(), start.getDate() + r * 7 + c), other = d.getMonth() !== m - 1, n = d.getDate();
          h += MODE === 'div' ? '<button type="button" class="day__z' + (other ? ' dim__z' : '') + '"' + (d < today ? ' disabled' : ' data-d="' + (other ? '' : n) + '"') + '>' + n + '</button>'
            : MODE === 'nj' ? (other ? '<td class="ui-datepicker-other-month"><button type="button" class="ui-state-default" aria-disabled="true">' + n + '</button></td>'
              : d < today ? '<td class="ui-datepicker-unselectable ui-state-disabled"><button type="button" class="ui-state-default">' + n + '</button></td>'
              : '<td aria-disabled="false" data-handler="selectDay"><button type="button" class="ui-state-default" data-d="' + n + '">' + n + '</button></td>')
            : other ? '<td class="ui-datepicker-other-month ui-datepicker-unselectable ui-state-disabled">&nbsp;</td>'
              : d < today ? '<td class="ui-datepicker-unselectable ui-state-disabled"><span class="ui-state-default">' + n + '</span></td>'
              : '<td data-handler="selectDay"><a class="ui-state-default" href="#" data-d="' + n + '">' + n + '</a></td>';
        }
        h += MODE === 'div' ? '</div>' : '</tr>';
      }
      c2.innerHTML = h + (MODE === 'div' ? '</div>' : '</tbody></table>');
      c2.querySelector('.nx').onclick = () => { view = m === 12 ? [y + 1, 1] : [y, m + 1]; d2(); };
      // 다른 달 날(div 의 dim__z)을 누르면 그 달 날이 들어감 (진짜 달력처럼) — 그래서 잘못 누르면 날짜 확인이 막음
      for (const b of c2.querySelectorAll('[data-d]')) b.onclick = e => { e.preventDefault(); sel = b.dataset.d ? [y, m, +b.dataset.d] : [m === 12 ? y + 1 : y, m === 12 ? 1 : m + 1, +b.textContent]; show(); c2.style.display = 'none'; redraw(); };
    };
    inp.onclick = () => { if (MODE === 'edit') return; view = [sel[0], sel[1]]; d2(); c2.style.display = 'block'; };
  } else
  inp.onclick = () => { if (MODE === 'nocal' || MODE === 'jq') return; view = [sel[0], sel[1]]; const f = () => { (jq ? jqDraw : draw)(); cal.style.display = jq && cal.style.display === 'block' ? 'none' : 'block'; }; if (MODE === 'late') setTimeout(f, 900); else f(); };
  if (jq) {
    inp.addEventListener('focus', () => { view = [sel[0], sel[1]]; jqDraw(); cal.style.display = 'block'; });
    document.addEventListener('mousedown', e => { if (!cal.contains(e.target) && e.target !== inp) cal.style.display = 'none'; });
  }
  window.rsvState = () => ({now: q('#radio_time1').checked, book: q('#radio_time2').checked, date: inp.value,
    h: MODE === 'nosel' ? null : q('.hour_option__J').value, m: MODE === 'nosel' ? null : q('.minute_option__V').value,
    catOpen: document.querySelector('.category_list').style.display !== 'none', ...window.rsvLog});
});
</script></body></html>
"""
RSV_RUN = RUN.replace("out.tags =", "out.rsv = await frame.evaluate(() => window.rsvState ? window.rsvState() : null);"
                      "out.report = await p.evaluate(() => ({dg: (window.__kkumiReport || {}).dg || ''}));out.tags =")


def _inner(mode: str) -> str:
    return INNER.replace("</body></html>", RSV.replace("__MODE__", mode))


def _pasted(when: datetime | None, cat: str = "셀럽 소식") -> str:
    line = _tags_line(["아일릿", "키링"], cat)
    return '<p style="text-align:center">첫 글이에요.</p><p>끝 글이에요.</p>' + (kkumi.stamp(line, when.strftime("%Y-%m-%dT%H:%M")) if when else line)


def _go(mode: str, when: datetime | None, inner: str | None = None) -> dict:
    env = _playwright_env()
    if env is None:
        pytest.skip("node playwright·크롬 없음")
    out = subprocess.run([NODE, "-e", RSV_RUN, inner or _inner(mode), code(), _pasted(when)], capture_output=True, text=True, timeout=120, env=env)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    # 꾸미기 본 일은 어느 경우에도 그대로 (글·태그·카테고리)
    assert got["alert"].startswith("모두 됐어요") and "태그 2/2" in got["alert"] and got["cat"] == "셀럽 소식", got["alert"]
    assert got["tags"].split() == ["#아일릿", "#키링"]
    return got


def _lab(d: datetime, minute: int | None = None) -> str:
    return f"{d.month}/{d.day} {d.hour:02d}:{d.minute if minute is None else minute:02d}"


def _date(d: datetime) -> str:
    return f"{d.year}. {d.month:02d}. {d.day:02d}"


@pytest.mark.skipif(shutil.which("node") is None, reason="node 없음")
def test_bookmarklet_fills_the_reservation_and_stops_before_publish() -> None:
    """내일 19:07 → '예약' 고름 · 달력에서 내일(달이 넘어가면 다음 달 단추) · 19시 · 00분(10분 단위로 내림) — 발행 단추는 0번."""
    when = (datetime.now() + timedelta(days=1)).replace(hour=19, minute=7, second=0, microsecond=0)
    got = _go("full", when)
    r = got["rsv"]
    assert r["book"] is True and r["now"] is False and r["date"] == _date(when) and r["h"] == "19" and r["m"] == "0", r
    assert r["confirm"] == 0  # 발행 단추는 주인이 직접
    assert f"예약 {_lab(when, 0)} 채움 — 발행은 직접" in got["alert"] and "분 내림" in got["alert"], got["alert"]
    assert "못 채움" not in got["alert"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node 없음")
def test_bookmarklet_walks_the_calendar_to_next_month_and_keeps_todays_date() -> None:
    now = datetime.now()
    nxt = (now.replace(day=1) + timedelta(days=32)).replace(day=3, hour=9, minute=0, second=0, microsecond=0)  # 다음 달 3일 09:00
    got = _go("full", nxt)
    r = got["rsv"]
    assert r["book"] is True and r["date"] == _date(nxt) and r["h"] == "9" and r["m"] == "0" and r["confirm"] == 0, r
    assert f"예약 {_lab(nxt)} 채움" in got["alert"] and "내림" not in got["alert"], got["alert"]
    soon = (now + timedelta(hours=1)).replace(minute=50, second=0, microsecond=0)  # 한 시간쯤 뒤 (보통 오늘 — 달력을 열지 않는 길)
    got = _go("full", soon)
    r = got["rsv"]
    assert r["book"] is True and r["date"] == _date(soon) and r["h"] == str(soon.hour) and r["m"] == "50" and r["confirm"] == 0, r


@pytest.mark.skipif(shutil.which("node") is None, reason="node 없음")
def test_bookmarklet_changes_nothing_when_the_layer_looks_different() -> None:
    when = (datetime.now() + timedelta(days=1)).replace(hour=19, minute=0, second=0, microsecond=0)
    hint = f"예약 {_lab(when)} 못 채움"
    # 예약 칸이 아예 없는 발행 설정창 (지금 시험 화면 그대로) → 안내만
    got = _go("", when, inner=INNER)
    assert got["rsv"] is None and f"{hint} (예약 칸 없음) — 직접 골라요" in got["alert"] and " 채움 —" not in got["alert"], got["alert"]
    # 시·분이 고르기 칸이 아님 → 골랐던 '예약'을 '현재'로 되돌리고 안내만
    got = _go("nosel", when)
    r = got["rsv"]
    assert r["now"] is True and r["book"] is False and r["confirm"] == 0 and f"{hint} (칸 못 찾음) — 직접 골라요" in got["alert"], (r, got["alert"])
    # 달력이 안 뜸 → 시·분 고르기 칸도 처음 값 그대로 · '현재'로
    got = _go("nocal", when)
    r = got["rsv"]
    assert r["now"] is True and r["book"] is False and r["h"] == "9" and r["m"] == "0" and r["confirm"] == 0, r
    assert r["date"] == _date(datetime.now()) and f"날짜 칸 못 찾음 — {_lab(when)} 직접 골라요" in got["alert"], (r, got["alert"])
    assert hint not in got["alert"] and " 채움 —" not in got["alert"], got["alert"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node 없음")
def test_bookmarklet_refuses_a_button_that_says_publish() -> None:
    """날짜처럼 보이는 단추라도 글자에 '발행'이 있으면 누르지 않는다 (이름에 confirm·publish 가 없어도) — 되돌리고 안내만."""
    when = (datetime.now() + timedelta(days=1)).replace(hour=19, minute=0, second=0, microsecond=0)
    got = _go("trap", when)
    r = got["rsv"]
    assert r["confirm"] == 0 and r["now"] is True and r["book"] is False and r["h"] == "9" and r["date"] == _date(datetime.now()), r
    assert f"날짜 칸 못 찾음 — {_lab(when)} 직접 골라요" in got["alert"], got["alert"]  # v9: 날짜 칸을 누르다 멈춤


@pytest.mark.skipif(shutil.which("node") is None, reason="node 없음")
def test_bookmarklet_does_not_round_the_minute_down_into_the_past() -> None:
    """7분 뒤 시각을 10분 단위로 내리면 이미 지났거나 5분도 안 남는 때 — 넣지 않고 '현재'로 되돌림 (내려도 5분 넘게 남으면 채움)."""
    when = (datetime.now() + timedelta(minutes=7)).replace(second=0, microsecond=0)
    got = _go("full", when)
    r = got["rsv"]
    assert r["confirm"] == 0, r
    if when.minute % 10 >= 2:  # 내린 시각이 지금부터 5분 안
        assert r["now"] is True and r["book"] is False and r["h"] == "9" and r["m"] == "0", r
        assert f"예약 {_lab(when)} 못 채움 (지난 시각) — 직접 골라요" in got["alert"], got["alert"]
    else:  # 내려도 5분 넘게 남음 (10번에 2번꼴)
        assert r["book"] is True and r["h"] == str(when.hour) and r["m"] == str(when.minute - when.minute % 10), r


@pytest.mark.skipif(shutil.which("node") is None, reason="node 없음")
def test_bookmarklet_leaves_the_layer_alone_for_a_past_time_or_no_time() -> None:
    past = (datetime.now() - timedelta(hours=2)).replace(second=0, microsecond=0)
    got = _go("full", past)
    r = got["rsv"]
    assert r["now"] is True and r["book"] is False and r["radio"] == 0 and r["confirm"] == 0 and r["h"] == "9", r  # 아무것도 누르지 않음
    assert f"예약 {_lab(past)} 못 채움 (지난 시각) — 직접 골라요" in got["alert"], got["alert"]
    got = _go("full", None)  # 시각이 안 실린 글 (발행 줄 · 옛 묶음) — 예약 칸을 건드리지 않고 알림에도 없음
    r = got["rsv"]
    assert r["now"] is True and r["radio"] == 0 and r["confirm"] == 0 and "예약" not in got["alert"], (r, got["alert"])


# ── v9 (11/18 드라마 글 '예약 11/19 07:30 못 채움 (칸 못 찾음)' — 같은 날 예약은 됐고 다음 날만 안 됨) ─────────────────
def _run(mode: str, when: datetime, cat: str = "셀럽 소식") -> dict:
    env = _playwright_env()
    if env is None:
        pytest.skip("node playwright·크롬 없음")
    out = subprocess.run([NODE, "-e", RSV_RUN, _inner(mode), code(), _pasted(when, cat)], capture_output=True, text=True, timeout=120, env=env)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert "태그 2/2" in got["alert"] and got["tags"].split() == ["#아일릿", "#키링"], got["alert"]
    return got


@pytest.mark.skipif(shutil.which("node") is None, reason="node 없음")
def test_v9_fills_a_next_day_reservation_in_a_jquery_ui_like_picker() -> None:
    """달력이 발행창 밖에 붙고 커서로 뜨며, 날짜를 고르면 시·분 칸을 새로 그려도 내일 07:30 을 채움 (주인 11/18 그대로)."""
    when = (datetime.now() + timedelta(days=1)).replace(hour=7, minute=30, second=0, microsecond=0)
    got = _run("jq", when)
    r = got["rsv"]
    assert r["book"] is True and r["now"] is False and r["date"] == _date(when) and r["h"] == "7" and r["m"] == "30" and r["confirm"] == 0, r
    assert got["alert"].startswith("모두 됐어요") and f"예약 {_lab(when)} 채움 — 발행은 직접" in got["alert"] and "못 채움" not in got["alert"], got["alert"]
    nxt = (datetime.now().replace(day=1) + timedelta(days=32)).replace(day=3, hour=21, minute=0, second=0, microsecond=0)  # 다음 달 (다음달 단추)
    r = _run("jq", nxt)["rsv"]
    assert r["book"] is True and r["date"] == _date(nxt) and r["h"] == "21" and r["m"] == "0" and r["confirm"] == 0, r
    r = _run("toggle", when)["rsv"]  # 커서로 열린 달력을 누름이 닫음 → 한 번 더 눌러 엶
    assert r["book"] is True and r["date"] == _date(when) and r["h"] == "7" and r["m"] == "30" and r["confirm"] == 0, r


@pytest.mark.skipif(shutil.which("node") is None, reason="node 없음")
def test_v9_never_leaves_a_wrong_date() -> None:
    """누른 날과 다른 날이 들어가면 예약을 '현재'로 되돌리고 '날짜 칸 못 찾음 — 11/19 07:30 직접 골라요' 만."""
    when = (datetime.now() + timedelta(days=1)).replace(hour=7, minute=30, second=0, microsecond=0)
    got = _run("wrongday", when)
    r = got["rsv"]
    assert r["now"] is True and r["book"] is False and r["confirm"] == 0, r
    assert f"\n날짜 칸 못 찾음 — {_lab(when)} 직접 골라요" in got["alert"] and "채움 —" not in got["alert"] and "못 채움" not in got["alert"], got["alert"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node 없음")
def test_v9_missing_category_still_fills_the_reservation() -> None:
    """카테고리가 목록에 없으면 연 목록을 닫고 알림은 그대로 · 예약은 채움 (11/18 '명작 다시 보기')."""
    when = (datetime.now() + timedelta(days=1)).replace(hour=7, minute=30, second=0, microsecond=0)
    got = _run("jq", when, cat="명작 다시 보기")
    r = got["rsv"]
    assert "카테고리 목록에 없음 (명작 다시 보기)" in got["alert"] and got["cat"] == "셀럽 패션", got["alert"]
    assert r["catOpen"] is False, r  # 연 목록은 닫음
    assert r["book"] is True and r["date"] == _date(when) and r["h"] == "7" and r["m"] == "30" and r["confirm"] == 0, r
    assert f"예약 {_lab(when)} 채움 — 발행은 직접" in got["alert"], got["alert"]


# ── v13 (11/25 하루치 '예약 11/26 08:10 못 채움 (날짜 칸 못 찾음)' — 저녁에 누른 하루치가 모두 다음 날 예약) ─────────────────
def _v13_days() -> list[datetime]:
    now = datetime.now()
    tmr = (now + timedelta(days=1)).replace(hour=8, minute=10, second=0, microsecond=0)  # 내일 (보통 같은 달 · 말일이면 다음 달)
    nxt = (now.replace(day=1) + timedelta(days=32)).replace(day=2, hour=21, minute=20, second=0, microsecond=0)  # 다음 달 2일 (다음 단추)
    return [tmr, nxt]


@pytest.mark.skipif(shutil.which("node") is None, reason="node 없음")
@pytest.mark.parametrize("mode", ["nj", "div", "ysel", "edit"])
def test_v13_fills_tomorrow_and_next_month_in_other_picker_shapes(mode: str) -> None:
    """표·div 격자·년월 고르기 칸·손으로 쓰는 칸 어느 모양이든 내일·다음 달 날짜를 채우고 날짜 칸이 그 날로 바뀐 것을 확인 — 발행 단추는 0번."""
    for when in _v13_days():
        got = _run(mode, when)
        r = got["rsv"]
        assert r["book"] is True and r["now"] is False and r["date"] == _date(when) and r["h"] == str(when.hour), (mode, when, r, got["alert"])
        assert r["m"] == str(when.minute) and r["confirm"] == 0, r
        assert f"예약 {_lab(when)} 채움 — 발행은 직접" in got["alert"] and "못 채움" not in got["alert"] and "날짜 칸" not in got["alert"], got["alert"]


@pytest.mark.skipif(shutil.which("node") is None, reason="node 없음")
def test_v13_failure_leaves_a_calendar_sketch_without_text() -> None:
    """달력에서 고른 날이 안 들어가면(wrongday) '현재'로 되돌리고, 다음 고침을 위해 달력 모양(R.dg)을 남김 — 긴 글은 ~ · 1,500자까지."""
    when = (datetime.now() + timedelta(days=1)).replace(hour=7, minute=30, second=0, microsecond=0)
    got = _run("wrongday", when)
    assert got["rsv"]["now"] is True and got["rsv"]["confirm"] == 0, got["rsv"]
    dg = got["report"]["dg"]
    assert dg.startswith(when.strftime("@%Y-%m-%dT%H:%M") + ",INPUT,true,") and "ui-datepicker" in dg and len(dg) < 1700, dg
