"""대시보드 앱(Sns-board) 화면 확인 — Playwright 로 휴대폰·태블릿 크기에서 탭·흐름마다 스크린숏 + 기본 점검.

가짜 데이터로 띄운 로컬 서버에만 붙는다 (바깥 요청 없음, 진짜 토큰 없음). 사람이 직접 보고 고치는 도구 — pytest 가 아님.

    python tools/ui_shots.py --base http://127.0.0.1:8765 --session <세션 토큰 파일> --out <폴더> [--chrome <크롬 경로>]

점검 (화면마다): 가로 스크롤 없음 · 누르는 곳 44px 이상 · 입력 칸 글자 16px 이상 · 아래 탭이 화면 안 ·
줄마다 큰 단추 하나 · 더보기는 더 할 일이 있을 때만 · 탭 이름 5개가 잘리지 않음.
"""

from __future__ import annotations

import argparse
import io
import json
import sys
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, sync_playwright

PROFILES = {
    "galaxy360": {"viewport": {"width": 360, "height": 780}, "device_scale_factor": 3, "is_mobile": True, "has_touch": True,
                  "user_agent": "Mozilla/5.0 (Linux; Android 14; SM-S921N) AppleWebKit/537.36 (KHTML, like Gecko) "
                                "SamsungBrowser/25.0 Chrome/121.0.0.0 Mobile Safari/537.36"},
    "galaxy412": {"viewport": {"width": 412, "height": 915}, "device_scale_factor": 3, "is_mobile": True, "has_touch": True,
                  "user_agent": "Mozilla/5.0 (Linux; Android 14; SM-S928N) AppleWebKit/537.36 (KHTML, like Gecko) "
                                "Chrome/126.0.0.0 Mobile Safari/537.36"},
    "iphone390": {"viewport": {"width": 390, "height": 844}, "device_scale_factor": 3, "is_mobile": True, "has_touch": True,
                  "user_agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
                                "Version/17.5 Mobile/15E148 Safari/604.1"},
    "ipad820": {"viewport": {"width": 820, "height": 1180}, "device_scale_factor": 2, "is_mobile": True, "has_touch": True,
                "user_agent": "Mozilla/5.0 (iPad; CPU OS 17_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) "
                              "Version/17.5 Mobile/15E148 Safari/604.1"},
}

CHECK_JS = """() => {
  const out = [];
  const vis = e => { const r = e.getBoundingClientRect(); const s = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && s.visibility !== 'hidden' && s.display !== 'none' && r.bottom > 0 && r.top < innerHeight; };
  if (document.documentElement.scrollWidth > innerWidth + 1) out.push('horizontal scroll ' + document.documentElement.scrollWidth);
  for (const e of document.querySelectorAll('button, a[href], input, textarea, select')) {
    if (!vis(e) || e.closest('.bdg')) continue;
    const r = e.getBoundingClientRect();
    if (e.matches('input[type=hidden]')) continue;
    if (r.height < 43.5 && !e.closest('.dr')) out.push('small target ' + Math.round(r.height) + 'px: ' + (e.textContent || e.placeholder || e.tagName).trim().slice(0, 30));
    if (e.matches('input, textarea, select') && parseFloat(getComputedStyle(e).fontSize) < 16) out.push('input font < 16: ' + (e.placeholder || ''));
  }
  const nav = document.querySelector('.tabs').getBoundingClientRect();
  if (nav.bottom > innerHeight + 1) out.push('tab bar outside viewport');
  for (const t of document.querySelectorAll('.tab span')) if (t.scrollWidth > t.clientWidth + 1) out.push('tab label cut: ' + t.textContent);
  for (const it of document.querySelectorAll('main .itm')) {
    const n = it.querySelectorAll(':scope > .pb, :scope > div > .pb, :scope > .ip > .pb, :scope > a.pb').length;
    if (n > 1 || (n === 0 && !it.querySelector('.st'))) out.push('item primary count ' + n + ': ' + it.dataset.iid);
  }
  return out;
}"""


class Run:
    def __init__(self, page: Page, out: Path, prof: str) -> None:
        self.page, self.out, self.prof = page, out, prof
        self.problems: list[str] = []

    def shot(self, name: str, *, full: bool = False) -> None:
        self.page.wait_for_timeout(350)
        path = self.out / f"{self.prof}-{name}.png"
        self.page.screenshot(path=str(path), full_page=full)
        for p in self.page.evaluate(CHECK_JS):
            self.problems.append(f"{self.prof}/{name}: {p}")

    def tab(self, key: str) -> None:
        self.page.click(f'.tab[data-tab="{key}"]')
        self.page.wait_for_timeout(500)


