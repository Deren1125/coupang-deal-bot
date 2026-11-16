"""X 반응 연속 모드 (dashboard/xengage.py · static/xengage.js — 주인 11/15 '반자동 연속 모드').

지키는 것:
1) 스크립트(조종석·책갈피)에 X 단추를 누르는 코드가 없다 (click 흉내·키 이벤트·창 열기 함수·X 요청 없음) · 저절로 넘어가는 타이머도 없다.
2) 페이스: 글 사이 60~120초 · 1시간 15 · 하루 40 — 연 글을 너무 빨리 넘기면 '조금 쉬었다가 n초'. 안 연 글은 바로 넘어감.
3) 답글 초안은 xstudio 규칙·모델 가드 그대로 (Fable 은 글을 쓰지 않는다) · 최근 초안과 비슷하면 한 번 다시.
4) 목록에 같은 글이 두 번 들어가지 않는다 (목록·이미 넘긴 글·오늘 답글 단 글·내 글 · 한 계정 3개 · 모두 20개).
5) 책갈피가 넘긴 목록은 열쇠가 맞을 때만 · 화면 API 는 로그인 + 같은 사이트 토큰.
"""

from __future__ import annotations

import gzip
import json
import random
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote

import pytest

from dash_helpers import get_json, post_json, push_settings, session_headers
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.dashboard import shell
from dealbot.dashboard import xengage as xe
from dealbot.dashboard import xstudio as xs

STATIC = Path(__file__).resolve().parents[1] / "src" / "dealbot" / "dashboard" / "static"
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node 없음")
NOW = 1_794_650_000.0  # 2026-11-14 18:53 KST
POST = "자취 3년 차, 한 달 장보기 18만원으로 줄인 방법"
R1 = "18만원이면 하루 6천원꼴인데 외식은 따로 잡은 건가요"
R1B = "18만원이면 하루 6천원꼴인데 외식은 따로 잡으신 건가요"  # R1 과 거의 같은 말
R2 = "장보기만 줄인 건지 배달까지 합친 건지에 따라 다르게 보이네요"
R3 = "3년 차에 이 정도면 처음엔 얼마였는지가 더 궁금함"
R4 = "자취 연차가 쌓일수록 장보는 횟수가 줄어드는 듯"
R5 = "식비 말고 생활용품은 따로 계산한 건지 확인이 필요해 보임"


class FakeDb:
    def __init__(self) -> None:
        self.kv: dict[str, str] = {}

    def kv_get(self, k: str, default: str | None = None) -> str | None:
        return self.kv.get(k, default)

    def kv_set(self, k: str, v: str) -> None:
        self.kv[k] = v


