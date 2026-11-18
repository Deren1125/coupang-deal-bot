"""X 화면 정리 (주인 11/18 — "X 탭 쓰기 어려움").

1. '지금 뜨는 주제'의 [글 쓰기] 한 번에 글 — draft {aggro: true, trend}: 주제·재료는 서버가 그 순위 줄에서 꺼내고(화면이 보낸 글자는 안 씀),
   끌리게 쓰되 사실은 기사 제목 안에서만 · 부풀린 말·사람 집단 편 가르기는 버림 · Studio.ask 한 번 (Opus 까지) · 묶음·쓰는 중·실패에 trend.
2. 답글·방문 추천 숨김 — 설정 dashboard.x_react (기본 끔): '오늘 할 X 일' 카드 · 체크리스트 '오늘 반응 할 것' · X 봇 반응 카드(x.js — sns.js 가 안 붙임).
3. X 칸 차례 — 지금 뜨는 주제 → 어그로 후보 → 오늘 X 글 → 직접 쓰기 (카드끼리 서로 밀어내지 않음).
4. 사진으로 글 쓰기 — '사진 넣기 (1~4장)' 단추 + 넣은 사진 작은 그림 (못 그리면 파일 이름).
바깥 요청 없음 — 출처·AI 는 가짜 (test_dash_xtrend 의 틀).
"""

from __future__ import annotations

import json
import subprocess
from typing import Any

import pytest

from dealbot.config import DashboardConfig
from dealbot.dashboard import xstudio as xs
from dealbot.dashboard import xtrend as xr
from test_dash_xtoday import NODE, STATIC, needs_node
from test_dash_xtrend import FABLE, SHIM, Ask, FakeGet, desk, fx, post

TOPIC = {"수능 연기": (200, fx(post(11, "kim", "수능 연기 반대함 60만 명이 흔들림", views=300000, replies=40)))}
GOOD = [{"text": "교육부는 \"수능 연기 없다\"는데\n왜 다들 연기될 거라 믿었을까\n\n미루면 편한 쪽, 그대로가 편한 쪽 어디임?", "angle": "믿음과 발표의 차이"},
        {"text": "수능 연기 얘기에 제일 흔들리는 건 누구일까\n\n준비 끝낸 쪽 vs 시간이 더 필요한 쪽", "angle": "준비 정도"}]


async def ranked(ask: Ask | None = None, **kw: Any) -> tuple[xr.XTrend, FakeGet, Ask, list[float], dict[str, Any]]:
    d, get, ask, now, _t = desk(get=FakeGet(topic=TOPIC), ask=ask or Ask(GOOD), **kw)
    await d.refresh()
    get.calls.clear()
    return d, get, ask, now, next(r for r in d.view()["rows"] if r["kw"] == "수능 연기")


