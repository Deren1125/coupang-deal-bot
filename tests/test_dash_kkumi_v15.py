"""꾸미기 v15 · 하루치 v11 — 링크 카드 (주인 아이패드 11/27 09:16 데런픽 3/6 「코르티스, 데뷔 1년 3개월 만에 대상만 4개…」
'카드 카드 안 생김 · 글 차례가 원고와 달라요' 로 멈춤 · 그 전 라커룸에는 카드가 안 되고 주소 글자 줄 'blog.naver.com/demiyum/…' 만 남음).

v14 는 확인 단추를 누른 뒤 8초만 기다림 → 아이패드에서 카드가 늦게 생겨 '카드 안 생김' + 늦은 카드가 다음 글 뒤에 붙어 차례도 어긋남
('링크 카드 0/1' 은 안 떴음 = 점검 때는 카드가 있었음).
v15: 우리가 방금 붙인 주소 줄 끝에서 Enter 먼저 (네이버가 주소를 카드로 바꿀 수 있음) → 툴바 링크 → 확인 뒤 20초까지 (창이 남았으면 확인 한 번 더)
→ 안 되면 한 번 더 → 끝내 안 되면 그 주소 줄만 골라 지우고(글이 그 주소 하나인 본문 끝 줄 · 다른 줄은 안 건드림) 메모와 함께 이어 감 ·
원래 차례에서도 그 카드를 뺌 · 줄을 못 지웠으면 그 줄을 지워 달라고 멈춤.
가짜 화면: test_dash_kkumi INNER + window.__og (late 늦게 생김 · second 두 번째 확인에서야 · never 안 생김 · enter Enter 로 카드 · bs 고른 줄 Backspace 로 지움).
"""
from __future__ import annotations

import json
import subprocess

import pytest

from dealbot.dashboard import autopub, kkumi
from test_dash_autopub import FLOW, NODE, _editor, _loader, _tags_line, needs_node  # noqa: F401
from test_dash_kkumi import INNER, PASTED, RUN, _playwright_env, code

OG = r"""<script>(() => {
const C = document.querySelector('.se-content'), o = window.__og || {}, ob = document.querySelector("[data-name='oglink']"), f = ob.onclick;
window.__ogn = 0; window.__cf = 0;
ob.onclick = () => { window.__ogn++; f(); const pop = [...document.querySelectorAll('.se-popup-oglink')].pop(), b = pop.querySelector('.se-popup-button-confirm'), g = b.onclick;
  b.onclick = () => { window.__cf++;
    if (o.never) { pop.remove(); return; }
    if (o.second && window.__cf < 2) return;
    if (o.late) { const u = pop.querySelector('input').value; pop.remove(); setTimeout(() => { const c = document.createElement('div'); c.className = 'se-component se-oglink'; c.innerHTML = '<a>' + u + '</a>'; C.append(c); }, o.late); return; }
    g(); }; };
C.addEventListener('keydown', e => {
  const l = C.lastElementChild;
  if (e.key === 'Enter' && o.enter && l && /^https?:\/\/\S+$/.test(l.textContent.trim())) { l.className = 'se-component se-oglink'; l.innerHTML = '<a>' + l.textContent.trim() + '</a>'; }
  if (e.key === 'Backspace' && o.bs) { const a = getSelection().anchorNode, c = a && (a.nodeType === 1 ? a : a.parentElement).closest('.se-component'); if (c && getSelection().toString().trim() === c.textContent.trim()) c.remove(); }
});
})();</script></body></html>"""

RUN15 = RUN.replace("i < 400 && !alerts.length", "i < 1500 && !alerts.length").replace(
    "out.tags =", "out.og = await frame.evaluate(() => [window.__ogn, window.__cf]);out.tags =")
assert RUN15.count("i < 1500") == 1 and "out.og" in RUN15


def _kk(og: dict) -> dict:
    env = _playwright_env()
    if env is None:
        pytest.skip("node playwright·크롬 없음")
    inner = INNER.replace("</body></html>", f"<script>window.__og = {json.dumps(og)};</script></body></html>").replace("</body></html>", OG)
    out = subprocess.run([NODE, "-e", RUN15, inner, code(), PASTED], capture_output=True, text=True, timeout=240, env=env)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _kinds(comps: list[str]) -> list[str]:
    k = [c.split(" ")[0] for c in comps]
    return [x for i, x in enumerate(k) if not (x == "TEXT" and i and k[i - 1] == "TEXT")]


SHAPE = ["TEXT", "LINE", "TEXT", "POSTIT", "TEXT", "CARD", "TEXT", "LINE", "TEXT"]
NOCARD = ["TEXT", "LINE", "TEXT", "POSTIT", "TEXT", "LINE", "TEXT"]


# ---------------------------------------------------------------- 꾸미기 (가짜 글쓰기 화면)
def test_v15_card_that_forms_after_ten_seconds_is_waited_for_in_place() -> None:
    got = _kk({"late": 10000})  # v14 는 8초에 '카드 안 생김'
    assert got["alert"].startswith("모두 됐어요") and "링크 카드 1/1" in got["alert"], got["alert"]
    assert _kinds(got["comps"]) == SHAPE and got["og"] == [1, 1], (got["comps"], got["og"])


def test_v15_confirm_is_pressed_once_more_when_the_popup_stays() -> None:
    got = _kk({"second": 1})
    assert got["alert"].startswith("모두 됐어요") and "링크 카드 1/1" in got["alert"], got["alert"]
    assert _kinds(got["comps"]) == SHAPE and got["og"] == [1, 2], got["og"]


