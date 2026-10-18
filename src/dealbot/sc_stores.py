"""쇼핑 커넥트에 없는 판매처 기억 (주인 10/18: '없을 것 같은 제품은 요청 안 하고 그냥 올려주는 기능').

- 주인이 링크 요청에서 '쇼핑 커넥트에 없음'(링크 없이 올리기·안 올리기)을 누른 판매처만 적는다. 봇이 스스로 적지 않는다.
- 적힌 판매처의 새 네이버 딜은 링크를 묻지 않고 원본 주소로, 제휴 고지 없이 올린다 (autolink.naver.no_link_autopost).
  가격 확인·사진 규칙·품절 확인은 여느 딜과 같다 (app.process_queue_once).
- 주인이 그 판매처 딜에 쇼핑 커넥트 링크(naver.me)를 넣으면 목록에서 뺀다 (판매처가 쇼핑 커넥트에 들어옴).
- 대시보드 딜 탭에서 하나씩 뺄 수 있다.

판매처 = 스토어 주소의 가게 이름 칸 (smartstore.naver.com/{가게}/products/…, brand.naver.com/{가게}/products/…).
네이버가 가게마다 하나씩 주는 주소 이름이라 믿을 만하다. 스토어 주소가 아니면(가게를 안 밝힌 /main/ 주소 등) 정품 확인 때 상품
페이지에서 읽은 판매자 이름(JSON-LD seller)으로 — 가게 이름이 바뀌면 다른 판매처로 보일 수 있어 덜 믿을 만하다. 둘 다 없으면 적지 않음.
kv sc_store:{s:가게 | n:판매자 이름} → {"label", "seller", "at", "via", "qid"}
"""

from __future__ import annotations

import json
import re
from typing import Any

from dealbot.models import Product
from dealbot.naver_shop import store_name
from dealbot.utils.timeutil import to_iso, utcnow

KV = "sc_store:"
_NAVER_ME = re.compile(r"^https://naver\.me/[A-Za-z0-9]+$")


def _seller(p: Product) -> str:
    auth = p.extra.get("auth")
    seller = auth.get("seller") if isinstance(auth, dict) else None
    return str(seller).strip()[:40] if isinstance(seller, str) else ""


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", text.lower())


def store_of(p: Product) -> tuple[str, str] | None:
    """(기억 키, 화면에 보일 이름). 네이버 딜이 아니거나 판매처를 모르면 None."""
    if p.shop != "naver":
        return None
    slug = store_name(p.url)
    seller = _seller(p)
    if slug:
        return f"s:{slug}", seller or slug
    if seller:
        return f"n:{_norm(seller)}", seller
    return None


def remember(db: Any, p: Product, *, via: str, qid: int | None = None) -> str | None:
    """주인이 '쇼핑 커넥트에 없음'을 누른 판매처를 적는다. 적은 판매처 이름 (모르면 None)."""
    got = store_of(p)
    if got is None:
        return None
    key, label = got
    db.kv_set(KV + key, json.dumps({"label": label, "seller": _norm(_seller(p)), "at": to_iso(utcnow()), "via": via,
                                    "qid": qid}, ensure_ascii=False))
    return label


def entries(db: Any) -> list[dict[str, Any]]:
    """적힌 판매처 (새로 적은 것부터): {"key", "label", "seller", "at", "via"}."""
    out = []
    for key, raw in db.kv_items(KV):
        try:
            data = json.loads(raw or "{}")
        except ValueError:
            data = {}
        if isinstance(data, dict):
            out.append({**data, "key": key[len(KV):], "label": str(data.get("label") or key[len(KV) + 2:])})
    return sorted(out, key=lambda e: str(e.get("at") or ""), reverse=True)


def known(db: Any, p: Product) -> dict[str, Any] | None:
    """이 딜의 판매처가 '쇼핑 커넥트에 없음'으로 적혀 있으면 그 기록. 가게 이름(주소)으로 먼저, 없으면 판매자 이름으로."""
    got = store_of(p)
    if got is None:
        return None
    raw = db.kv_get(KV + got[0])
    if raw:
        return {"key": got[0], **(json.loads(raw) or {})}
    seller = _norm(_seller(p))
    if seller:
        return next((e for e in entries(db) if e.get("seller") == seller), None)
    return None


def forget(db: Any, key: str) -> bool:
    """목록에서 뺀다 (대시보드). 뺐으면 True."""
    return db.kv_take(KV + key) is not None


def joined(db: Any, p: Product, link: str) -> str | None:
    """주인이 쇼핑 커넥트 링크(naver.me)를 넣음 = 그 판매처가 쇼핑 커넥트에 있음 → 목록에서 뺀다. 뺀 판매처 이름 (없으면 None).
    원본 상품 주소 같은 다른 주소는 쇼핑 커넥트 링크가 아니라 그대로 둔다."""
    if not _NAVER_ME.match((link or "").strip()):
        return None
    hit = known(db, p)
    if hit is None or not forget(db, str(hit["key"])):
        return None
    return str(hit.get("label") or hit["key"])
