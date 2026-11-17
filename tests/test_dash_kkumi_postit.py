"""꾸미기 책갈피 — 구분선 없이 글 바로 다음 포스트잇 (주인 11/16 아이패드 드라마 글: 장면 소제목 5개는 '선 포스트잇' 이라 됐고,
끝의 시청률 표 인용구(table_mode quote — 앞에 선 없음)만 '포스트잇: 글 밖 · 포스트잇 글이 안 들어감').

가짜 편집기를 아이패드 네이버처럼 두 가지 더:
- 붙여넣기는 커서(선택)가 있는 칸에 들어감 · 글 덩어리 다음에 넣은 포스트잇 뒤에는 빈 글 줄이 하나 따라옴 (본문 맨 끝 줄 ≠ 포스트잇)
- 포스트잇 안 글은 숫자 앞 빈칸이 &nbsp; 로 바뀜 ('1회 · 4.0%' → '1회 ·&nbsp;4.0%')
"""

from __future__ import annotations

import json
import subprocess

import pytest

from test_dash_kkumi import INNER, NODE, RUN, _playwright_env, code

FOLLOW = INNER.replace(
    "C.addEventListener('paste', e => {\n  e.preventDefault();",
    "C.addEventListener('paste', e => {\n  e.preventDefault();\n"
    "  { const a = getSelection().anchorNode, el = a && (a.nodeType === 1 ? a : a.parentElement); inPostit = (el && el.closest('.se-quotation')) || null; }",
).replace(
    "inPostit.querySelector('.qbox').append(q); }",
    "q.innerHTML = q.innerHTML.replace(/ (\\d)/g, '&nbsp;$1'); inPostit.querySelector('.qbox').append(q); }",
).replace(
    "const c = add(comp('se-quotation se-l-quotation_postit', '<div class=\"qbox\"></div>')); inPostit = c;",
    "const prev = C.lastElementChild; const c = add(comp('se-quotation se-l-quotation_postit', '<div class=\"qbox\"><p class=\"se-text-paragraph\"></p></div>')); inPostit = c;"
    " if (prev && /se-text/.test(prev.className) && prev.textContent.trim()) add(comp('se-text', '<div class=\"se-section se-section-align-left\"><p class=\"se-text-paragraph\"></p></div>'));",
)
assert FOLLOW.count("getSelection().anchorNode") == 1 and "&nbsp;$1" in FOLLOW and "const prev = C.lastElementChild" in FOLLOW

# 드라마 글 꼴: 글 · 선 · 소제목 포스트잇 · 글 · 선 · 소제목 포스트잇 · 글 · 표 포스트잇(앞에 선 없음) · 글
PASTED = ("<p>생일 밤이었습니다.</p>"
          "<p>⁣</p><blockquote><p>눈을 가린 술래잡기</p></blockquote><p>정년이 무대 앞에서 숨을 고릅니다.</p>"
          "<p>⁣</p><blockquote><p>8회 예고, 11/18</p></blockquote><p>그리고 첫 소리가 터집니다.</p>"
          "<blockquote><p>1회 · 4.0%</p><p>7회 (11/12) · 6.2%</p></blockquote>"
          "<p>여러분은 8회에서 누구의 선택이 바뀔 것 같나요?</p>")


def test_postit_right_after_text_gets_its_text_inside() -> None:
    env = _playwright_env()
    if env is None:
        pytest.skip("node playwright·크롬 없음")
    run = RUN.replace("out.tags =", "out.quotes = await frame.evaluate(() => [...document.querySelectorAll('.se-quotation .qbox')].map(q => q.textContent));out.tags =")
    out = subprocess.run([NODE, "-e", run, FOLLOW, code(), PASTED], capture_output=True, text=True, timeout=120, env=env)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert "포스트잇 3/3" in got["alert"] and "포스트잇: 글 안 → 나옴 → 글 안 → 나옴 → 글 안 → 나옴" in got["alert"], got["alert"]
    assert got["alert"].startswith("모두 됐어요"), got["alert"]
    q = got["quotes"]
    assert len(q) == 3 and "눈을 가린 술래잡기" in q[0] and "8회 예고" in q[1]
    assert "1회 · 4.0%" in q[2] and "6.2%" in q[2]  # 표 포스트잇: 글이 포스트잇 안에 (한 번만)
    body = [c for c in got["comps"] if c.startswith("TEXT")]
    assert not any("4.0%" in c for c in body)  # 포스트잇 밖에 표 글이 새지 않음


# ── v8 (11/17 아이패드 두 글): ① 3번째 포스트잇을 '글 밖'으로 잘못 봄(글은 안에 있었음) ② 3번째 포스트잇에서 '못 나옴' ─────────────
def _run(inner: str, pasted: str = PASTED) -> dict:
    env = _playwright_env()
    if env is None:
        pytest.skip("node playwright·크롬 없음")
    run = RUN.replace("out.tags =", "out.quotes = await frame.evaluate(() => [...document.querySelectorAll('.se-quotation .qbox')].map(q => q.textContent));out.tags =")
    out = subprocess.run([NODE, "-e", run, inner, code(), pasted], capture_output=True, text=True, timeout=120, env=env)
    assert out.returncode == 0, out.stderr
    return json.loads(out.stdout)