def jpeg(w: int, h: int, color: tuple[int, int, int]) -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, "JPEG")
    return buf.getvalue()


def scenario(r: Run, base: str, flows: bool) -> None:
    p = r.page
    p.goto(base + "/dashboard/")
    p.wait_for_selector("main .itm, main .empty")
    r.shot("todo")
    r.shot("todo-full", full=True)
    if not flows:
        for t in ("deal", "blog", "stats", "connect"):
            r.tab(t)
            r.shot(t, full=t in ("connect", "blog"))
        return
    if p.locator("main > button.pb.ghost").count():  # 더 보기 (N)
        p.click("main > button.pb.ghost")
    # 자세히 시트 (글 — 사진 자리)
    p.click('[data-iid="post-issue-46"] .ti')
    p.wait_for_selector(".sh")
    r.shot("detail-photos")
    p.click(".sh .shh .lnk")
    p.wait_for_timeout(300)
    # 더보기 시트
    p.click('[data-iid="post-issue-31"] .ih .lnk')
    p.wait_for_selector(".sh")
    r.shot("more-sheet")
    p.click(".sh .shh .lnk")
    p.wait_for_timeout(300)
    # 링크 넣기 (줄은 접혀 있음 → 누르면 그 자리에 칸 → 붙여넣기 → 넣기)
    r.shot("link-collapsed")
    link_item = p.locator("main .itm", has=p.locator(".pb", has_text="링크 넣기")).first
    iid = link_item.get_attribute("data-iid")
    link_item.locator(".pb").click()
    link_item = p.locator(f'[data-iid="{iid}"]')
    link_item.locator("input[data-url]").fill("https://service.toss.im/shopping-share?p=777")
    r.shot("link-typed")
    link_item.locator(".ip > .pb").click()
    p.wait_for_selector("#toast:not([hidden])")
    r.shot("link-toast")
    print("link result:", p.text_content("#toast"), "iid", iid)
    p.wait_for_timeout(800)
    r.shot("link-next")
    # 사진 올리기 (Pick #46) — 여러 장, 미리 보기, 올리기
    with p.expect_file_chooser() as fc:
        p.click('[data-iid="post-issue-46"] .pb')
    fc.value.set_files([{"name": f"p{i}.jpg", "mimeType": "image/jpeg", "buffer": jpeg(1200, 1600, (40 * i, 120, 160))} for i in range(1, 4)])
    p.wait_for_selector('[data-iid="post-issue-46"] .pv img')
    r.shot("photos-picked")
    p.click('[data-iid="post-issue-46"] .ip > .pb')
    p.wait_for_selector("#toast:not([hidden])", timeout=20000)
    r.shot("photos-sent")
    print("photo result:", p.text_content("#toast"))
    p.wait_for_timeout(5500)
    r.shot("photos-done")
    print("photo final:", p.text_content("#toast"))
    # 간격 두고 발행 (Pick — 확인 없이 예약 줄 끝으로)
    p.locator('[data-iid="post-issue-31"] .pb').click()
    p.wait_for_selector("#toast:not([hidden])")
    r.shot("publish-queued")
    p.wait_for_timeout(5000)
    r.shot("publish-done")
    print("publish final:", p.text_content("#toast"))
    # 직접 고친 글 덮어쓰기 (★★)
    p.click('[data-iid="post-issue-50"] .ih .lnk')
    p.wait_for_selector(".sh")
    p.click(".sh .mr.red >> nth=0")
    p.wait_for_selector(".sh .cf")
    r.shot("oedit-confirm")
    p.click(".sh .cf .ghost")
    p.wait_for_timeout(400)
    # 답하기 (질문 3개)
    p.locator('[data-iid="post-finance-12"] .pb').click()
    p.wait_for_selector('[data-iid="post-finance-12"] textarea')
    r.shot("answers")
    p.locator('[data-iid="post-finance-12"] .lnk.mute', has_text="접기").click()
    # 시스템 줄 → 자세히 시트 (무엇이 멈췄는지)
    p.locator('[data-iid="sys-dealbot"] .pb').click()
    p.wait_for_selector(".sh")
    r.shot("sys-detail")
    p.click(".sh .shh .lnk")
    p.wait_for_timeout(300)
    # 딥 링크
    p.goto(base + "/dashboard/go/deal-4")
    p.wait_for_selector(".sh")
    r.shot("deeplink-deal")
    p.goto(base + "/dashboard/go/sns-replies")
    p.wait_for_selector(".sh")
    p.wait_for_timeout(600)
    r.shot("deeplink-replies")
    p.goto(base + "/dashboard/go/deal-999")
    p.wait_for_timeout(1200)
    r.shot("deeplink-gone")
    for t in ("deal", "blog", "stats", "connect"):
        r.tab(t)
        r.shot(t, full=t in ("connect", "blog"))
    r.tab("deal")
    p.click(".top .lnk")
    p.wait_for_selector(".sh")
    r.shot("deal-more")
    p.click(".sh .shh .lnk")
    p.wait_for_timeout(300)
    # 핫딜(자동 발행 모드) '지금 한 편 쓰기' → 바로 공개된다는 확인 창
    p.goto(base + "/dashboard/#/blog/hotdeal")
    p.wait_for_timeout(400)
    p.locator("main .pb", has_text="지금 한 편 쓰기").click()
    p.wait_for_selector(".sh .cf")
    r.shot("hotdeal-publish-confirm")
    p.click(".sh .cf .ghost")
    p.wait_for_timeout(300)
    # Pick 발행 대기 여러 편 → 한 번에 (제목 목록 확인 창)
    p.goto(base + "/dashboard/#/blog/issue")
    p.wait_for_timeout(400)
    batch = p.locator("main > .pb", has_text="간격 두고 발행")
    if batch.count():
        batch.click()
        p.wait_for_selector(".sh .cf")
        r.shot("pick-batch-confirm")
        p.click(".sh .cf .ghost")
        p.wait_for_timeout(300)


