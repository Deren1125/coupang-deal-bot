"""꾸미기 v16 — 대표 사진은 한 장만 (주인 11/28 10:38 데런픽 「아이유 연말 목걸이 "골드야, 실버야?"… 헤일로 화보」 글에서
사진 두 장에 네이버 에디터 '대표' 표시가 붙음). 사진을 다 넣은 뒤(R.shape 앞) 켜진 '대표' 단추가 둘 넘으면 맨 앞 하나만 남기고 끈다.
하루치 한 번에(autopub)는 꾸미기 코드를 통째로 담으므로 같이 바뀐다."""
from __future__ import annotations

import json
import re
import subprocess

import pytest

from dealbot.dashboard import autopub, kkumi
from test_dash_autopub import NODE
from test_dash_kkumi import _playwright_env

PAGE = """<html><body><div class="se-content">
<div class="se-component se-image" id="c1"><img src="https://x/1.jpg"><button class="se-set-rep-image-button se-is-selected">대표</button></div>
<div class="se-component se-text"><p>글</p></div>
<div class="se-component se-image" id="c2"><img src="https://x/2.jpg"><button class="se-set-rep-image-button se-is-selected">대표</button></div>
<div class="se-component se-image" id="c3"><img src="https://x/3.jpg"><button class="se-set-rep-image-button">대표</button></div>
</div><script>document.querySelectorAll('button').forEach(b => b.onclick = () => { b.classList.toggle('se-is-selected'); (window.__n = (window.__n || 0) + 1); });</script>
</body></html>"""

RUN = r"""
const {chromium} = require('playwright');
(async () => {
  const b = await chromium.launch(); const p = await b.newPage();
  await p.setContent(process.argv[1]);
  const out = await p.evaluate(async src => {
    const D = document, S = m => new Promise(r => setTimeout(r, Math.min(m, 5))), qa = (e, q) => [...e.querySelectorAll(q)];
    const press = e => e.click();
    eval(src + '; window.__rep1 = rep1;');
    await window.__rep1();
    return {n: window.__n || 0, on: qa(D, '.se-image').map(c => c.querySelector('button').classList.contains('se-is-selected'))};
  }, process.argv[2]);
  console.log(JSON.stringify(out)); await b.close();
})();
"""


def _rep_src() -> str:
    m = re.search(r"^const rep1 = .*$", kkumi.SRC.read_text(encoding="utf-8"), re.M)
    assert m
    return m.group(0)


def test_v16_rep_runs_after_all_pieces_and_before_shape() -> None:
    src = kkumi.SRC.read_text(encoding="utf-8")
    assert kkumi.version() == 16 and src.count("await rep1();") == 1
    assert src.index("await rep1();") < src.index("  R.shape = shape();") and src.index("await rep1();") > src.index("R.done++;")
    js = autopub.runner() if hasattr(autopub, "runner") else ""
    assert "rep1" in (js or __import__("urllib.parse").parse.unquote(autopub.bookmarklet("https://dash.test/dashboard/api/autopub_act", "KEY")))
    assert len(kkumi.bookmarklet()) < 31000


def test_v16_two_rep_marks_become_one_on_the_first_photo() -> None:
    env = _playwright_env()
    if env is None:
        pytest.skip("node playwright·크롬 없음")
    out = subprocess.run([NODE, "-e", RUN, PAGE, _rep_src()], capture_output=True, text=True, timeout=120, env=env)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got == {"n": 1, "on": [True, False, False]}


def test_v16_single_rep_is_left_alone() -> None:
    env = _playwright_env()
    if env is None:
        pytest.skip("node playwright·크롬 없음")
    page = PAGE.replace('se-set-rep-image-button se-is-selected">대표</button></div>\n<div class="se-component se-text">',
                        'se-set-rep-image-button se-is-selected">대표</button></div>\n<div class="se-component se-text">', 1)
    page = page.replace('id="c2"><img src="https://x/2.jpg"><button class="se-set-rep-image-button se-is-selected">',
                        'id="c2"><img src="https://x/2.jpg"><button class="se-set-rep-image-button">')
    out = subprocess.run([NODE, "-e", RUN, page, _rep_src()], capture_output=True, text=True, timeout=120, env=env)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout) == {"n": 0, "on": [True, False, False]}
