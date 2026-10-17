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
    if (n !== 1) out.push('item primary count ' + n + ': ' + it.dataset.iid);
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
    # 링크 넣기 (붙여넣기 → 넣기)
    link_item = p.locator("main .itm", has=p.locator("input[data-url]")).first
    iid = link_item.get_attribute("data-iid")
    link_item.locator("input[data-url]").fill("https://service.toss.im/shopping-share?p=777")
    r.shot("link-typed")
    link_item.locator(".pb").click()
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
    # 발행 확인 창 (★)
    p.locator('[data-iid="post-issue-31"] .pb').click()
    p.wait_for_selector(".sh .cf")
    r.shot("publish-confirm")
    p.click(".sh .cf .pb:not(.ghost)")
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
    p.locator('[data-iid="post-finance-12"] .lnk.mute', has_text="취소").click()
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
            else:
                scenario(r, a.base, name in a.flows.split(","))
            problems += r.problems + [f"{name}: js error {e}" for e in errors]
            ctx.close()
        browser.close()
    print(json.dumps(problems, ensure_ascii=False, indent=1))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