def sched(r: Run, base: str) -> None:
    """간격 두고 발행 (주인 10/18): 준비된 글 · 여러 편 확인 창(시각 목록·첫 글은 지금) · 예약된 글 · 블로그 탭 '예약된 글' · 멈춘 예약 ·
    최근 글 검색 등록."""
    p = r.page
    p.goto(base + "/dashboard/")
    p.wait_for_selector("main .itm")
    if p.locator("main > button.pb.ghost").count():
        p.click("main > button.pb.ghost")
    p.locator('[data-iid="post-finance-15"]').scroll_into_view_if_needed()
    p.evaluate("scrollBy(0, -120)")
    r.shot("sched-ready-todo")
    p.locator('[data-iid="post-issue-58"]').scroll_into_view_if_needed()
    p.evaluate("scrollBy(0, -120)")
    r.shot("sched-blocked-todo")
    p.click('[data-iid="post-finance-15"] .ih .lnk')
    p.wait_for_selector(".sh")
    r.shot("sched-ready-more")
    p.click(".sh .shh .lnk")
    p.wait_for_timeout(300)
    p.goto(base + "/dashboard/#/blog/finance")
    p.wait_for_timeout(500)
    r.shot("sched-blog-finance")
    p.locator("main > .pb", has_text="간격 두고 발행").click()
    p.wait_for_selector(".sh .cf ul.pl")
    r.shot("sched-batch-confirm")
    p.click(".sh .cf .tg")
    p.wait_for_timeout(700)
    r.shot("sched-batch-first-now")
    p.click(".sh .cf .ghost")
    p.wait_for_timeout(300)
    p.goto(base + "/dashboard/#/blog/issue")
    p.wait_for_timeout(500)
    r.shot("sched-blog-issue", full=True)
    p.locator("main .top.hs").scroll_into_view_if_needed()
    p.evaluate("scrollBy(0, 120)")
    r.shot("sched-section-issue")
    p.locator('[data-iid="post-issue-57"] .ih .lnk').click()
    p.wait_for_selector(".sh")
    r.shot("sched-scheduled-more")
    p.click(".sh .shh .lnk")
    p.wait_for_timeout(300)
    p.locator("main .fold").click()
    p.wait_for_timeout(300)
    p.locator("main .row .lnk", has_text="더보기").first.click()
    p.wait_for_selector(".sh")
    r.shot("sched-recent-submit")
    p.click(".sh .shh .lnk")
    p.wait_for_timeout(300)
    p.goto(base + "/dashboard/#/blog/finance")
    p.wait_for_timeout(500)
    p.locator('[data-iid="post-finance-15"] .pb').click()
    p.wait_for_selector("#toast:not([hidden])")
    p.wait_for_timeout(5500)
    r.shot("sched-single-done")
    print("schedule toast:", p.text_content("#toast"))