class FakeAsk:
    def __init__(self, *answers: list[str]) -> None:
        self.answers = list(answers)
        self.calls: list[tuple[str, str]] = []

    async def __call__(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((system, user))
        texts = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        return {"ok": True, "why": "", "replies": [{"text": t} for t in texts]}


class FakeFetcher:
    def __init__(self, post: tuple[str, str] = (POST, "saver")) -> None:
        self._post = post
        self.seen: list[str] = []

    async def post(self, sid: str) -> tuple[str, str]:
        self.seen.append(sid)
        return self._post


def engage(ask: Any = None, *, own: str = "", **kw: Any) -> tuple[xe.Engage, list[float]]:
    now = [NOW]
    desk = xs.Studio(FakeDb(), ask=ask or FakeAsk([R1, R2, R3]), fetcher=kw.pop("fetcher", FakeFetcher()), clock=lambda: now[0],
                     rng=random.Random(7), **kw)
    return xe.Engage(desk, lambda: own), now


def sid(n: int) -> str:
    return str(1856000000000000000 + n)


def urls(*ns: int, h: str = "saver") -> str:
    return "\n".join(f"https://x.com/{h}/status/{sid(n)}?s=20" for n in ns)


@pytest.fixture
def bot(settings: Settings, tmp_path: Path):  # type: ignore[no-untyped-def]
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


# ---------------------------------------------------------------- 1) 누르는 코드가 없다 (정적 검사)
PRESS = re.compile(r"\.click\s*\(|dispatchEvent|createEvent|initEvent|new\s+(?:Mouse|Pointer|Keyboard|Touch|Input|Custom)?Event\b|\.submit\s*\(|"
                   r"execCommand|\.focus\s*\(|\bopen\s*\(|XMLHttpRequest|sendBeacon|WebSocket|\.requestSubmit|\[['\"]click['\"]\]")


def strip_comments(js: str) -> str:
    return re.sub(r"/\*.*?\*/|^\s*//[^\n]*|  //[^\n]*", "", js, flags=re.S | re.M)


def test_cockpit_script_never_presses_x_buttons_and_never_moves_on_by_itself() -> None:
    js = (STATIC / "xengage.js").read_text(encoding="utf-8")
    if NODE:
        assert subprocess.run([NODE, "--check", str(STATIC / "xengage.js")], capture_output=True).returncode == 0
    code = strip_comments(js)
    assert PRESS.findall(code) == []  # 누르기 흉내·키 이벤트·창 열기·폼 보내기 없음
    assert "innerHTML" not in code and "outerHTML" not in code and "eval(" not in code and "new Function" not in code and "document.write" not in code
    assert re.findall(r"https?://[^\s'\"`)]+", code) == []  # 바깥 주소 없음 — X 로 가는 길은 서버가 준 링크(a)를 주인이 누를 때만
    assert re.findall(r"location\.(?:href|assign)\s*[=(]", code) == [] and set(re.findall(r"location\.replace\(([^)]*)\)", code)) == {"'#/sns/xe'", "'#/sns/xt'", "'#/sns/xt/off'"}  # 숨김이면 체크리스트로 (11/16)
    assert re.findall(r"\blocation\s*=[^=]", code) == [] and set(re.findall(r"location\.hash\s*=\s*([^=;]+);", code)) == {"'#/sns/xe'"}  # 옮겨 가는 곳은 조종석뿐
    # 요청은 대시보드 API 두 갈래뿐 (X 로 보내는 요청 없음)
    assert set(re.findall(r"'(/dashboard/[^']*)'", code)) == {"/dashboard/api/xengage/", "/dashboard/api/xstudio/reply_go"}
    assert len(re.findall(r"(?<![\w.])fetch\(", code)) == 1 and "await fetch(url," in code
    # 타이머: 알림 숨기기 · 다시 그리기 · 남은 시간 글자뿐 — '다음 글'(next·step)·열기(markOpen)를 부르는 타이머는 없다
    timers = re.findall(r"set(?:Timeout|Interval)\((.*)", code)
    assert len(timers) == 5
    for t in timers:
        assert not re.search(r"\bnext\(|step|markOpen|makeDraft|href|location", t), t
    # '다음 글'로 넘기는 요청(step)은 next() 안에서만, next() 는 단추의 onclick 에서만 부른다
    assert code.count("API + 'step'") == 2 and len(re.findall(r"\(\) => next\(q\.sid\)", code)) == 2 and len(re.findall(r"\bnext\(", code)) == 3
    for label in ("반응 연속 모드", "X에서 이 글 열기", "답글 쓰기", "다음 글", "X 화면에서 단추를 직접 눌러 주세요", "X 에서 아무것도 누르지 않아요", "넘기기는 내가 누를 때만"):
        assert label in code, label
    raw = (STATIC / "xengage.js").read_bytes()
    assert len(raw) <= 24 * 1024 and len(gzip.compress(raw, 9)) <= 9 * 1024
    assert shell.XENGAGE_JS == "xengage.js" and f'<script defer src="{shell.asset_url("xengage.js")}"></script>' in shell.page({})


def test_bookmarklet_only_reads_and_server_has_no_x_action_calls() -> None:
    src = strip_comments((STATIC / "xhelper.js").read_text(encoding="utf-8"))
    pressed = [m for m in PRESS.findall(src) if not m.startswith("open")]  # 책갈피는 대시보드를 새 탭으로 열기만 (window.open — X 주소가 아님)
    assert pressed == [] and "fetch(" not in src
    assert re.findall(r"open\(([^,)]*)", src) == ["u"] and "const u = B + 'x#/x/' + a" in src  # 여는 곳은 대시보드 디딤돌뿐
    assert "open_('engage'" in src and "article a[href*=\"/status/\"]" in src
    py = (Path(xe.__file__)).read_text(encoding="utf-8")
    body = py.split('"""', 2)[2]
    assert not re.search(r"favorite|retweet|CreateTweet|api\.(?:x|twitter)\.com|cookie|auth_token|httpx|requests\.|urlopen", body, re.I)
    assert set(re.findall(r"https?://[^\s\"']+", body)) == {"https://x.com/search?f=top&q="}  # 서버가 스스로 여는 주소 없음 — 화면에 주는 검색 링크 하나


# ---------------------------------------------------------------- 2) 페이스
def test_pace_matches_reply_go_pace() -> None:
    assert (xe.GAP_MIN, xe.GAP_MAX, xe.HOUR_CAP, xe.DAY_CAP) == (60.0, 120.0, 15, 40) == (xs.GAP_MIN, xs.GAP_MAX, xs.HOUR_CAP, xs.DAY_CAP)
    assert 15 <= xe.QUEUE_MAX <= 20


def test_next_too_fast_asks_to_rest_and_unopened_posts_skip_freely() -> None:
    e, now = engage()
    assert e.add_pasted({"text": urls(1, 2, 3) + "\n" + urls(4, 5, h="other")})[1]["added"] == 5
    a, b, c = sid(1), sid(2), sid(3)
    # 안 연 글은 반응이 아님 → 바로 넘어가고 셈에 안 들어감
    status, got = e.step({"sid": a, "op": "next"})
    assert status == 200 and [q["sid"] for q in got["queue"]][0] == b and got["pace"]["today"] == 0 and got["pace"]["wait_s"] == 0
    # 글을 X 에서 열면 반응 1 + 쉬는 시간 60~120초
    status, got = e.open({"sid": b})
    assert status == 200 and got["pace"]["today"] == 1 and 60 <= got["pace"]["wait_s"] <= 120 and got["pace"]["why"] == "gap"
    wait = got["pace"]["wait_s"]
    assert e.open({"sid": b})[0] == 200 and e.pace()["today"] == 1  # 같은 글을 다시 여는 것은 그냥 됨 (또 세지 않음)
    # 너무 빨리 '다음 글' → 429 '조금 쉬었다가 n초' · 목록은 그대로
    now[0] += 10
    status, got = e.step({"sid": b, "op": "next"})
    assert status == 429 and got["code"] == "pace" and got["text"].startswith("조금 쉬었다가 ") and "초 뒤에" in got["text"] and got["pace"]["wait_s"] == wait - 10
    assert e.view()["queue"][0]["sid"] == b
    # 쉬는 중에는 다음 글을 열 수도 없음 (목록에서 골라 열어도)
    status, got = e.open({"sid": c})
    assert status == 429 and got["text"].startswith("조금 쉬었다가 ") and e.pace()["today"] == 1
    # 다 쉬면 넘어감 → 다음 글 열기 됨
    now[0] += wait
    status, got = e.step({"sid": b, "op": "next"})
    assert status == 200 and got["queue"][0]["sid"] == c and got["pace"]["wait_s"] == 0
    assert e.open({"sid": c})[0] == 200 and e.pace()["today"] == 2
    # 없는 글·이미 넘긴 글
    for bad in ({"sid": a}, {"sid": "abc"}, {}):
        with pytest.raises(xs.StudioError) as err:
            e.step(dict(bad, op="next"))
        assert err.value.status == 404
        with pytest.raises(xs.StudioError):
            e.open(bad)
    # 서버에는 시간이 지나면 저절로 넘기는 것이 없다: 한참 지나도 목록 맨 위는 그대로
    now[0] += 3 * 3600
    assert e.view()["queue"][0]["sid"] == c


def test_hour_cap_15_and_day_cap_40() -> None:
    e, now = engage()
    now[0] = 1_794_582_000.0  # 2026-11-14 00:00 KST — 하루를 다 쓰게
    n = 0

    def react() -> tuple[int, dict[str, Any]]:
        nonlocal n
        n += 1
        assert e.add([(f"u{n}", sid(n))], "paste")["added"] == 1
        status, got = e.open({"sid": sid(n)})
        if status == 200:
            now[0] += 121
            assert e.step({"sid": sid(n), "op": "next"})[0] == 200
        return status, got

    for _ in range(15):
        assert react()[0] == 200
    status, got = react()  # 한 시간 안 16번째
    assert status == 429 and got["pace"]["why"] == "hour" and "한 시간에 15개까지" in got["text"]
    assert e.step({"sid": sid(n), "op": "next"})[0] == 200  # 안 연 글이라 그냥 뺄 수 있음
    now[0] += 3600
    for _ in range(15):
        assert react()[0] == 200
    now[0] += 3600
    for _ in range(10):
        assert react()[0] == 200
    assert e.pace()["today"] == 40
    now[0] += 3600
    status, got = react()
    assert status == 429 and got["pace"]["why"] == "day" and got["text"] == "오늘은 글 40개에 반응했어요 — 내일 다시"
    now[0] = 1_794_582_000.0 + 86400 + 60  # 다음 날
    assert e.pace()["today"] == 0 and e.open({"sid": sid(n)})[0] == 200


# ---------------------------------------------------------------- 3) 답글 초안 — 모델 가드 · 같은 답글 되풀이 막기
def mid(*parts: str) -> str:
    """모델 이름을 조각으로 (버전 붙은 이름을 저장소에 그대로 적지 않는다 — test_no_model_ids_in_the_repo)."""
    return "-".join(("claude", *parts))


async def test_draft_uses_the_writer_model_guard() -> None:
    ask = FakeAsk([R1, R2, R3])
    e, _now = engage(ask, model=mid("fable", "5-2"))  # 설정이 Fable 이어도 글 쓰는 모델은 기본값(Opus)
    assert e.desk.model == "opus" and e.view()["model"] == "Opus"
    e.add_pasted({"text": urls(1)})
    got = await e.draft({"sid": sid(1)})
    assert got["model"] == "Opus" and len(got["replies"]) == 3
    for bad in (mid("fable", "5-2"), "fable", mid("opus", "5-6"), mid("mythos", "5"), "gpt-5", ""):
        e.desk.model = bad  # 누가 모델을 바꿔 놓아도 초안을 쓰기 전에 막힌다
        calls = len(ask.calls)
        with pytest.raises(ValueError):
            await e.draft({"sid": sid(1)})
        assert len(ask.calls) == calls, bad
    e.desk.model = mid("opus", "5-5")
    assert (await e.draft({"sid": sid(1)}))["model"] == "Opus 5.5"


async def test_draft_reads_post_once_keeps_reply_rules_and_rotates_tone() -> None:
    bad_num = "한 달 45만원 쓰던 집이면 절반도 안 되는 셈이네요"  # 원글에 없는 숫자 → xstudio 숫자 검사 경고
    ask = FakeAsk([R1, "좋은 글 감사합니다 https://a.example", bad_num])
    ff = FakeFetcher()
    e, _now = engage(ask, fetcher=ff)
    e.add_pasted({"text": urls(1, 2)})
    got = await e.draft({"sid": sid(1)})
    assert ff.seen == [sid(1)] and got["post"] == {"text": POST, "author": "saver", "fetched": True}
    assert [r["text"] for r in got["replies"]] == [R1, bad_num]  # 링크 든 것은 버림 (lint_reply)
    assert got["replies"][0]["warn"] == [] and "원글에 없는 숫자: 45" in got["replies"][1]["warn"][0]
    system, user = ask.calls[0]
    assert "원글 속 구체적인 것 하나" in system and "보태는 것이 하나" in system and POST in user  # 관점 한 줄 규칙 그대로 (xstudio REPLY_SYSTEM)
    assert got["tone"] == "agree" and "[말투] 공감" in system
    assert (await e.draft({"sid": sid(2)}))["tone"] == "add"  # 말투를 안 고르면 글마다 돌아가며
    assert (await e.draft({"sid": sid(2), "tone": "counter"}))["tone"] == "counter"
    with pytest.raises(xs.StudioError) as err:  # 목록에 없는 글은 초안을 쓰지 않음
        await e.draft({"sid": sid(9)})
    assert err.value.status == 404
    assert POST not in json.dumps(e.desk.db.kv, ensure_ascii=False) and R1 not in json.dumps(e.desk.db.kv, ensure_ascii=False)  # 글·초안은 저장하지 않음


async def test_similar_drafts_are_dropped_and_asked_again_once() -> None:
    assert xe.similar(xe.sig(R1), xe.sig(R1B)) and xe.similar(xe.sig(R1), xe.sig("  " + R1 + " ")) and not xe.similar(xe.sig(R1), xe.sig(R2))
    assert not xe.similar([], []) and not xe.similar(xe.sig(R3), xe.sig(R4))
    ask = FakeAsk([R1, R2, R3], [R1B, R2, R3], [R4, R5, R1], [R1, R2], [R1B, R3])
    ff = FakeFetcher()
    e, _now = engage(ask, fetcher=ff)
    e.add_pasted({"text": urls(1, 2, 3)})
    assert [r["text"] for r in (await e.draft({"sid": sid(1)}))["replies"]] == [R1, R2, R3] and len(ask.calls) == 1
    # 다음 글에서 방금과 비슷한 초안만 나옴 → 다른 말투로 한 번 다시 → 새로운 것만 (읽어 둔 원글로 — 서버가 글을 또 읽지 않음)
    got = await e.draft({"sid": sid(2)})
    assert [r["text"] for r in got["replies"]] == [R4, R5] and len(ask.calls) == 3 and ff.seen == [sid(1), sid(2)]
    assert "[말투] 정보 더하기" in ask.calls[1][0] and "[말투] 공감" in ask.calls[2][0]
    # 다시 써도 비슷하면 처음 것을 경고와 함께 (더는 묻지 않음 — AI 는 글 하나에 많아야 두 번)
    got = await e.draft({"sid": sid(3)})
    assert len(ask.calls) == 5 and [r["text"] for r in got["replies"]] == [R1, R2]
    assert all(r["warn"][-1] == xe.SIM_WARN for r in got["replies"])
    assert len(json.loads(e.desk.db.kv[xe.KV])["sig"]) == 7


# ---------------------------------------------------------------- 4) 목록 — 겹치지 않게
def test_parse_posts_from_pasted_text_and_bookmarklet_list() -> None:
    text = (f"이 글 좋음 https://x.com/saver/status/{sid(1)}?s=20&t=abc 그리고\nhttps://twitter.com/Other_1/status/{sid(2)}/photo/1\n"
            f"https://mobile.x.com/saver/status/{sid(1)}\nhttps://x.com/i/web/status/{sid(3)}\nhttps://x.com/saver\nhttps://evil.example/a/status/{sid(4)}"
            f"\nhttps://evilx.com/a/status/{sid(5)}\nhttps://x.com.evil.example/a/status/{sid(6)}")
    assert xe.parse_posts(text) == [("saver", sid(1)), ("Other_1", sid(2))]
    assert xe.parse_posts(f"saver/{sid(1)},other/{sid(2)}, saver/{sid(1)},bad/12,../{sid(5)},a b/{sid(6)}") == [("saver", sid(1)), ("other", sid(2))]
    assert xe.parse_posts(f"https://x.com/i/status/{sid(7)}") == [("", sid(7))] and xe.post_url("", sid(7)) == f"https://x.com/i/web/status/{sid(7)}"
    assert xe.parse_posts(None) == [] and xe.parse_posts("") == [] and xe.parse_posts(["x"]) == []


def test_queue_has_no_duplicates_and_keeps_caps() -> None:
    e, now = engage(own="MyAcct")
    status, got = e.add_pasted({"text": urls(1, 2, 1) + "\n" + urls(1)})
    assert status == 200 and got["added"] == 2 and [q["sid"] for q in got["queue"]] == [sid(1), sid(2)]
    assert got["queue"][0] == {"sid": sid(1), "h": "saver", "url": f"https://x.com/saver/status/{sid(1)}"}
    status, got = e.add_pasted({"text": urls(2, 3, 4, 5)})  # 목록에 있는 글 뺌 · 한 계정 3개까지
    assert got["added"] == 1 and got["text"] == "글 1개를 반응 목록에 넣었어요 (이미 있거나 한 글 1개 · 목록이 차서 못 넣은 글 2개 뺌)"
    assert e.add_pasted({"text": urls(6, h="myacct")})[1]["text"] == "글 0개를 반응 목록에 넣었어요 (내 글 1개 뺌)"  # 내 글에는 반응하지 않음
    # 이미 넘긴 글 · 오늘 답글 단 글은 다시 들어오지 않음
    assert e.step({"sid": sid(1), "op": "next"})[0] == 200
    assert e.desk.reply_go({"op": "go", "sid": sid(7)})[0] == 200
    got = e.add_pasted({"text": urls(1, h="a") + "\n" + urls(7, h="b") + "\n" + urls(8, h="c")})[1]
    assert got["added"] == 1 and [q["sid"] for q in got["queue"]] == [sid(2), sid(3), sid(8)]
    # 모두 20개까지
    more = e.add([(f"u{n}", sid(100 + n)) for n in range(30)], "helper")
    assert more == {"added": 17, "dup": 0, "full": 13, "mine": 0}
    q = [x["sid"] for x in e.view()["queue"]]
    assert len(q) == xe.QUEUE_MAX == 20 and len(set(q)) == 20
    # 안 연 글 비우기는 연 글을 남긴다
    assert e.open({"sid": sid(2)})[0] == 200
    assert [x["sid"] for x in e.step({"op": "clear"})[1]["queue"]] == [sid(2)]
    for bad in ({}, {"text": ""}, {"text": "x" * (xe.ADD_TEXT_MAX + 1)}, {"text": ["a"]}, {"text": "주소 없음 https://example.com/a"}):
        with pytest.raises(xs.StudioError) as err:
            e.add_pasted(bad)
        assert err.value.status == 400


def test_view_gives_profile_and_search_links_but_server_never_lists_timelines() -> None:
    e, _now = engage(hot=lambda: [{"id": "h1", "keyword": "엔비디아 시총", "text": "요약"}, {"id": "h2", "keyword": "엔비디아 시총", "text": "요약"},
                                  {"id": "h3", "keyword": "", "text": "요약"}])
    e.desk.set_target({"op": "add", "handle": "mad_dogdebt", "memo": "부업"})
    v = e.view()
    assert v["targets"] == [{"h": "mad_dogdebt", "memo": "부업"}] and v["links"] == {"reply": "https://x.com/intent/post?in_reply_to=", "profile": "https://x.com/"}
    assert v["hot"] == [{"keyword": "엔비디아 시총", "url": "https://x.com/search?f=top&q=%EC%97%94%EB%B9%84%EB%94%94%EC%95%84%20%EC%8B%9C%EC%B4%9D"}]
    assert v["queue"] == [] and v["pace"]["gap"] == [60, 120] and v["pace"]["day_cap"] == 40 and v["pace"]["hour_cap"] == 15 and v["replies"]["day_cap"] == 40
    assert v["tones"][0] == ["auto", "알아서"] and v["limits"]["queue"] == 20 and v["limits"]["reply"] == [15, 60]
    assert e.desk.fetcher.seen == []  # 화면 값을 만들 때 서버는 아무 글도 읽지 않음


# ---------------------------------------------------------------- 5) 책갈피 넘기기 · 화면 API
def test_handoff_needs_the_key_then_adds_visible_posts_and_the_profile() -> None:
    e, _now = engage(own="MyAcct")
    key = xs.helper_key(e.desk.db)
    lst = f"saver/{sid(1)},saver/{sid(2)},other/{sid(3)}"
    for bad in ({"l": lst}, {"k": "wrong", "l": lst}, {"k": key[:-1], "l": lst, "h": "saver"}):
        status, got = e.handoff(bad)
        assert status == 403 and got["code"] == "bad_key" and "다시 설치" in got["text"]
    assert e.view()["queue"] == [] and e.desk.targets() == []
    status, got = e.handoff({"k": key, "l": lst})  # 홈·검색 화면: 글만
    assert status == 200 and got["text"] == "글 3개를 반응 목록에 넣었어요" and len(got["queue"]) == 3 and e.desk.targets() == []
    status, got = e.handoff({"k": key, "l": f"saver/{sid(1)},saver/{sid(4)}", "h": "saver", "fo": "27000"})  # 프로필: 그 계정도 자주 보는 계정에
    assert status == 200 and got["text"] == "글 1개를 반응 목록에 넣었어요 (이미 있거나 한 글 1개 뺌) · @saver 을 자주 보는 계정에 넣었어요"
    assert got["targets"] == [{"h": "saver", "memo": "팔로워 27,000"}]
    status, got = e.handoff({"k": key, "l": f"myacct/{sid(5)}", "h": "MyAcct"})  # 내 프로필: 내 글은 안 넣고 계정도 안 넣음
    assert status == 200 and "내 글 1개" in got["text"] and len(got["targets"]) == 1 and "팔로워" not in got["text"]
    # 내 프로필에서 팔로워 수가 보이면 예전 책갈피처럼 그 수는 기록 (프로필에는 늘 글이 보여 이 길로만 옴)
    status, got = e.handoff({"k": key, "l": f"myacct/{sid(5)}", "h": "MyAcct", "fo": "1234"})
    assert status == 200 and got["text"].endswith(" · 내 계정 팔로워 1,234명을 기록했어요") and len(got["targets"]) == 1
    assert e.desk.progress()["followers"] == 1234
    with pytest.raises(xs.StudioError):
        e.handoff({"k": key, "l": "읽은 것 없음"})


async def test_endpoints_need_login_and_same_site_token(bot: DealBot) -> None:
    anon = {":peer": "127.0.0.1", "x-forwarded-for": "203.0.113.7"}
    posts = (xe.ADD_PATH, xe.HANDOFF_PATH, xe.OPEN_PATH, xe.STEP_PATH, xe.DRAFT_PATH)
    assert (await bot.web.dispatch("GET", xe.STATE_PATH, anon))[0] == 401
    for path in posts:
        assert (await post_json(bot, path, {}, dict(anon, **{"content-type": "application/json"})))[0] == 401, path
    eng: xe.Engage = bot.dashboard.api.xengage  # type: ignore[union-attr]
    assert eng.desk is bot.dashboard.api.xstudio  # type: ignore[union-attr]
    ask = FakeAsk([R1, R2, R3])
    eng.desk._ask_fn, eng.desk.fetcher = ask, FakeFetcher()
    body = {"text": urls(1), "sid": sid(1)}
    for path in posts:
        status, got, _ = await post_json(bot, path, body, session_headers(bot, csrf=False))
        assert status == 403 and got["code"] == "csrf", path
        status, got, _ = await post_json(bot, path, body, dict(session_headers(bot), origin="https://evil.example"))
        assert status == 403 and got["code"] == "cross_site", path
    assert ask.calls == [] and eng.view()["queue"] == []  # 막힌 요청은 목록도 AI 도 건드리지 않음
    assert (await bot.web.dispatch("GET", "/dashboard/static/xengage.js", anon))[0] == 200
    h = session_headers(bot)
    status, got, head = await get_json(bot, xe.STATE_PATH, h)
    assert status == 200 and head["Cache-Control"] == "no-store" and got["queue"] == [] and got["model"] == "Opus"
    status, got, _ = await post_json(bot, xe.ADD_PATH, {"text": urls(1, 2)}, h)
    assert status == 200 and got["added"] == 2
    status, got, _ = await post_json(bot, xe.DRAFT_PATH, {"sid": sid(1), "rid": "r_abcdef123456"}, h)
    assert status == 200 and [r["text"] for r in got["replies"]] == [R1, R2, R3]
    status, again, _ = await post_json(bot, xe.DRAFT_PATH, {"sid": sid(1), "rid": "r_abcdef123456"}, h)  # 같은 rid = 같은 일 (AI 를 또 부르지 않음)
    assert status == 200 and again == got and len(ask.calls) == 1
    status, got, _ = await post_json(bot, xe.OPEN_PATH, {"sid": sid(1)}, h)
    assert status == 200 and got["pace"]["today"] == 1
    status, got, _ = await post_json(bot, xe.STEP_PATH, {"sid": sid(1), "op": "next"}, h)
    assert status == 429 and got["code"] == "pace" and got["text"].startswith("조금 쉬었다가 ")
    # 주인 11/16 'X 는 체크리스트만': 기본은 책갈피·연속 모드 숨김 — 예전 책갈피로 와도 한 줄만 (목록은 그대로)
    assert got_state_flags(eng) == (False, False)
    status, got, _ = await post_json(bot, xe.HANDOFF_PATH, {"k": xs.helper_key(eng.desk.db), "l": f"a/{sid(5)}"}, h)
    assert status == 410 and got["code"] == "off" and got["text"] == xs.HELPER_OFF and len(eng.view()["queue"]) == 2
    bot.settings.dashboard.x_helper = bot.settings.dashboard.x_engage = True  # 설정을 켜면 예전대로
    assert got_state_flags(eng) == (True, True)
    status, got, _ = await post_json(bot, xe.HANDOFF_PATH, {"k": "nope", "l": f"a/{sid(5)}"}, h)
    assert status == 403 and got["code"] == "bad_key"
    status, got, _ = await post_json(bot, xe.HANDOFF_PATH, {"k": xs.helper_key(eng.desk.db), "l": f"a/{sid(5)}"}, h)
    assert status == 200 and len(got["queue"]) == 3


# ---------------------------------------------------------------- 6) 책갈피·디딤돌 (node)
HELPER_FAKE = r"""
const [code, page] = process.argv.slice(1);
const A = href => ({getAttribute: k => (k === 'href' ? href : null)});
const N = (q = {}) => ({q, querySelector(sel) { for (const s of sel.split(',')) { const v = this.q[s.trim()]; if (v) return Array.isArray(v) ? v[0] : v; } return null; },
  querySelectorAll(sel) { const out = []; for (const s of sel.split(',')) { const v = this.q[s.trim()]; if (v) out.push(...(Array.isArray(v) ? v : [v])); } return out; }});
const links = [A('/saver/status/1856000000000000001'), A('/saver/status/1856000000000000001/photo/1'), A('/saver/status/1856000000000000001'),
  A('/other_1/status/1856000000000000002'), A('/saver/status/1856000000000000002/analytics'), A('/i/status/12'), A(null)];
const pages = {
  home: {loc: {hostname: 'x.com', origin: 'https://x.com', pathname: '/home', href: ''}, doc: Object.assign(N({'article a[href*="/status/"]': links}), {body: {innerText: '홈'}})},
  profile: {loc: {hostname: 'x.com', origin: 'https://x.com', pathname: '/saver', href: ''},
    doc: Object.assign(N({'article a[href*="/status/"]': links, '[data-testid="UserName"]': {}, 'a[href$="/followers"]': {nodeType: 3, nodeValue: '2.7만 팔로워'}}), {body: {innerText: '프로필'}})},
  many: {loc: {hostname: 'x.com', origin: 'https://x.com', pathname: '/search', href: ''},
    doc: Object.assign(N({'article a[href*="/status/"]': Array.from({length: 30}, (_, i) => A('/u' + i + '/status/18560000000000001' + (10 + i)))}), {body: {innerText: '검색'}})},
};
const p = pages[page];
const out = {confirms: [], alerts: [], opened: [], touched: []};
for (const a of p.doc.q['article a[href*="/status/"]']) for (const k of ['click', 'dispatchEvent', 'focus']) a[k] = () => out.touched.push(k);
global.document = p.doc; global.location = p.loc; global.window = global;
global.confirm = m => { out.confirms.push(m); return true; };
global.alert = m => out.alerts.push(m);
global.open = (u, t) => { out.opened.push([u, t]); return {}; };
new Function(code)();
console.log(JSON.stringify(out));
"""


def run_helper(db: Any, page: str) -> dict[str, Any]:
    code = xs.helper_view(db, "https://dash.example")["code"]
    assert code.startswith("javascript:") and len(code) < 12000
    res = subprocess.run([NODE, "-e", HELPER_FAKE, unquote(code[len("javascript:"):]), page], capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


@needs_node
def test_helper_hands_over_only_visible_post_addresses() -> None:
    db = FakeDb()
    key = xs.helper_key(db)
    got = run_helper(db, "home")
    assert got["alerts"] == [] and got["touched"] == [] and len(got["opened"]) == 1 and got["opened"][0][1] == "_blank"
    head, _, q = got["opened"][0][0].partition("?")
    assert head == "https://dash.example/dashboard/x#/x/engage"  # 대시보드 디딤돌로만
    assert {k: v[0] for k, v in parse_qs(q).items()} == {"k": key, "l": "saver/1856000000000000001,other_1/1856000000000000002"}
    assert "보이는 글 2개" in got["confirms"][0] and "내가 직접" in got["confirms"][0]
    got = run_helper(db, "profile")
    q = {k: v[0] for k, v in parse_qs(got["opened"][0][0].partition("?")[2]).items()}
    assert q == {"k": key, "l": "saver/1856000000000000001,other_1/1856000000000000002", "h": "saver", "fo": "27000"} and got["touched"] == []
    got = run_helper(db, "many")
    q = {k: v[0] for k, v in parse_qs(got["opened"][0][0].partition("?")[2]).items()}
    assert len(q["l"].split(",")) == 20 and len(xe.parse_posts(q["l"])) == 20 and got["touched"] == []  # 한 번에 20개까지


XGO_FAKE = r"""
const [src, hash] = process.argv.slice(1);
const out = {};
global.location = {hash, replace(u) { out.to = u; }};
global.document = {querySelector: () => null};
global.sessionStorage = {setItem() {}, removeItem() {}, getItem: () => null};
eval(require('fs').readFileSync(src, 'utf8'));
console.log(JSON.stringify(out));
"""


@needs_node
def test_stepping_stone_passes_the_engage_hash() -> None:
    def run(hash_: str) -> str:
        res = subprocess.run([NODE, "-e", XGO_FAKE, str(STATIC / "xgo.js"), hash_], capture_output=True, text=True, timeout=30)
        assert res.returncode == 0, res.stderr
        return json.loads(res.stdout)["to"]

    assert run("#/x/engage?k=K&l=a%2F12345") == "/dashboard/#/x/engage?k=K&l=a%2F12345"
    assert run("#/x/engagex?k=K") == "/dashboard/" and run("#/x/engage/../../login") == "/dashboard/"


# ---------------------------------------------------------------- 7) 조종석 화면 (node — 가짜 DOM)
SHIM = r"""
const [src, scenario, stateJson, extraJson] = process.argv.slice(1);
const fs = require('fs');
function mk(tag) {
  const n = {nodeType: 1, tagName: tag.toUpperCase(), children: [], attrs: {}, style: {}, dataset: {}, listeners: {}, parentNode: null, hidden: false, disabled: false,
    className: '', value: '', _text: '',
    setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; }, hasAttribute(k) { return k in this.attrs; },
    removeAttribute(k) { delete this.attrs[k]; }, addEventListener(t, f) { this.listeners[t] = f; },
    append(...xs) { for (const x of xs) { const c = typeof x === 'string' ? Object.assign(mk('#text'), {_text: x}) : x; if (c.parentNode) c.remove(); c.parentNode = this; this.children.push(c); } },
    prepend(x) { if (x.parentNode) x.remove(); x.parentNode = this; this.children.unshift(x); },
    after(x) { if (x.parentNode) x.remove(); const p = this.parentNode; x.parentNode = p; p.children.splice(p.children.indexOf(this) + 1, 0, x); },
    remove() { const p = this.parentNode; if (p) { p.children.splice(p.children.indexOf(this), 1); this.parentNode = null; } },
    replaceWith(x) { const p = this.parentNode; if (!p) return; p.children[p.children.indexOf(this)] = x; x.parentNode = p; this.parentNode = null; },
    replaceChildren(...xs) { this.children.forEach(c => { c.parentNode = null; }); this.children = []; this.append(...xs); },
    contains(x) { while (x) { if (x === this) return true; x = x.parentNode; } return false; },
    get firstChild() { return this.children[0] || null; },
    get nextSibling() { const p = this.parentNode; return p ? p.children[p.children.indexOf(this) + 1] || null : null; },
    set textContent(v) { this._text = String(v); this.children = []; }, get textContent() { return this._text + this.children.map(c => c.textContent).join(''); }};
  return n;
}
const main = mk('main'), ttl = mk('h1'), toast = mk('div'), head = mk('head');
main.attrs.id = 'main'; main.dataset.tab = 'sns'; ttl.attrs.id = 'ttl'; toast.attrs.id = 'toast';
const snt = mk('div'); snt.attrs.id = 'snt';
for (let i = 0; i < 4; i++) { const b = mk('button'); b.attrs['aria-pressed'] = i === 1 ? 'true' : 'false'; snt.append(b); }
const xsc = mk('article'); xsc.attrs.id = 'xsc';
main.append(snt, xsc, mk('div'));
if (JSON.parse(extraJson || '{}').xtc) { const c = mk('article'); c.attrs.id = 'xtc'; xsc.after(c); }
const byId = (n, id) => (n.attrs && n.attrs.id === id ? n : n.children.map(c => byId(c, id)).find(Boolean));
global.document = {readyState: 'complete', head, visibilityState: 'visible', activeElement: null, createElement: mk, addEventListener() {},
  getElementById: id => (id === 'boot' ? {textContent: JSON.stringify({me: {csrf: 'tok'}})} : id === 'ttl' ? ttl : id === 'toast' ? toast : byId(main, id) || byId(head, id) || null)};
const store = {}, hc = [], timers = [];
global.location = {hash: '#/sns/x', origin: 'https://dash.example', replace(h) { this.hash = h; setImmediate(() => hc.forEach(f => f())); }};
global.window = global; global.addEventListener = (t, f) => { if (t === 'hashchange') hc.push(f); }; global.scrollTo = () => {};
global.MutationObserver = class { observe() {} };
global.localStorage = {getItem: k => (k in store ? store[k] : null), setItem: (k, v) => { store[k] = String(v); }};
global.setTimeout = (f, ms) => { timers.push(['t', ms, f]); return 0; }; global.clearTimeout = () => {};
global.setInterval = (f, ms) => { timers.push(['i', ms, f]); return 0; }; global.clearInterval = () => {};
global.open = () => { throw new Error('창 열기 금지'); };
let state = JSON.parse(stateJson);
const extra = JSON.parse(extraJson || '{}'), posts = [], gets = [];
global.fetch = async (u, o) => {
  if (!/^\/dashboard\/api\/(xengage\/|xstudio\/reply_go$)/.test(u)) throw new Error('바깥 요청: ' + u);
  const path = u.replace('/dashboard/api/', '');
  if (!o || o.method !== 'POST') { gets.push(path); return {status: 200, json: async () => state}; }
  const body = JSON.parse(o.body);
  posts.push([path, o.headers['X-Dash-Token'], body]);
  const r = (extra[path] || []).shift() || {};
  if (Array.isArray(r.queue)) state = r;
  return {status: r._status || 200, json: async () => r};
};
const tick = async (n = 8) => { for (let i = 0; i < n; i++) await new Promise(r => setImmediate(r)); };
const text = n => n.textContent;
const all = (n, f, out = []) => { if (f(n)) out.push(n); n.children.forEach(c => all(c, f, out)); return out; };
const byText = (root, tag, t) => all(root, n => n.tagName === tag && text(n) === t)[0];
const tap = n => { const e = {currentTarget: n, prevented: false, preventDefault() { this.prevented = true; }}; n.listeners.click(e); return e.prevented; };
(async () => {
  const out = {};
  if (scenario === 'card') {
    eval(fs.readFileSync(src, 'utf8'));
    await tick();
    out.order = main.children.map(c => c.attrs.id || c.tagName);
    out.card = byId(main, 'xec') ? text(byId(main, 'xec')) : null; out.posts = posts;
    location.hash = '#/sns/xe'; window.SBXEngage.mount(); await tick();
    out.scr = main.children[0].attrs.id; out.hidden = 'data-xe' in main.attrs; out.title = text(ttl); out.h2 = location.hash;
    location.hash = '#/sns/x'; window.SBXEngage.mount();
    out.back = ['data-xe' in main.attrs, main.children.map(c => c.attrs.id || c.tagName)];
  }
  if (scenario === 'flow') {
    location.hash = '#/sns/xe';
    eval(fs.readFileSync(src, 'utf8'));
    await tick(12);
    const scr = main.children[0];
    out.screen = text(scr);
    out.drafted = posts.filter(p => p[0] === 'xengage/draft').map(p => p[2].sid);  // 지금 글 + 다음 글 초안을 미리
    const go = byText(scr, 'A', 'X에서 이 글 열기');
    out.go = [go.getAttribute('href'), go.getAttribute('target'), go.getAttribute('rel')];
    const replyA = all(scr, n => n.tagName === 'A' && text(n) === '답글 쓰기');
    out.replyHref = replyA.map(a => a.getAttribute('href'));
    const before = posts.length;
    // 한참 기다려도 (타이머가 다 돌아도) 아무 요청도 안 나간다 — 저절로 열거나 넘어가지 않음
    for (const t of timers.splice(0)) t[2]();
    await tick();
    out.idle = posts.length - before;
    // 글 열기: 링크를 내가 누름 → 기록(open)만 나가고 화면은 그대로 X 로 (막지 않음)
    out.goPrevented = tap(go); await tick();
    out.afterGo = posts.slice(before).map(p => [p[0], p[2]]);
    // 다음 글을 너무 빨리 → 서버가 '조금 쉬었다가' (목록 그대로)
    const scr2 = main.children[0];
    byText(scr2, 'BUTTON', '다음 글').listeners.click({}); await tick();
    out.fast = [posts[posts.length - 1][0], posts[posts.length - 1][2], text(toast), text(main.children[0]).includes('지금 글 1/3 · @saver')];
    // 쉬는 중에 답글 쓰기 → 이미 연 글이라 답글 쉬는 시간만 봄 → 열림 + reply_go 기록
    const ra = all(main.children[0], n => n.tagName === 'A' && text(n) === '답글 쓰기')[0];
    const n0 = posts.length;
    out.replyPrevented = tap(ra); await tick();
    out.replyPosts = posts.slice(n0).map(p => [p[0], p[2]]);
    const rb = all(main.children[0], n => n.tagName === 'A' && text(n) === '답글 쓰기')[0];
    toast.textContent = ''; out.reply2 = [tap(rb), text(toast)];  // 같은 글에 또 답글은 막음
    // 다 쉬고 다음 글 → 넘어감
    byText(main.children[0], 'BUTTON', '다음 글').listeners.click({}); await tick(12);
    out.next = [posts.filter(p => p[0] === 'xengage/step').length, text(main.children[0]).includes('지금 글 1/2 · @other')];
    // 안 연 둘째 글을 쉬는 중에 열려고 하면 화면에서 먼저 막음 (요청 없음)
    const go2 = byText(main.children[0], 'A', 'X에서 이 글 열기');
    const n1 = posts.length; toast.textContent = '';
    out.blocked = [tap(go2), text(toast), posts.length - n1];
  }
  if (scenario === 'deep') {
    location.hash = '#/x/engage?k=KEY123&l=saver%2F1856000000000000001%2Cother%2F1856000000000000002&h=saver&fo=27000&evil=1';
    eval(fs.readFileSync(src, 'utf8'));
    await tick(12);
    out.hash = location.hash; out.handoff = posts.filter(p => p[0] === 'xengage/handoff'); out.toast = text(toast);
  }
  out.gets = gets;
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
"""


def view_json(e: xe.Engage) -> dict[str, Any]:
    return json.loads(json.dumps(e.view(), ensure_ascii=False))


def run_shim(scenario: str, state: dict[str, Any], extra: dict[str, Any] | None = None) -> dict[str, Any]:
    res = subprocess.run([NODE, "-e", SHIM, str(STATIC / "xengage.js"), scenario, json.dumps(state, ensure_ascii=False), json.dumps(extra or {}, ensure_ascii=False)],
                         capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


@needs_node
def test_js_card_sits_under_the_x_card_and_cockpit_takes_over() -> None:
    e, _now = engage()
    e.add_pasted({"text": urls(1, 2)})
    got = run_shim("card", view_json(e))
    assert got["order"] == ["snt", "xsc", "xec", "DIV"] and got["posts"] == []  # 카드만 볼 때는 초안을 미리 받지 않음
    for t in ("반응 연속 모드", "반응할 글 2개", "오늘 0/40", "반응 연속 모드 시작"):
        assert t in got["card"], t
    assert got["scr"] == "xes" and got["hidden"] and got["title"] == "X 반응 연속 모드"
    assert got["back"] == [False, ["snt", "xsc", "xec", "DIV"]]
    # '오늘 X 글' 카드(xtoday.js)가 있으면 그 뒤에 — 둘 다 '오늘 할 X 일' 바로 밑을 다투면 서로 끝없이 옮김
    got = run_shim("card", view_json(e), {"xtc": True})
    assert got["order"] == ["snt", "xsc", "xtc", "xec", "DIV"] and got["back"][1] == ["snt", "xsc", "xtc", "xec", "DIV"]


@needs_node
async def test_js_flow_only_the_owner_opens_replies_and_moves_on() -> None:
    e, now = engage()
    e.add_pasted({"text": urls(1) + "\n" + urls(2, h="other") + "\n" + urls(3, h="third")})
    v0 = view_json(e)
    d1, d2 = await e.draft({"sid": sid(1)}), await e.draft({"sid": sid(2)})
    _s, opened = e.open({"sid": sid(1)})
    _s, fast = e.step({"sid": sid(1), "op": "next"})
    _s, rgo = e.desk.reply_go({"op": "go", "sid": sid(1)})
    now[0] += 200
    _s, after = e.step({"sid": sid(1), "op": "next"})
    after = json.loads(json.dumps(after, ensure_ascii=False))
    after["pace"]["wait_s"], after["pace"]["why"], after["pace"]["text"] = 45, "gap", "조금 쉬었다가 45초 뒤에 (글 사이 1~2분 쉬기)"  # 화면이 먼저 막는지 보려고
    got = run_shim("flow", v0, {"xengage/draft": [d1, d2], "xengage/open": [opened], "xengage/step": [dict(fast, _status=429), after],
                                "xstudio/reply_go": [rgo]})
    for t in ("오늘 반응한 글0/40", "지금 글 1/3 · @saver", POST, "♥·리포스트는 X 화면에서 단추를 직접 눌러 주세요", "답글 초안 3개", "다음 차례 2개", "넘기기는 내가 누를 때만"):
        assert t in got["screen"], t
    assert got["drafted"] == [sid(1), sid(2)]
    assert got["go"] == [f"https://x.com/saver/status/{sid(1)}", "_blank", "noopener"]
    assert got["replyHref"][0] == xs.reply_intent(sid(1), R1) and len(got["replyHref"]) == 3  # X 공식 글쓰기 창 (in_reply_to) — 게시 단추는 거기서 주인이
    assert got["idle"] == 0
    assert got["goPrevented"] is False and got["afterGo"] == [["xengage/open", {"sid": sid(1)}]]
    assert got["fast"][:2] == ["xengage/step", {"sid": sid(1), "op": "next"}] and got["fast"][2].startswith("조금 쉬었다가 ") and got["fast"][3] is True
    assert got["replyPrevented"] is False and got["replyPosts"] == [["xstudio/reply_go", {"op": "go", "sid": sid(1)}]]
    assert got["reply2"] == [True, "이 글에는 오늘 이미 답글을 달았어요"]
    assert got["next"] == [2, True]
    assert got["blocked"][0] is True and got["blocked"][1].startswith("조금 쉬었다가 45초 뒤에") and got["blocked"][2] == 0


@needs_node
def test_js_deep_link_hands_the_list_to_the_server_with_the_key() -> None:
    e, _now = engage()
    got = run_shim("deep", view_json(e), {"xengage/handoff": [{"_status": 403, "status": "error", "code": "bad_key", "text": "책갈피가 예전 것이에요"}]})
    assert got["hash"] == "#/sns/xe" and got["toast"] == "책갈피가 예전 것이에요"
    assert got["handoff"] == [["xengage/handoff", "tok", {"k": "KEY123", "l": f"saver/{sid(1)},other/{sid(2)}", "h": "saver", "fo": "27000"}]]


@needs_node
def test_js_hidden_by_default_switch_no_card_cockpit_goes_to_checklist_and_old_bookmark_gets_one_line() -> None:
    """주인 11/16 'X 는 체크리스트만' — on=false (설정 dashboard.x_engage 기본) 면 카드 없음 · #/sns/xe 는 '오늘 X 글' 체크리스트로."""
    e, _now = engage()
    e.add_pasted({"text": urls(1, 2)})
    e.on = lambda: False
    got = run_shim("card", view_json(e))
    assert got["order"] == ["snt", "xsc", "DIV"] and got["posts"] == [] and got["h2"] == "#/sns/xt" and got["scr"] == "snt" and not got["hidden"]
    e.desk.helper_on = lambda: False  # 예전 책갈피로 와도 받지 않고 한 줄만 → 체크리스트
    got = run_shim("deep", view_json(e))
    assert got["hash"] == "#/sns/xt/off" and got["handoff"] == [] and got["toast"] == xs.HELPER_OFF


def got_state_flags(eng: xe.Engage) -> tuple[bool, bool]:
    v = eng.view()
    return v["on"], v["helper"]