# 붙여 넣은 뒤 에디터가 포스트잇을 새로 그림(옛 칸은 비고 떨어져 나감) + 글 사이에 안 보이는 글자(​)
REDRAW = FOLLOW.replace(
    "inPostit.querySelector('.qbox').append(q); }",
    "q.innerHTML = q.innerHTML.replace(/·/g, '·\\u200b'); inPostit.querySelector('.qbox').append(q);"
    " const k = inPostit.cloneNode(true); inPostit.querySelector('.qbox').innerHTML = ''; inPostit.replaceWith(k); inPostit = k; }",
)
assert REDRAW.count("replaceWith(k)") == 1


def test_postit_check_requeries_the_redrawn_postit_and_ignores_invisible_spaces() -> None:
    got = _run(REDRAW)
    assert "포스트잇: 글 안 → 나옴 → 글 안 → 나옴 → 글 안 → 나옴" in got["alert"] and got["alert"].startswith("모두 됐어요"), got["alert"]
    q = got["quotes"]
    assert len(q) == 3 and "6.2%" in q[2] and sum("1회" in x for x in q) == 1  # 다시 붙이지 않음 (한 번만)


# '본문 추가'가 처음 한 번은 안 먹음 → 한 번 더 눌러 나옴
FLAKY = FOLLOW.replace(
    "document.querySelector('.se-canvas-bottom-button').onclick = () => {",
    "let miss = 1; document.querySelector('.se-canvas-bottom-button').onclick = () => { if (miss && document.querySelectorAll('.se-quotation').length === 2) { miss = 0; return; }",
)
assert FLAKY.count("let miss = 1") == 1


def test_postit_exit_retries_when_the_bottom_button_misses_once() -> None:
    got = _run(FLAKY)
    assert got["alert"].startswith("모두 됐어요") and "포스트잇 3/3" in got["alert"], got["alert"]
    assert "포스트잇: 글 안 → 나옴 → 글 안 → 나옴 → 글 안 → 나옴" in got["alert"], got["alert"]
    assert "그리고 첫 소리가" not in got["quotes"][1]  # 다음 글은 포스트잇 밖


# 2번째 포스트잇에서 끝내 못 나옴 → 다음 글이 그 안에 들어감 → 어느 포스트잇·어떤 글인지 알림
STUCK = FOLLOW.replace(
    "document.querySelector('.se-canvas-bottom-button').onclick = () => {",
    "document.querySelector('.se-canvas-bottom-button').onclick = () => { if (document.querySelectorAll('.se-quotation').length === 2) return;",
)
assert STUCK != FOLLOW


def test_postit_stuck_names_the_postit_and_the_captured_text() -> None:
    got = _run(STUCK)
    a = got["alert"]
    assert a.startswith("확인해 주세요") and "글 안 → 나옴 → 글 안 → 못 나옴" in a, a
    assert "포스트잇 2 '8회 예고, 11/18'에서 못 나옴" in a, a
    assert "포스트잇 2 안에 든 글 '그리고 첫 소리가 터집니다.'" in a, a


# v8 (11/17 픽 글 '사진 17/17 · 차례 같음' 인데 '글 안 들어감: 사진' 14번): 사진 사이 빈 줄만 든 조각은 기다릴 글·사진이 없어 늘 '안 들어감'
# 으로 셌음 (거짓 알림 · 사진 설명 줄은 제대로 들어감) → 기다리지 않고 넘어감 · 같은 '못 한 것' 줄은 한 줄 '×n' 으로
def test_blank_lines_between_photos_are_not_reported_and_repeats_collapse() -> None:
    from test_dash_kkumi import PHOTO_RUN
    env = _playwright_env()
    if env is None:
        pytest.skip("node playwright·크롬 없음")
    o = "https://example.com/x.jpg"
    pasted = (f'<p>첫 글이에요.</p><p><img src="{o}"></p><p><br></p><p><img src="{o}"></p><p>사진=홍길동 인스타그램</p>'
              f'<p><img src="{o}"></p><p><br></p><p><img src="{o}"></p><p>끝 글이에요.</p>')
    out = subprocess.run([NODE, "-e", PHOTO_RUN, INNER, code(), pasted], capture_output=True, text=True, timeout=120, env=env)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert "글 안 들어감" not in got["alert"], got["alert"]
    assert any("사진=홍길동 인스타그램" in c for c in got["comps"]), got["comps"]  # 사진 설명 줄은 들어감
    js = kkumi_src()
    assert "[...new Set(R.bad)].map(x" in js and "' ×' + n" in js


def kkumi_src() -> str:
    from dealbot.dashboard import kkumi
    return kkumi.SRC.read_text(encoding="utf-8")