def visits(r: Run, base: str) -> None:
    """'오늘 들를 이웃 글' (주인 10/18): 할 일 한 줄 · 목록 시트(댓글 복사·열기·했어요) · 블로그 탭 이웃 칸 (서로이웃 한 줄)."""
    p = r.page
    p.goto(base + "/dashboard/")
    p.wait_for_selector("main .itm")
    if p.locator("main > button.pb.ghost").count():
        p.click("main > button.pb.ghost")
    row = p.locator('[data-iid="visits"]')
    row.scroll_into_view_if_needed()
    p.evaluate("scrollBy(0, -160)")
    r.shot("visits-todo-row")
    row.locator(".pb").click()
    p.wait_for_selector(".sh .vi")
    r.shot("visits-sheet")
    links = p.eval_on_selector_all(".sh .vi a.sm", "as => as.map(a => [a.target, a.rel, a.href.slice(0, 26)])")
    print("open links:", links[:2], "auto-opened pages:", len(p.context.pages) - 1)
    p.locator(".sh .vi .sm", has_text="댓글 복사").first.click()
    p.wait_for_selector("#toast:not([hidden])")
    r.shot("visits-copied")
    print("copy toast:", p.text_content("#toast"), "| clipboard:", p.evaluate("navigator.clipboard.readText()")[:20])
    p.locator(".sh .vi .sm", has_text="했어요").first.click()
    p.wait_for_timeout(600)
    r.shot("visits-done")
    print("done toast:", p.text_content("#toast"), "| left in sheet:", p.locator(".sh .vi").count())
    p.click(".sh .shh .lnk")
    p.wait_for_timeout(4000)
    print("todo row after done:", p.text_content('[data-iid="visits"] .ti'))
    for key in ("issue", "finance"):
        p.goto(base + f"/dashboard/#/blog/{key}")
        p.wait_for_timeout(600)
        p.locator("main .h3", has_text="이웃").scroll_into_view_if_needed()
        p.evaluate("scrollBy(0, -60)")
        r.shot(f"visits-blog-{key}")
    p.goto(base + "/dashboard/#/blog/issue")
    p.wait_for_timeout(600)
    r.shot("visits-blog-issue-full", full=True)
    p.locator("main .pb.ghost", has_text="모두 보기").click()
    p.wait_for_selector(".sh .vi")
    r.shot("visits-blog-sheet")


def today(r: Run, base: str) -> None:
    """'오늘 한 일' (주인 10/18): 할 일 한 줄 → 칸별 시트 (위·아래) · 어제 보기 · 연결 탭에서 같은 시트."""
    p = r.page
    p.goto(base + "/dashboard/")
    p.wait_for_selector("main .itm, main .empty")
    row = p.locator("main .fold", has_text="오늘 한 일")
    row.scroll_into_view_if_needed()
    p.evaluate("scrollBy(0, 200)")
    r.shot("today-todo-row")
    row.click()
    p.wait_for_selector(".sh .grp, .sh .empty:not(:has-text('불러오는'))")
    p.wait_for_timeout(300)
    r.shot("today-sheet")
    for i, frac in enumerate((0.33, 0.66, 1.0), start=1):
        p.evaluate(f"(() => {{ const b = document.querySelector('.sh .shb'); b.scrollTop = b.scrollHeight * {frac}; }})()")
        r.shot(f"today-sheet-{i}")
    print("today title:", p.text_content(".sh h2"), "| groups:", p.eval_on_selector_all(".sh .grp", "gs => gs.map(g => g.textContent)"))
    print("links:", p.eval_on_selector_all(".sh a.sm", "as => as.map(a => [a.textContent, a.target, a.rel, a.href.slice(0, 40)])"))
    p.locator(".sh .foot .lnk", has_text="어제 보기").click()
    p.wait_for_timeout(600)
    r.shot("today-yesterday")
    p.click(".sh .shh .lnk")
    p.wait_for_timeout(300)
    p.goto(base + "/dashboard/#/connect")
    p.wait_for_timeout(600)
    p.evaluate("scrollTo(0, document.body.scrollHeight)")
    r.shot("today-connect")
    p.locator("main .fold", has_text="오늘 한 일").click()
    p.wait_for_selector(".sh .grp, .sh .empty:not(:has-text('불러오는'))")
    p.wait_for_timeout(300)
    r.shot("today-connect-sheet")


