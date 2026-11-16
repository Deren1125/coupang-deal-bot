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