def test_v15_enter_at_the_end_of_our_url_line_can_make_the_card_without_the_toolbar() -> None:
    got = _kk({"enter": 1})
    assert got["alert"].startswith("모두 됐어요") and "링크 카드 1/1" in got["alert"], got["alert"]
    assert _kinds(got["comps"]) == SHAPE and got["og"] == [0, 0], got["og"]  # 툴바 링크 단추를 안 누름
    assert not any(c.startswith("TEXT") and "https://t.me" in c for c in got["comps"])  # 주소 줄이 카드가 됨


def test_v15_card_that_never_forms_drops_our_url_line_and_goes_on_with_a_note() -> None:
    got = _kk({"never": 1, "bs": 1})
    a = got["alert"]
    assert a.startswith("모두 됐어요") and "카드 안 됨 (주소 줄 뺌)" in a and "링크 카드" not in a, a  # 원래 차례에서도 카드를 뺌
    assert _kinds(got["comps"]) == NOCARD and not any("t.me" in c for c in got["comps"]), got["comps"]
    assert any("카드 아래 가운데 문단" in c for c in got["comps"]) and any("포스트잇 아래 가운데" in c for c in got["comps"])  # 다른 줄은 그대로
    assert got["og"] == [2, 2]  # 툴바로 두 번


def test_v15_url_line_that_cannot_be_deleted_is_named_for_the_owner() -> None:
    got = _kk({"never": 1})
    a = got["alert"]
    assert a.startswith("확인해 주세요") and "카드 안 됨 — 주소 줄을 지워 주세요 t.me/Derens_tip" in a, a
    assert "원래:" in a and a.split("차례: ")[1].split("\n")[0] == a.split("원래: ")[1].split("\n")[0], a  # 차례는 맞음 (카드 뺀 원래 차례)
    assert any(c.startswith("TEXT") and "https://t.me" in c for c in got["comps"])  # 스스로는 못 지움 — 주인 몫


def test_v15_card_code_touches_only_our_url_line() -> None:
    src = kkumi.SRC.read_text(encoding="utf-8")
    c = src[src.index("const card = async u => {"):src.index("const LINE_OPENERS")]
    assert "L = lastPara()" in c and "txt(L)" in c and c.count("my() === u") == 2  # Enter·지우기 모두 글이 그 주소 하나인 본문 끝 줄에서만
    for word in ("execCommand", ".remove(", "innerHTML", "textContent =", "deleteContents"):  # 스크립트로 글을 지우지 않음 (키만)
        assert word not in c, word
    assert kkumi.version() == 16 and autopub.version() == 11 and len(kkumi.bookmarklet()) < 31000


# ---------------------------------------------------------------- 하루치 (점검·발행)
FIX = "        await f.click('#apb button');\n        continue;\n      }\n      if (!opt.fix"
assert FLOW.count(FIX) == 1
FLOW15 = FLOW.replace(FIX, "        if (opt.fix === 'url') { out.stop_bar = bar; await ed.evaluate(() => { for (const c of document.querySelectorAll('.se-content > .se-component')) if (/^https:/.test(c.textContent.trim())) c.remove(); }); }\n" + FIX)

URL = "https://blog.naver.com/derenpick/224000000001"


def _post() -> dict:
    return {"id": "pub:issue:21", "title": "코르티스 대상 네 개 정리", "at": "", "label": "지금", "photos": 0, "disc": False, "text": "",
            "html": "<p>코르티스 첫 문단이에요.</p><p>둘째 줄이에요 길게.</p><p>같이 보면 좋은 글</p>"
                    f"<p>{URL}</p><p>카드 아래 끝 문단이에요.</p>" + _tags_line(["코르티스", "마마"], "셀럽 소식")}


def _ap(og: dict, **opt) -> dict:
    env = _playwright_env()
    if env is None:
        pytest.skip("node playwright·크롬 없음")
    ed = _editor().replace("</body></html>", f"<script>window.__og = {json.dumps(og)};</script></body></html>").replace("</body></html>", OG)
    out = subprocess.run([NODE, "-e", FLOW15, ed, _loader(), json.dumps([_post()], ensure_ascii=False), json.dumps({"mode": "xhr", **opt})],
                         capture_output=True, text=True, timeout=300, env=env)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


@needs_node
def test_v11_late_card_publishes_without_a_stop() -> None:
    got = _ap({"late": 10000})
    assert [d["r"] for d in got["done"]] == ["hold", "ok"] and not got["done"][-1]["n"], got["done"]
    assert got["end"].endswith(" · v11") and "올린 글 1" in got["end"], got["end"]


@needs_node
def test_v11_dropped_card_does_not_trip_the_order_check_and_publishes_with_a_note() -> None:
    got = _ap({"never": 1, "bs": 1})
    assert [d["r"] for d in got["done"]] == ["hold", "ok"], got["done"]
    assert got["done"][-1]["n"] == "카드 안 됨 (주소 줄 뺌)" and "카드 안 됨" in got["lines"][0], (got["done"], got["lines"])
    assert URL not in got["pub"][0]["text"] and "카드 아래 끝 문단이에요." in got["pub"][0]["text"]


@needs_node
def test_v11_url_line_left_stops_once_names_it_and_publishes_after_the_owner_deletes_it() -> None:
    got = _ap({"never": 1}, fix="url")
    st = [d for d in got["done"] if d["r"] == "stop"]
    assert len(st) == 1 and "카드 안 됨 — 주소 줄을 지워 주세요 blog.naver.com/derenpick/2240" in st[0]["n"], got["done"]
    assert "글 차례가 원고와 달라요" not in st[0]["n"] and "링크 카드" not in st[0]["n"], st[0]["n"]  # 뺀 카드로 차례 점검이 걸리지 않음
    assert [d["r"] for d in got["done"]][-2:] == ["hold", "ok"] and len(got["pub"]) == 1 and URL not in got["pub"][0]["text"], got["done"]