def pics(r: Run, base: str) -> None:
    """링크에서 사진 고르기 (주인 10/18 — 손이 아파요): 할 일 카드 '이대로 넣기'(추천 사진 줄) · 자세히 시트 그리드(누르면 넣기·빼기,
    꾹 누르면 크게) · 찾는 중·못 찾음·없음 · 자동 발행 블로그는 확인 창 · 블로그 탭 '링크에서 사진 뽑기' (막는 링크·뽑은 사진·크게)."""
    p = r.page

    def todo(iid: str) -> None:
        p.goto(base + "/dashboard/")
        p.wait_for_selector("main .itm")
        if p.locator("main > button.pb.ghost").count():
            p.click("main > button.pb.ghost")
        p.locator(f'[data-iid="{iid}"]').scroll_into_view_if_needed()
        p.evaluate("scrollBy(0, -140)")

    def close() -> None:
        p.click(".sh .shh .lnk >> nth=-1")
        p.wait_for_timeout(300)

    def press(sel: str) -> None:  # 꾹 누르기 (손가락을 0.7초 대고 있음)
        box = p.locator(sel).bounding_box()
        assert box is not None
        p.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        p.mouse.down()
        p.wait_for_timeout(750)
        p.mouse.up()

    todo("post-issue-46")
    r.shot("pics-todo-card")
    print("card primary:", p.text_content('[data-iid="post-issue-46"] .pb'), "| status:", p.text_content('[data-iid="post-issue-46"] .st'))
    p.click('[data-iid="post-issue-46"] .ti')
    p.wait_for_selector(".sh .pk")
    r.shot("pics-detail")
    p.locator(".sh .pk").scroll_into_view_if_needed()
    r.shot("pics-detail-grid")
    tiles = p.locator(".sh .pk .pt")
    tiles.nth(2).click()  # 넣기 (빈 자리에)
    tiles.nth(0).click()  # 빼기 (AI 가 고른 것)
    p.wait_for_timeout(200)
    r.shot("pics-toggled")
    print("put label after toggles:", p.text_content(".sh .pk + .pb"))
    press(".sh .pk .pt >> nth=1")
    p.wait_for_selector(".sh .big img")
    p.wait_for_timeout(300)
    r.shot("pics-full")
    close()
    p.click(".sh .pk + .pb")
    p.wait_for_selector("#toast:not([hidden])")
    r.shot("pics-put-sent")
    print("put toast:", p.text_content("#toast"), "| sheets open:", p.locator(".sh").count())
    p.wait_for_timeout(5000)
    r.shot("pics-put-done")
    print("put final:", p.text_content("#toast"))
    # 자동 발행 블로그(핫딜): 할 일에서 한 번에 → 확인 창
    todo("post-hotdeal-8")
    r.shot("pics-hotdeal-card")
    p.locator('[data-iid="post-hotdeal-8"] .pb').click()
    p.wait_for_selector(".sh .cf")
    r.shot("pics-hotdeal-confirm")
    p.click(".sh .cf .ghost")
    p.wait_for_timeout(300)
    # 찾는 중 · 못 찾음 (다시 찾기) · 없음 (링크에서 사진 찾기)
    todo("post-issue-53")
    r.shot("pics-working-card")
    todo("post-issue-55")
    p.click('[data-iid="post-issue-55"] .ti')
    p.wait_for_selector(".sh")
    r.shot("pics-failed")
    p.locator(".sh .pb.ghost", has_text="다시 찾기").click()
    p.wait_for_selector("#toast:not([hidden])")
    print("refetch toast:", p.text_content("#toast"))
    close()
    p.wait_for_timeout(6500)
    r.shot("pics-refetch-done")
    print("refetch final:", p.text_content("#toast"))
    todo("post-issue-58")
    p.click('[data-iid="post-issue-58"] .ti')
    p.wait_for_selector(".sh")
    r.shot("pics-none")
    close()
    # 블로그 탭 → 링크에서 사진 뽑기
    p.goto(base + "/dashboard/#/blog/issue")
    p.wait_for_timeout(600)
    r.shot("pics-blog-entry")
    p.click("main a.bn[href='#/blog/pics']")
    p.wait_for_timeout(500)
    r.shot("pics-tool")
    p.fill("main input[data-k='pics|url']", "https://www.instagram.com/p/DPq7x2aE1bc/")
    p.locator("main .box .pb", has_text="사진 뽑기").click()
    p.wait_for_selector("#toast:not([hidden])")
    r.shot("pics-tool-refused")
    print("refused toast:", p.text_content("#toast"))
    p.fill("main input[data-k='pics|url']", "https://www.29cm.co.kr/products/1234567")
    p.fill("main input[data-k='pics|want']", "착용 사진")
    p.locator("main .box .pb", has_text="사진 뽑기").click()
    p.wait_for_selector("#toast:not([hidden])")
    p.wait_for_timeout(500)
    r.shot("pics-tool-working")
    p.wait_for_timeout(6500)
    r.shot("pics-tool-done", full=True)
    print("tool final:", p.text_content("#toast"), "| jobs:", p.locator("main .box .pk").count())
    p.locator("main .pk .pt").first.click()
    p.wait_for_selector(".sh .big img")
    p.wait_for_timeout(300)
    r.shot("pics-tool-full")
    print("full src:", p.get_attribute(".sh .big img", "src"), "natural:", p.evaluate("document.querySelector('.sh .big img').naturalWidth"))


