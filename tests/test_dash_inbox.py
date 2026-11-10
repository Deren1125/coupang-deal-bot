"""결재함 (주인 11/10 — "대시보드에 결재함을 새로 만들어서 실시간으로 직접 보내게" · owner-inbox 가지 README).

1) clean_items·clean_digest: 엄격히 거름 (closed·틀린 id·겹친 id 빼기 · once→decide→info 순 · 세션 id 를 화면에 안 보냄 · 토큰 모양 가림).
2) clean_send: 빈 답은 빼고 · 다 비면 거절 · 틀린 모양 거절.
3) 진짜 git (임시 '원격' 저장소): sync 가 가지를 받아 worktree 를 만들고 · send 가 answers/<ms>.json 을 바로 올림 ·
   그사이 원격이 바뀌면 rebase 뒤 다시 · 올리기가 막히면 커밋은 남기고 다음 sync 가 다시 올림 · 강제 push 없음.
4) GET /dashboard/api/inbox (로그인 아래 · no-store) · inbox.send (틀린 args 는 bad_args).
5) inbox.js: 바깥 주소·innerHTML 없음 · 셸이 defer 로 받음 · 크기 한도.
"""

from __future__ import annotations

import gzip
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from dash_helpers import get_json, post_json, push_settings, session_headers
from dealbot.app import DealBot
from dealbot.config import Settings
from dealbot.dashboard import api as dash_api
from dealbot.dashboard import inbox, shell

STATIC = Path(__file__).resolve().parents[1] / "src" / "dealbot" / "dashboard" / "static"
# 11/10: 결재함 (할 일 맨 위 줄 + 화면 전체 카드·선택지·답 칸·복사·했어요·한 번에 보내기·밤사이 소식 탭) — 셸이 defer 로 받는 inbox.js
INBOX_JS_MAX = 17 * 1024
INBOX_JS_GZIP_MAX = 6 * 1024 + 512

ITEMS = {"updated_at": 1794287790000, "title": "11월 10일 결재함", "items": [
    {"id": "B1", "group": "blog", "kind": "decide", "risk": "돈", "status": "open", "title": "모델 바꿀 곳", "question": "어디를?",
     "recommend": "코디 감독만", "background": ["배경 1", "토큰 ghp_abcdefghijklmnopqrstuvwxyz0123456789AB 은 가림", 3],
     "options": ["코디 감독만", "세 곳 모두", "보류"], "targets": [{"name": "메인 6", "session_id": "session_secret_id", "via": "msg"}],
     "created_at": 2, "secret_field": "버림"},
    {"id": "C0", "group": "common", "kind": "once", "risk": "일반", "status": "open", "title": "한 번", "copy": "붙여 넣을 문구", "created_at": 5},
    {"id": "N1", "group": "perp", "kind": "info", "risk": "이상함", "status": "open", "title": "소식", "copy": "info 는 복사 없음", "created_at": 1},
    {"id": "S4", "group": "shorts", "kind": "decide", "status": "sent", "title": "보낸 것", "sent_at": 9, "sent_note": "전함"},
    {"id": "X9", "group": "blog", "kind": "decide", "status": "closed", "title": "닫힘"},
    {"id": "B1", "group": "blog", "kind": "decide", "status": "open", "title": "같은 id"},
    {"id": "bad id!", "status": "open", "title": "틀린 id"}, "줄 아님"]}
DIGEST = {"digest": {"blog": {"period": "밤사이", "bullets": [{"t": "13:56", "who": "메인 6", "text": "권한 설정"}, {"t": "1", "text": ""}]},
                     "perp": {"bullets": [{"t": "02:00", "who": "봇", "text": "가상매매"}]}, "nope": {}},
          "server": {"lines": ["메모리 여유 1038MB", 5]}}


