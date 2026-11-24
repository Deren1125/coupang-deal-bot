"""'하루치 한 번에' v6 (주인 11/24 10:28·10:32·10:35).

- 사진: 원고의 90% 넘고 2장까지 빠지면 발행하고 요약 줄에 '사진 N장 빠짐' · 더 빠지면 그대로 멈춤 · 원고보다 많으면(같은 사진 겹침) 멈춤.
- 태그: 90% 넘거나 1개만 빠지면 발행하고 요약에 빠진 태그 이름 · 2개 넘게(90% 아래) 빠지면 멈춤.
- 가운데 블로그(드라마·데런픽): 주인이 누른 빈 본문 줄에서 가운데 단추를 먼저 눌러 붙여넣는 글이 가운데를 이어받음 ·
  원고에서 가운데인 글 줄이 화면에서 2줄 넘게 가운데가 아니면 멈춤 ('전체 선택 → 가운데 단추').
가짜 글쓰기 화면은 test_dash_autopub 그대로 (붙여넣은 글 줄은 에디터의 지금 정렬을 따르고, 가운데 단추는 지금 정렬과 마지막 글 덩어리를 바꿈 —
붙여넣기가 정렬을 잃는 아이패드처럼).
"""
from __future__ import annotations

import json
import subprocess

import pytest

from test_dash_autopub import FLOW, NODE, _editor, _loader, _tags_line, needs_node
from test_dash_kkumi import _playwright_env

FLOW6 = FLOW.replace("return opt.nophoto ? r.abort()", "return (opt.nophoto || (opt.miss || []).some(x => u.pathname.includes(x))) ? r.abort()")
assert FLOW6 != FLOW


def _run(posts: list[dict], mods: tuple[tuple[str, str], ...] = (), timeout: int = 300, **opt) -> dict:
    env = _playwright_env()
    if env is None:
        pytest.skip("node playwright·크롬 없음")
    ed = _editor()
    for a, b in mods:
        assert ed.count(a) == 1, a
        ed = ed.replace(a, b)
    out = subprocess.run([NODE, "-e", FLOW6, ed, _loader(), json.dumps(posts, ensure_ascii=False), json.dumps({"mode": "xhr", **opt})],
                         capture_output=True, text=True, timeout=timeout, env=env)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


def _pic(i: int) -> str:
    return f'<p style="text-align:center"><img src="https://dash.test/media/blog_issue_11_{i}_abcdef{i:02d}.jpg"></p>'


def _post(n_pics: int = 0, tags: tuple[str, ...] = ("아일릿", "키링"), center: bool = False) -> dict:
    st = ' style="text-align:center"' if center else ""
    body = "".join(f"<p{st}>본문 {i}번째 줄이에요 길게.</p>" + (_pic(i) if i < n_pics else "") for i in range(max(4, n_pics)))
    return {"id": "pub:issue:11", "title": "첫 글 제목이에요", "at": "", "label": "지금", "photos": n_pics, "disc": False, "text": "",
            "html": f"<p{st}>첫 글 첫 문단.</p>" + body + f"<p{st}>첫 글 끝 문단.</p>" + _tags_line(list(tags), "셀럽 소식")}


@needs_node
def test_one_missing_photo_of_ten_publishes_and_says_so_in_the_summary() -> None:
    got = _run([_post(10)], miss=["_3_abcdef03"])
    assert [d["r"] for d in got["done"]] == ["hold", "ok"], got["done"]
    assert got["done"][-1]["n"] == "사진 1장 빠짐" and len(got["pub"]) == 1
    assert got["lines"][0].startswith("✓ 올렸어요") and "사진 1장 빠짐" in got["lines"][0], got["lines"]


@needs_node
def test_three_missing_photos_still_stop_before_publish() -> None:
    got = _run([_post(10)], miss=["_2_abcdef02", "_3_abcdef03", "_4_abcdef04"], give_up=40)
    assert got["pub"] == [] and got["done"][0]["r"] == "stop" and "사진 7/10 — 빠진 사진을 넣어 주세요" in got["done"][0]["n"], got["done"]


