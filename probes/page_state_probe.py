"""1회용 (읽기 전용, 주인 OK 10/13): 네이버 상품 페이지에서 가격이 어디에 들어 있는지 구조만 읽는다.
- 딜봇과 같은 크롬 경로(StoreBrowser._open_page: 창 없는 크롬 → 막히면 가상 화면 크롬, UA 위장 없음)로 주소마다 한 번씩만 연다 (사이 20초).
- 글쓰기·구매·로그인 없음. 블로그·카페 주소는 열지 않는다. HTML 전체는 저장하지 않고 가격 관련 경로·값 요약만 남긴다.
실행: cd ~/coupang-deal-bot && git fetch -q origin wip/probes && mkdir -p var/probes && \
      git show origin/wip/probes:probes/page_state_probe.py > var/probes/page_state_probe.py && \
      .venv/bin/python var/probes/page_state_probe.py URL1 URL2 [URL3]
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from dealbot.page_price import _STATE_RE, naver_state_prices  # noqa: E402
from dealbot.store_browser import StoreBrowser, is_naver_product_page  # noqa: E402

PRICE_KEY = re.compile(r"price|amount|fee", re.I)
LD_RE = re.compile(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', re.S | re.I)


def walk(node, path, out, depth=0):
    if depth > 14 or len(out) >= 120:
        return
    if isinstance(node, dict):
        ids = {k: node.get(k) for k in ("productNo", "id", "channelProductNo", "originProductNo") if k in node}
        name = node.get("name") or node.get("productName")
        for k, v in node.items():
            if PRICE_KEY.search(k) and isinstance(v, int | float) and not isinstance(v, bool):
                out.append({"path": f"{path}.{k}", "value": v, "ids": ids, "name": str(name)[:20] if name else None})
        for k, v in node.items():
            walk(v, f"{path}.{k}", out, depth + 1)
    elif isinstance(node, list):
        for i, v in enumerate(node[:30]):
            walk(v, f"{path}[{i}]", out, depth + 1)


async def probe(sb: StoreBrowser, url: str) -> dict:
    if not is_naver_product_page(url):
        return {"url": url, "skip": "네이버 상품 페이지 아님"}
    try:
        status, final, html = await asyncio.wait_for(sb._open_page(url), 90)
    except Exception as e:  # noqa: BLE001
        return {"url": url, "error": f"{type(e).__name__}: {str(e)[:120]}"}
    out: dict = {"url": url, "status": status, "final": final[:160], "html_len": len(html or "")}
    m = _STATE_RE.search(html or "")
    out["state_found"] = bool(m)
    if m:
        try:
            state, _ = json.JSONDecoder().raw_decode(html, m.end())
            out["state_top_keys"] = sorted(state.keys())[:40] if isinstance(state, dict) else type(state).__name__
            found: list = []
            walk(state, "$", found)
            out["price_paths"] = found
        except ValueError as e:
            out["state_error"] = str(e)[:120]
    else:
        out["other_state_markers"] = sorted({s for s in re.findall(r"window\.(__[A-Z_]+__)", html or "")})[:10]
    lds = []
    for blk in LD_RE.findall(html or "")[:5]:
        try:
            d = json.loads(blk)
        except ValueError:
            continue
        for item in d if isinstance(d, list) else [d]:
            if isinstance(item, dict):
                offers = item.get("offers")
                lds.append({"type": item.get("@type"), "offers": offers if isinstance(offers, dict | list) else None})
    out["jsonld"] = lds[:3]
    parsed = naver_state_prices(html or "", url)
    out["current_parser"] = None if parsed is None else {"options": [o.prices for o in parsed.options][:5], "fee": parsed.shipping_fee}
    return out


async def main() -> None:
    urls = [u for u in sys.argv[1:] if u.startswith("http")][:4]
    if not urls:
        print("주소를 1~4개 주세요")
        return
    sb = StoreBrowser(min_interval=20, timeout=45, cache_seconds=0, cooldown_seconds=0)
    res = []
    for i, u in enumerate(urls):
        if i:
            await asyncio.sleep(20)
        res.append(await probe(sb, u))
    dest = ROOT / "var" / "probes" / f"page_state_{datetime.now():%m%d_%H%M%S}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8")
    for r in res:
        print("==", r.get("url"), "status", r.get("status"), "state", r.get("state_found"), "html", r.get("html_len"))
        print("   current_parser:", r.get("current_parser"), "| jsonld:", json.dumps(r.get("jsonld"), ensure_ascii=False)[:300])
        for p in (r.get("price_paths") or [])[:40]:
            print("   ", p["path"][:110], "=", p["value"], p["ids"], p["name"] or "")
        if r.get("other_state_markers"):
            print("   markers:", r["other_state_markers"])
        if r.get("error") or r.get("skip"):
            print("   ", r.get("error") or r.get("skip"))
    print("저장:", dest)


if __name__ == "__main__":
    asyncio.run(main())