# ---------------------------------------------------------------- 1) 거르기
def test_clean_items_strict_and_ordered() -> None:
    got = inbox.clean_items(ITEMS)
    assert [x["id"] for x in got["open"]] == ["C0", "B1", "N1"]  # once → decide → info
    assert [x["id"] for x in got["sent"]] == ["S4"] and got["title"] == "11월 10일 결재함"
    b1 = got["open"][1]
    assert b1["options"] == ["코디 감독만", "세 곳 모두", "보류"] and b1["targets"] == ["메인 6"] and b1["glabel"] == "블로그"
    assert len(b1["background"]) == 2 and "ghp_" not in json.dumps(got)
    assert "session_secret_id" not in json.dumps(got) and "secret_field" not in json.dumps(got)
    assert got["open"][2]["risk"] == "일반" and got["open"][2]["copy"] == "" and got["open"][0]["copy"] == "붙여 넣을 문구"
    with pytest.raises(ValueError):
        inbox.clean_items({"no": 1})


def test_clean_digest() -> None:
    got = inbox.clean_digest(DIGEST)
    assert [t["key"] for t in got["tabs"]] == ["perp", "blog"] and got["tabs"][1]["bullets"] == [{"t": "13:56", "who": "메인 6", "text": "권한 설정"}]
    assert got["server"] == ["메모리 여유 1038MB"]
    assert inbox.clean_digest(None) == {"tabs": [], "server": []}


# ---------------------------------------------------------------- 2) 보내기 args
def test_clean_send() -> None:
    got = inbox.clean_send({"answers": [{"id": "B1", "choice": 1, "text": " 좋아요 "}, {"id": "P1", "choice": None, "text": "  "},
                                        {"id": "P2", "text": "글만"}], "done": ["C0", "C0"]})
    assert got == {"answers": [{"id": "B1", "choice": 1, "text": "좋아요"}, {"id": "P2", "choice": None, "text": "글만"}], "done": ["C0"]}
    for bad in ({"answers": "x"}, {"answers": [{"id": "B1", "choice": 0}]}, {"answers": [{"id": "B1", "choice": True}]},
                {"answers": [{"id": "B 1", "choice": 1}]}, {"answers": [{"id": "B1", "text": "x" * 4001}]}, {"done": [3]},
                {"answers": [{"id": "B1", "choice": 1}, {"id": "B1", "choice": 2}]}, {"answers": [{"id": "B1", "text": ""}]}, {}):
        with pytest.raises(ValueError):
            inbox.clean_send(bad)


# ---------------------------------------------------------------- 3) 진짜 git
def git(*args: str, cwd: Path) -> str:
    r = subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", "-c", "init.defaultBranch=main", *args],
                       cwd=cwd, capture_output=True, text=True, check=True)
    return r.stdout.strip()


@pytest.fixture
def remote(tmp_path: Path) -> tuple[Path, Path, Path]:
    """(원격 bare, 서버의 Blog-Auto 클론, 중계 세션 흉내 클론)."""
    bare = tmp_path / "remote.git"
    git("init", "-q", "--bare", str(bare), cwd=tmp_path)
    seed = tmp_path / "seed"
    git("init", "-q", str(seed), cwd=tmp_path)
    (seed / "README.md").write_text("main\n", encoding="utf-8")
    git("add", ".", cwd=seed)
    git("commit", "-qm", "main", cwd=seed)
    git("remote", "add", "origin", str(bare), cwd=seed)
    git("push", "-q", "origin", "HEAD:refs/heads/main", cwd=seed)
    git("checkout", "-q", "--orphan", "owner-inbox", cwd=seed)
    git("rm", "-rqf", ".", cwd=seed)
    (seed / "inbox").mkdir()
    (seed / "answers").mkdir()
    (seed / "answers" / ".keep").write_text("", encoding="utf-8")
    (seed / "inbox" / "items.json").write_text(json.dumps(ITEMS, ensure_ascii=False), encoding="utf-8")
    (seed / "inbox" / "digest.json").write_text(json.dumps(DIGEST, ensure_ascii=False), encoding="utf-8")
    git("add", ".", cwd=seed)
    git("commit", "-qm", "inbox", cwd=seed)
    git("push", "-q", "origin", "HEAD:refs/heads/owner-inbox", cwd=seed)
    repo = tmp_path / "Blog-Auto"
    git("clone", "-q", str(bare), str(repo), cwd=tmp_path)
    return bare, repo, seed