@needs_node
def test_two_missing_of_ten_is_below_ninety_percent_and_stops() -> None:
    got = _run([_post(10)], miss=["_2_abcdef02", "_3_abcdef03"], give_up=40)
    assert got["pub"] == [] and "사진 8/10" in got["done"][0]["n"], got["done"]


@needs_node
def test_a_photo_that_landed_twice_stops() -> None:
    twice = ("if (e.clipboardData.files.length) { const c = add(comp('se-image', '<img src=\"data:image/svg+xml;base64,PHN2\">'));",
             "if (e.clipboardData.files.length) { add(comp('se-image', '<img src=\"https://blogfiles.pstatic.net/twin.jpg\">')); const c = add(comp('se-image', '<img src=\"data:image/svg+xml;base64,PHN2\">'));")
    got = _run([_post(3)], mods=(twice,), give_up=40)
    assert got["pub"] == [] and "사진 3장 더 — 겹친 사진을 지워 주세요" in got["done"][0]["n"], got["done"]


REJECT = ("d.querySelector('input').addEventListener('keydown', e => { if (e.key === 'Enter') {",
          "d.querySelector('input').addEventListener('keydown', e => { if (e.key === 'Enter' && /거부/.test(e.target.value)) return; if (e.key === 'Enter') {")


@needs_node
def test_one_tag_the_editor_refuses_publishes_and_names_it() -> None:
    got = _run([_post(0, ("아일릿", "키링", "거부태그"))], mods=(REJECT,))
    assert [d["r"] for d in got["done"]] == ["hold", "ok"], got["done"]
    assert got["done"][-1]["n"] == "태그 2/3 빠짐 거부태그" and got["pub"][0]["tags"].split() == ["#아일릿", "#키링"]
    assert "태그 2/3 빠짐 거부태그" in got["lines"][0]


@needs_node
def test_two_refused_tags_stop() -> None:
    got = _run([_post(0, ("아일릿", "거부하나", "거부둘", "키링"))], mods=(REJECT,), give_up=40)
    assert got["pub"] == [] and "태그 2/4 빠짐 거부하나,거부둘" in got["done"][0]["n"], got["done"]


@needs_node
def test_center_post_gets_center_from_the_body_tap_and_publishes_clean() -> None:
    got = _run([_post(2, center=True)])
    assert [d["r"] for d in got["done"]] == ["hold", "ok"], got["done"]
    assert "가운데 아닌 줄" not in (got["done"][-1]["n"] or ""), got["done"]


@needs_node
def test_center_post_whose_lines_stay_left_stops_and_tells_how_to_fix() -> None:
    # 도구줄 가운데 단추가 먹지 않는 화면 — 붙여넣은 글 줄이 모두 왼쪽에 남음
    hide = ("""document.querySelector("[data-value='center']").onclick = () => { align = 'center';""", """document.querySelector("[data-value='center']").onclick = () => { return; align = 'center';""")
    got = _run([_post(0, center=True)], mods=(hide,), give_up=40)
    assert got["pub"] == [] and got["done"][0]["r"] == "stop", got["done"]
    assert "가운데 아닌 줄 " in got["done"][0]["n"] and "본문 「전체 선택」 → 위 가운데 단추" in got["done"][0]["n"], got["done"]


@needs_node
def test_left_blog_lines_are_not_checked_for_center() -> None:
    hide = ("""document.querySelector("[data-value='center']").onclick = () => { align = 'center';""", """document.querySelector("[data-value='center']").onclick = () => { return; align = 'center';""")
    got = _run([_post(0)], mods=(hide,))
    assert [d["r"] for d in got["done"]] == ["hold", "ok"] and not got["done"][-1]["n"], got["done"]