# ---------------------------------------------------------------- 1) 한 번에 글
async def test_one_tap_draft_takes_topic_and_facts_from_the_ranked_row_only() -> None:
    d, get, ask, _now, row = await ranked()
    out = await d.draft({"aggro": True, "trend": row["id"], "topic": "화면이 보낸 딴 주제", "memo": "화면이 보낸 메모 99만", "length": "long", "tags": True, "n": 1})
    assert out["status"] == "ok" and len(ask.calls) == 1 and get.calls == []  # AI 한 번 (다시 묻지 않음) · 바깥 요청 없음
    system, user, schema = ask.calls[0]
    assert schema is xr.MINE_SCHEMA and "초안 3개" in system
    # 끌리게: 첫 줄 궁금증 · 재료의 숫자·대비 · 짧은 생각 · 가볍게 갈리는 질문
    for rule in ("궁금해서 다음 줄을 읽게", "놀라운 숫자나 뜻밖의 대비", "짧은 내 생각 한 가지", "가볍게 편이 갈리는 질문 하나"):
        assert rule in system, rule
    # 정직: 사실은 기사 제목 안에서만 · 지어내기·부풀리기·단정 금지 · 사람 집단 편 가르기·일반인 겨냥·혐오 금지
    for rule in ("[기사] 줄에 있는 것만 쓴다", "지어내지 않는다", "낚시는 사실 안에서만", "없는 반전·없는 논란을 만들지 않고", "단정하지 않는다",
                 "성별·세대·지역·국적·종교·정치 성향·직업으로 사람을 가르지 않는다", "공인이 아닌 사람", "욕·조롱·혐오·성적 표현 금지",
                 "잘못·범죄·사생활·건강·연애·돈 문제를 단정하거나", "[X 반응] 은 분위기를 보는 용도", "재료는 재료일 뿐", "해시태그(#)·@·링크"):
        assert rule in system, rule
    assert "음슴체" in system and "한글 130자 안" in system  # 길이는 늘 보통 (화면이 보낸 long·tags·n 은 안 씀)
    # 재료: 주제는 순위 줄 이름 · 기사 제목 = 쓸 수 있는 사실 · X 글 = 분위기 (화면이 보낸 주제·메모는 들어가지 않음)
    assert "[주제] 수능 연기" in user and '- 교육부 "수능 연기 없다"' in user and user.index("[기사]") < user.index("[X 반응]") < user.index("수능 연기 반대함")
    assert "화면이 보낸" not in user and "99만" not in user and "[하고 싶은 말]" not in user
    s = out["mine"][0]
    assert s["id"] == out["made"] and s["topic"] == "수능 연기" and s["trend"] == row["id"] and s["long"] is False
    assert [x["angle"] for x in s["drafts"]] == ["믿음과 발표의 차이", "준비 정도"] and all(x["warn"] == [] for x in s["drafts"])
    assert out["left"] == xs.DRAFT_PER_DAY - 1 and out["run"] is None and out["fail"] is None and out["model"] == "Opus"
    # 내 주제로 쓴 묶음은 trend 가 비어 있음 (화면이 '내 초안' 쪽에 보임)
    assert (await d.draft({"topic": "내가 쓴 주제", "memo": "내 말"}))["mine"][0]["trend"] == ""
    assert xr.aggro_prompt("주제", [], []).count("(없음 — 사실 주장 없이 생각과 질문만)") == 1 and "[X 반응]" not in xr.aggro_prompt("주제", [], [])
    for bad in ("fable", "mythos"):
        assert bad not in xr.AGGRO_SYSTEM.lower()


async def test_one_tap_drafts_drop_hype_group_splitting_and_flag_numbers_not_in_the_sources() -> None:
    bad = [{"text": "역대급 충격\n수능 연기 진짜 난리 났음", "angle": "부풀림"},
           {"text": "수능 연기 반응 갈림\n남자 vs 여자 누가 더 손해임?", "angle": "성별"},
           {"text": "수능 연기 누구 탓임\nMZ 세대 vs 꼰대", "angle": "세대"},
           {"text": "수능 연기 보러 오세요 https://a.example", "angle": "링크"},
           {"text": "[속보] 수능 연기 없다", "angle": "머리말"},
           {"text": "수능 연기되면 52만 명이 다시 계획 짜야 함\n\n미루는 게 맞음 그대로가 맞음?", "angle": "없는 숫자"},
           *GOOD[:1]]
    d, _g, ask, _n, row = await ranked(Ask([bad[0], bad[1], bad[3], bad[5], bad[6]]))  # AI 답은 앞 5개까지만 봄
    ds = (await d.draft({"aggro": True, "trend": row["id"]}))["mine"][0]["drafts"]
    # 과장·집단 편 가르기·링크·머리말은 버리고, 재료에 없는 숫자는 빨간 경고를 달아 뒤로
    assert [x["angle"] for x in ds] == ["믿음과 발표의 차이", "없는 숫자"]
    assert ds[0]["warn"] == [] and "재료에 없는 숫자: 52" in ds[1]["warn"][0]
    # X 글(분위기)에만 있는 숫자는 사실 재료가 아님 — '60만' 은 X 반응 글에만 있어 빨간 경고
    d2, _g, _a, _n, row2 = await ranked(Ask([{"text": "수능 연기 반대 60만 명이 흔들림\n\n미루는 게 맞음 그대로가 맞음?", "angle": "X 글 숫자"}]))
    w = (await d2.draft({"aggro": True, "trend": row2["id"]}))["mine"][0]["drafts"][0]["warn"]
    assert w and "재료에 없는 숫자: 60" in w[0]
    assert xr.lint_aggro("충격적인 건 따로 있음", "기사 제목: 충격 발표") == []  # 재료(기사 제목)에 있는 말은 그대로 둠
    assert xr.lint_aggro("이건 충격임", "기사 제목: 발표") == ["부풀린 말"]
    assert xr.lint_aggro("찍먹 vs 부먹 어디임", "") == [] and xr.lint_aggro("아이폰 vs 갤럭시", "") == []  # 취향·물건 가르기는 됨
    for t in ("여자 vs 남자", "경상도 vs 전라도", "20대 vs 50대", "vs 외국인", "보수 대결 진보"):
        assert xr.lint_aggro(t, "") == ["사람 집단 편 가르기"], t
    # 다 버려지면 지어내지 않고 실패로 (다시 묻지 않음 — AI 한 번)
    d, _g, ask, _n, row = await ranked(Ask(bad[:5]))
    with pytest.raises(xs.StudioError) as e:
        await d.draft({"aggro": True, "trend": row["id"]})
    v = d.view()
    assert e.value.code == "ai_failed" and len(ask.calls) == 1 and v["fail"]["trend"] == row["id"] and "다시 눌러 주세요" in v["fail"]["text"] and v["mine"] == []