def answers_on_remote(bare: Path) -> list[dict]:
    names = git("ls-tree", "--name-only", "owner-inbox", "answers/", cwd=bare).split()
    return [json.loads(git("show", f"owner-inbox:{n}", cwd=bare)) for n in names if n.endswith(".json")]


def test_sync_send_and_rebase(remote: tuple[Path, Path, Path], tmp_path: Path) -> None:
    bare, repo, seed = remote
    t = [1794300000.0]
    ib = inbox.Inbox(repo, tmp_path / "data" / "owner-inbox", clock=lambda: t[0])
    assert ib.read()["open"] == [] and ib.read()["bad"] == ["items", "digest"]
    assert ib.sync() == "ok" and ib.state["state"] == "ok"
    v = ib.read()
    assert [x["id"] for x in v["open"]] == ["C0", "B1", "N1"] and v["digest"]["server"] == ["메모리 여유 1038MB"]
    # 중계 세션이 그사이 원격을 바꿈 → 첫 push 는 막히고 rebase 뒤 올라감
    items2 = dict(ITEMS, title="바뀐 결재함")
    (seed / "inbox" / "items.json").write_text(json.dumps(items2, ensure_ascii=False), encoding="utf-8")
    git("commit", "-qam", "relay", cwd=seed)
    git("push", "-q", "origin", "HEAD:refs/heads/owner-inbox", cwd=seed)
    st, text = ib.send([{"id": "B1", "choice": 2, "text": "좋아요"}], ["C0"])
    assert st == "done" and "보냈어요" in text, text
    (got,) = answers_on_remote(bare)
    assert got == {"at": 1794300000000, "answers": [{"id": "B1", "choice": 2, "choice_text": "세 곳 모두", "text": "좋아요"}], "done": ["C0"]}
    assert json.loads(git("show", "owner-inbox:inbox/items.json", cwd=bare))["title"] == "바뀐 결재함"  # 중계 판을 덮지 않음
    # 같은 ms 에 또 보내면 파일 이름이 겹치지 않음
    st, _ = ib.send([{"id": "B1", "choice": None, "text": "하나 더"}], [])
    assert st == "done" and len(answers_on_remote(bare)) == 2
    assert ib.sync() == "ok" and ib.read()["title"] == "바뀐 결재함" and ib.state["pending"] == 0


def test_push_blocked_keeps_commit_and_sync_retries(remote: tuple[Path, Path, Path], tmp_path: Path) -> None:
    bare, repo, _seed = remote
    hook = bare / "hooks" / "pre-receive"
    hook.write_text("#!/bin/sh\necho denied >&2\nexit 1\n", encoding="utf-8")
    hook.chmod(0o755)
    ib = inbox.Inbox(repo, tmp_path / "data" / "owner-inbox", clock=lambda: 1794300000.0)
    assert ib.sync() == "ok"
    st, text = ib.send([], ["N1"])
    assert st == "queued" and "1분마다 다시" in text and ib.state["pending"] == 1
    assert answers_on_remote(bare) == []
    assert ib.sync() != "ok" and ib.state["state"] == "down" and ib.state["pending"] == 1  # 아직 막힘 — 커밋은 남김
    hook.unlink()
    assert ib.sync() == "ok" and ib.state["pending"] == 0
    assert [a["done"] for a in answers_on_remote(bare)] == [["N1"]]


def test_no_repo_is_a_reason_not_a_crash(tmp_path: Path) -> None:
    ib = inbox.Inbox(tmp_path / "nothing", tmp_path / "wt")
    assert ib.sync() == "Blog-Auto 저장소를 못 찾음" and ib.state["state"] == "down"
    assert ib.send([], ["N1"])[0] == "failed"


def test_never_force_pushes() -> None:
    src = (Path(inbox.__file__)).read_text(encoding="utf-8")
    assert "--force" not in src and '"-f"' not in src and "+HEAD" not in src