def extra(r: Run, base: str, ctx: Any, token: str, host: str) -> None:
    """로그인이 끝났을 때 로그인 시트 → 다시 로그인."""
    p = r.page
    p.goto(base + "/dashboard/")
    p.wait_for_selector("main .itm, main .empty")
    p.wait_for_timeout(1500)  # 서비스 워커 등록 (오프라인 화면은 서버를 끄고 본다 — Playwright 오프라인은 서비스 워커에 안 걸림)
    ctx.clear_cookies()
    p.click("#rf")
    p.wait_for_selector(".sh form")
    r.shot("login-sheet")
    p.fill(".sh input[name=user]", "owner")
    p.fill(".sh input[name=password]", "plum-river-cactus-42")  # 가짜 서버의 가짜 값
    p.click(".sh form .pb")
    p.wait_for_timeout(1500)
    r.shot("login-done")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8765")
    ap.add_argument("--session", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--chrome", default="/opt/pw-browsers/chromium-1194/chrome-linux/chrome")
    ap.add_argument("--profiles", default=",".join(PROFILES))
    ap.add_argument("--flows", default="galaxy360,iphone390")
    ap.add_argument("--dark", action="store_true")
    ap.add_argument("--deny", action="store_true", help="알림 권한을 막힌 상태로")
    ap.add_argument("--tag", default="", help="파일 이름 뒤에 붙일 말 (예: empty)")
    ap.add_argument("--extra", action="store_true", help="오프라인·로그인 시트")
    ap.add_argument("--sched", action="store_true", help="간격 두고 발행 화면만")
    ap.add_argument("--visits", action="store_true", help="오늘 들를 이웃 글 화면만")
    ap.add_argument("--today", action="store_true", help="오늘 한 일 화면만")
    ap.add_argument("--pics", action="store_true", help="링크에서 사진 고르기 화면만")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    token = Path(a.session).read_text(encoding="utf-8").strip()
    problems: list[str] = []
    host = a.base.split("//", 1)[1].split(":")[0]
    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=a.chrome, args=["--no-sandbox"])
        for name in a.profiles.split(","):
            ctx = browser.new_context(**PROFILES[name], locale="ko-KR", timezone_id="Asia/Seoul",
                                      color_scheme="dark" if a.dark else "light", permissions=["clipboard-read", "clipboard-write"] + ([] if a.deny else ["notifications"]))
            ctx.add_cookies([{"name": "dash_session", "value": token, "domain": host, "path": "/dashboard", "httpOnly": True,
                              "secure": False, "sameSite": "Lax"}])
            page = ctx.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda e, errors=errors: errors.append(str(e)))
            page.on("console", lambda m, errors=errors: m.type == "error" and "Failed to load resource" not in m.text and errors.append(m.text))
            r = Run(page, out, name + ("-dark" if a.dark else "") + (f"-{a.tag}" if a.tag else ""))
            if a.extra:
                extra(r, a.base, ctx, token, host)
            elif a.sched:
                sched(r, a.base)
            elif a.visits:
                visits(r, a.base)
            elif a.today:
                today(r, a.base)
            elif a.pics:
                pics(r, a.base)
            else:
                scenario(r, a.base, name in a.flows.split(","))
            problems += r.problems + [f"{name}: js error {e}" for e in errors]
            ctx.close()
        browser.close()
    print(json.dumps(problems, ensure_ascii=False, indent=1))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