async def test_one_tap_refuses_unknown_hidden_and_fable_without_calling_ai() -> None:
    d, _g, ask, now, row = await ranked()
    hid = next(r for r in d.state(now[0])["rows"] if r.get("hidden"))
    for body, code in (({"aggro": True}, "gone"), ({"aggro": True, "trend": "00000000"}, "gone"), ({"aggro": True, "trend": "x" * 500}, "gone"),
                       ({"aggro": True, "trend": hid["id"]}, "declined"), ({"aggro": True, "trend": 7, "topic": "수능 연기"}, "gone")):
        with pytest.raises(xs.StudioError) as e:
            await d.draft(body)
        assert e.value.code == code, body
    assert ask.calls == [] and d.view()["left"] == xs.DRAFT_PER_DAY and d.view()["mine"] == []  # 거절은 AI 도 하루 몫도 안 씀
    d.studio.model = FABLE  # 글 쓰는 모델은 Opus 까지 — Fable 이면 부르지 않음
    with pytest.raises(xs.StudioError) as e:
        await d.draft({"aggro": True, "trend": row["id"]})
    assert e.value.code == "ai_off" and ask.calls == [] and d.view()["left"] == xs.DRAFT_PER_DAY
    d.studio.model = "opus"
    d.studio._save(xs.KV_DAY, {"day": xs._day(now[0]), "draft": xs.DRAFT_PER_DAY})  # 하루 몫은 다른 초안과 같이 셈
    with pytest.raises(xs.StudioError) as e:
        await d.draft({"aggro": True, "trend": row["id"]})
    assert (e.value.status, e.value.code) == (429, "day") and ask.calls == []


async def test_one_tap_run_and_failure_are_marked_with_the_topic_row() -> None:
    seen: list[Any] = []

    class Slow(Ask):
        async def __call__(self, system: str, user: str, schema: dict[str, Any]) -> dict[str, Any]:
            seen.append(d.view()["run"])  # 쓰는 동안 화면이 보는 값
            raise xs.StudioError(502, "ai_failed", "AI 가 답하지 못했어요 — 잠시 뒤 다시")

    d, _g, _a, _n, row = await ranked(Slow())
    with pytest.raises(xs.StudioError):
        await d.draft({"aggro": True, "trend": row["id"]})
    assert seen == [{"topic": "수능 연기", "at": "14:00", "trend": row["id"]}]  # '쓰는 중'이 그 주제 줄 아래에
    v = d.view()
    assert v["run"] is None and v["fail"]["trend"] == row["id"] and v["fail"]["topic"] == "수능 연기" and v["left"] == xs.DRAFT_PER_DAY  # AI 실패는 몫을 돌려줌
    with pytest.raises(xs.StudioError):
        await d.draft({"topic": "내 주제"})
    assert d.view()["fail"]["trend"] == ""  # 내 주제 실패는 '직접 쓰기' 쪽