# ---------------------------------------------------------------- 4) API
@pytest.fixture
def bot(settings: Settings, tmp_path: Path, remote: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv(inbox.REPO_ENV, str(remote[1]))
    settings.dashboard.blogauto_metrics_path = str(tmp_path / "blogauto.json")
    b = DealBot(push_settings(settings))
    yield b
    b.db.close()


async def test_api_view_and_send(bot: DealBot, remote: tuple[Path, Path, Path]) -> None:
    h = session_headers(bot)
    status, v, headers = await get_json(bot, "/dashboard/api/inbox", h)
    assert status == 200 and headers["Cache-Control"] == "no-store" and v["state"] == "wait" and v["count"] == 0
    assert await inbox.tick(bot) == "ok" and await inbox.tick(bot) is None  # 1분이 안 지나면 그냥 돌아옴
    status, v, _ = await get_json(bot, "/dashboard/api/inbox", h)
    assert v["state"] == "ok" and v["count"] == 3 and v["err"] is None and "session_" not in json.dumps(v)
    body = {"action": "inbox.send", "args": {"answers": [{"id": "B1", "choice": 1, "text": ""}], "done": []}}
    status, got, _ = await post_json(bot, "/dashboard/api/action", body, h)
    assert status == 200 and got["status"] == "ok" and "보냈어요" in got["text"], got
    (a,) = answers_on_remote(remote[0])
    assert a["answers"] == [{"id": "B1", "choice": 1, "choice_text": "코디 감독만", "text": ""}]
    for bad in ({"answers": [], "done": []}, {"answers": [{"id": "B1", "choice": 99}]}, {"done": ["x y"]}):
        status, got, _ = await post_json(bot, "/dashboard/api/action", {"action": "inbox.send", "args": bad}, h)
        assert status == 400 and got["code"] == "bad_args", bad
    anon = {":peer": "127.0.0.1", "x-forwarded-for": "203.0.113.7"}
    assert (await bot.web.dispatch("GET", "/dashboard/api/inbox", anon))[0] == 401


# ---------------------------------------------------------------- 5) inbox.js
def test_inbox_js_local_safe_and_wired() -> None:
    js = (STATIC / "inbox.js").read_text(encoding="utf-8")
    assert "http://" not in js and "https://" not in js and "innerHTML" not in js and "eval(" not in js and "new Function" not in js
    for word in ("/dashboard/api/inbox", "inbox.send", "W.SBInbox", "'한 번에 보내기", "'문구 복사'", "'했어요'", "배경 자료", "보낸 것", "밤사이 소식",
                 "결재함", "localStorage"):
        assert word in js, word
    assert len(js.encode()) <= INBOX_JS_MAX and len(gzip.compress(js.encode(), 9)) <= INBOX_JS_GZIP_MAX
    html = shell.page({"me": {}})
    assert f'<script defer src="{shell.asset_url("inbox.js")}"></script>' in html and html.count("<script src=") == 1
    assert "inbox.js" in dash_api.APP_FILES
    node = shutil.which("node")
    if node:
        assert subprocess.run([node, "--check", str(STATIC / "inbox.js")], capture_output=True).returncode == 0


# 진짜 크롬 (ops 원칙 24 — '[object'·'undefined'·'NaN' 없음 · 콘솔 오류 없음 · 폰 너비에서 가로로 안 넘침 · 단추 44px)
BROWSER = r"""
const {chromium} = require('playwright');
const fs = require('fs');
(async () => {
  const [js, css, view, shot] = [fs.readFileSync(process.argv[1], 'utf8'), fs.readFileSync(process.argv[2], 'utf8'), process.argv[3], process.argv[4]];
  const b = await chromium.launch();
  const p = await b.newPage({viewport: {width: 360, height: 800}});
  const errors = [], posts = [];
  p.on('console', m => { if (m.type() === 'error') errors.push(m.text()); });
  p.on('pageerror', e => errors.push(String(e)));
  const html = '<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">' +
    '<link rel="stylesheet" href="/dashboard/static/app.css"></head><body><main id="main"><section id="rtp" class="rt"><p class="sl">오늘 할 일</p></section></main>' +
    '<div class="toast" id="toast" hidden></div><script type="application/json" id="boot">{"me":{"csrf":"t"}}</script>' +
    '<script defer src="/dashboard/static/inbox.js"></script></body></html>';
  await p.route('http://dash.test/**', async r => {
    const u = new URL(r.request().url());
    const json = (status, d) => r.fulfill({status, contentType: 'application/json', body: JSON.stringify(d)});
    if (u.pathname === '/dashboard/') return r.fulfill({contentType: 'text/html', body: html});
    if (u.pathname.endsWith('app.css')) return r.fulfill({contentType: 'text/css', body: css});
    if (u.pathname.endsWith('inbox.js')) return r.fulfill({contentType: 'text/javascript', body: js});
    if (u.pathname === '/dashboard/api/inbox') return json(200, JSON.parse(view));
    if (u.pathname === '/dashboard/api/action') { posts.push(JSON.parse(r.request().postData())); return json(200, {status: 'ok', text: '3개 보냈어요'}); }
    return r.fulfill({status: 404, body: ''});
  });
  await p.goto('http://dash.test/dashboard/#/todo');
  await p.waitForSelector('#rtp > #ibr');
  const out = {row: await p.$eval('#ibr', e => e.innerText)};
  // 칸을 다시 그려도(routine.js 처럼 통째로) 줄이 다시 붙음
  await p.evaluate(() => document.getElementById('rtp').replaceChildren(Object.assign(document.createElement('p'), {className: 'sl', textContent: '새로'})));
  await p.waitForSelector('#rtp > #ibr');
  await p.click('#ibr');
  await p.waitForSelector('#ibx .cd');
  if (shot) await p.screenshot({path: shot, fullPage: false});
  await p.click('#ibx .op .ib >> nth=1');
  await p.fill('#ibx textarea', '좋아요');
  await p.click('#ibx .rw .ib:has-text("했어요")');
  await p.click('#ibx .rw .ib:has-text("읽음")');
  out.bar = await p.$eval('#ibx .sb .ib', e => e.innerText);
  out.text = await p.$eval('#ibx', e => e.innerText);
  out.wide = await p.evaluate(() => Math.max(document.documentElement.scrollWidth, document.getElementById('ibx').scrollWidth));
  out.btn = await p.$$eval('#ibx .ib', es => Math.min(...es.map(e => Math.round(e.getBoundingClientRect().height))));
  await p.click('#ibx .sb .ib');
  await p.waitForFunction(() => document.querySelector('#ibx .cd.gone'));
  out.after = await p.$eval('#ibx', e => e.innerText);
  out.posts = posts; out.errors = errors;
  console.log(JSON.stringify(out));
  await b.close();
})().catch(e => { console.error(String(e)); process.exit(2); });
"""


def test_real_browser_inbox(tmp_path: Path) -> None:
    import os

    from test_dash_kkumi import _playwright_env

    env = _playwright_env()
    if env is None:
        pytest.skip("node playwright·크롬 없음")
    view = {**inbox.clean_items(ITEMS), "digest": inbox.clean_digest(DIGEST), "bad": [], "state": "ok", "err": None, "pending": 0, "count": 3}
    out = subprocess.run([shutil.which("node") or "node", "-e", BROWSER, str(STATIC / "inbox.js"), str(STATIC / "app.css"),
                          json.dumps(view, ensure_ascii=False), os.environ.get("INBOX_SHOT", "")],
                         capture_output=True, text=True, timeout=120, env=env)
    assert out.returncode == 0, out.stderr
    got = json.loads(out.stdout)
    assert got["errors"] == []
    for bad in ("[object", "undefined", "NaN", "null"):
        assert bad not in got["text"] and bad not in got["row"], bad
    assert "결재함" in got["row"] and "3개" in got["row"]
    for word in ("모델 바꿀 곳", "추천: 코디 감독만", "배경 자료 2개", "1. 코디 감독만", "붙여 넣을 문구", "문구 복사", "보낸 것 1개", "밤사이 소식", "퍼프덱스",
                 "→ 메인 6", "한 번 할 일", "정해 주세요", "알려 드림"):
        assert word in got["text"], word
    assert got["bar"] == "한 번에 보내기 (3)" and got["wide"] <= 360 and got["btn"] >= 44
    (sent,) = got["posts"]
    assert sent["action"] == "inbox.send" and sent["args"] == {"answers": [{"id": "B1", "choice": 2, "text": "좋아요"}], "done": ["C0", "N1"]}
    assert "보냈어요 — 중계 세션이 전하는 중" in got["after"]