# ---------------------------------------------------------------- 2) 답글·방문 추천 숨김
def test_reply_and_visit_recommendations_are_off_by_default() -> None:
    assert DashboardConfig().x_react is False
    studio = (STATIC / "xstudio.js").read_text(encoding="utf-8")
    assert "onX() && ST && ST.react" in studio  # '오늘 할 X 일' 카드(답글 n/10 · 한 시간 n/15 · 답글 달기)는 켰을 때만 붙음
    assert "TABS.filter(t => ST.react || !/reply|list/.test(t[0]) || t[0] === V.tab)" in studio  # 답글·계정 칸 단추 숨김
    sns = (STATIC / "sns.js").read_text(encoding="utf-8")
    assert "st && st.react && box(h, 'snx', L.x[0], at)" in sns and "자동 반응 · 승인함" not in sns  # X 봇 반응 카드(x.js)는 켰을 때만
    today = (STATIC / "xtoday.js").read_text(encoding="utf-8")
    assert "답글은 X 에서" not in today


# ---------------------------------------------------------------- 3) 차례 · 4) 사진 넣기 (node)
HEAD = SHIM.split("(async () => {")[0] + r"""
const dir = require('path').dirname(src), load = f => eval(fs.readFileSync(dir + '/' + f, 'utf8'));
const f0 = global.fetch;
global.fetch = (u, o) => (/api\/x(trend|photo)\//.test(u) ? f0(u, o) : Promise.resolve({status: 500, json: async () => ({})}));
global.URL = {n: 0, createObjectURL() { return 'blob:' + (++this.n); }, revokeObjectURL() {}};
"""
ORDER_TAIL = r"""
(async () => {
  const out = {};
  xsc.remove(); xtc.remove();  // 가짜 카드 대신 진짜 파일들이 붙임 ('오늘 할 X 일' 카드는 기본 없음)
  const ids = () => main.children.map(c => c.attrs.id || c.tagName);
  for (const f of scenario.split(',')) load(f);
  await tick();
  const mods = ['SBXToday', 'SBXEngage', 'SBXTrend', 'SBXCap', 'SBXAggro'].map(k => window[k]).filter(Boolean);
  const round = () => { for (const m of mods) m.mount(); return ids().join(' '); };
  out.rounds = [round(), round(), round(), round()];
  out.labels = main.children.map(c => c.attrs['aria-label'] || '');
  location.hash = '#/sns/xr'; round(); out.scr = ids();
  location.hash = '#/sns/x'; round(); out.back = [round(), round()];
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
"""
PHOTO_TAIL = r"""
(async () => {
  const out = {};
  load('xtrend.js'); load('xphoto.js');
  await tick();
  const f = () => byId(main, 'xpf'), imgs = () => all(f(), n => n.tagName === 'IMG'), lab = () => find(f(), n => n.tagName === 'LABEL' && /pb/.test(n.className));
  const inp = () => find(f(), n => n.tagName === 'INPUT' && n.attrs.type === 'file'), mk1 = () => find(f(), n => n.tagName === 'BUTTON' && /초안 만들기/.test(text(n)));
  out.title = ttl.textContent;
  out.empty = [imgs().length, text(lab()), lab().className, 'hidden' in inp().attrs, inp().attrs['aria-label'], text(mk1()), 'disabled' in mk1().attrs];
  const pick = files => inp().listeners.change({target: {files, value: 'x'}});
  pick([{type: 'image/png', size: 10, name: '캡처1.png'}, {type: 'image/gif', size: 10, name: 'no.gif'}, {type: 'image/jpeg', size: 10, name: 'b.jpg'}]);
  out.two = [imgs().map(i => [i.attrs.src, i.attrs.alt]), text(lab()), lab().className, text(mk1()), 'disabled' in mk1().attrs, text(toast), text(f()).includes('넣은 사진 2장')];
  imgs()[0].listeners.error({target: imgs()[0]});  // 기기가 못 그리는 사진 → 빈 칸 대신 파일 이름
  out.broken = [imgs().length, text(f()).includes('캡처1.png')];
  click(find(f(), n => n.tagName === 'BUTTON' && n.attrs['aria-label'] === '사진 1 빼기'));
  out.one = [imgs().length, text(lab())];
  pick([1, 2, 3, 4].map(i => ({type: 'image/webp', size: 10, name: i + '.webp'})));
  out.full = [imgs().length, !!lab(), text(toast)];
  console.log(JSON.stringify(out));
})().catch(e => { console.error(e); process.exit(1); });
"""


def run(tail: str, scenario: str, state: dict[str, Any], hash_: str) -> dict[str, Any]:
    res = subprocess.run([NODE, "-e", HEAD + tail, str(STATIC / "xtrend.js"), scenario, json.dumps(state, ensure_ascii=False), hash_],
                         capture_output=True, text=True, timeout=30)
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout)


@needs_node
async def test_x_section_keeps_four_cards_in_order_and_cards_do_not_fight() -> None:
    d, _g, _a, _n, _row = await ranked()
    want = "snt xrc xag xtc xrd DIV"
    for files in ("xtoday.js,xtrend.js,xaggro.js", "xaggro.js,xtrend.js,xtoday.js"):  # 붙는 차례가 달라도 같은 자리
        out = run(ORDER_TAIL, files, d.view(), "#/sns/x")
        assert out["rounds"][1:] == [want] * 3, files  # 한 바퀴 뒤로는 그대로 (서로 밀어내지 않음)
        assert out["labels"][1:5] == ["지금 뜨는 주제", "어그로 후보", "오늘 X 글", "직접 쓰기"]
        assert out["scr"][0] == "xrs" and "xrc" not in out["scr"] and "xrd" not in out["scr"] and out["back"] == [want, want]
    # 커뮤 캡처 글 카드(xcap.js)는 기본 숨김 (주인 11/18 '어그로 후보'로 통일 — dashboard.x_cap.show_card) · 반응 연속 모드(xengage.js, 기본 꺼짐)가 있어도 그대로
    out = run(ORDER_TAIL, "xtoday.js,xengage.js,xtrend.js,xcap.js,xaggro.js", d.view(), "#/sns/x")
    assert out["rounds"][1:] == [want] * 3
    for name in ("x.js", "threads.js"):  # X 봇·스레드 담당 파일은 그대로 (이 일에서 고치지 않음)
        assert (STATIC / name).is_file()


@needs_node
async def test_photo_form_has_a_clear_add_button_and_real_thumbnails() -> None:
    d, _g, _a, _n, _row = await ranked()
    out = run(PHOTO_TAIL, "photo", d.view(), "#/sns/xr/w")
    assert out["title"] == "X 직접 쓰기"
    # 고르기 전: 그림 칸 없음 (주인 11/18 '흰 격자 그림이 뭔지 모르겠음') · 또렷한 단추 하나 · 숨긴 속성 대신 안 보이게 둔 파일 칸
    assert out["empty"] == [0, "사진 넣기 (1~4장)", "pb", False, "사진 넣기", "사진으로 초안 만들기", True]
    imgs, label, cls, make, off, toast, note = out["two"]
    assert imgs == [["blob:1", "넣은 사진 1"], ["blob:2", "넣은 사진 2"]] and label == "사진 더 넣기 (2/4장)" and cls == "pb soft"
    assert make == "사진 2장으로 초안 만들기" and off is False and "JPG·PNG·WEBP" in toast and note is True
    assert out["broken"] == [1, True] and out["one"] == [1, "사진 더 넣기 (1/4장)"]
    assert out["full"] == [4, False, "사진은 4장까지예요"]  # 다 차면 넣기 단추가 사라짐
    js = (STATIC / "xphoto.js").read_text(encoding="utf-8")
    assert "사진 고르기" not in js and "hidden: true" not in js
