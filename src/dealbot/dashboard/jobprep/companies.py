"""③ 회사·분야 씨앗 (코드에 박은 표 — 주인이 ⑨·지켜보는 회사에서 켜고 끔).

ats/token 은 11/22 로그인 없이 200 을 확인한 것만 (docs/jobprep_plan.md 0장). 그 밖은 career_url 로 '지켜보는 회사'
(채용 페이지 글이 바뀌면 알림 — 자바스크립트로만 그리는 곳·막힌 곳은 쉼만, 우회 없음). 주소를 모르는 곳은 빈칸 (주인이 넣음).
로그인이 필요한 곳·사람 확인 화면이 뜨는 곳은 넣지 않는다.
"""

from __future__ import annotations

import re
from typing import Any

CATS = {"crypto": "크립토 대형", "crypto_growth": "크립토 성장", "kr_exchange": "국내 거래소", "mm_quant": "마켓메이커·퀀트(크립토)",
        "ai_growth": "AI·테크 성장", "bigtech": "빅테크", "quant": "퀀트·트레이딩", "ib": "IB·금융·VC/PE", "consulting": "컨설팅",
        "semi": "반도체(회로 설계 아님)", "kr_list": "국내 목록"}

# (이름, 분야, 지역, ats, token, career_url)
SEED: list[tuple[str, str, str, str, str, str]] = [
    ("Binance", "crypto", "global", "lever", "binance", ""),
    ("OKX", "crypto", "global", "greenhouse", "okx", ""),
    ("Coinbase", "crypto", "global", "greenhouse", "coinbase", ""),
    ("Kraken", "crypto", "global", "ashby", "kraken.com", ""),
    ("Bybit", "crypto", "global", "", "", "https://careers.bybit.com/"),
    ("Crypto.com", "crypto", "global", "", "", "https://crypto.com/careers"),
    ("Bitget", "crypto", "global", "", "", ""),
    ("Gate", "crypto", "global", "", "", ""),
    ("KuCoin", "crypto", "global", "", "", ""),
    ("Hyperliquid", "crypto_growth", "global", "", "", ""),
    ("Lighter", "crypto_growth", "global", "", "", ""),
    ("두나무", "kr_exchange", "KR", "", "", "https://careers.dunamu.com/"),
    ("빗썸", "kr_exchange", "KR", "", "", ""),
    ("코빗", "kr_exchange", "KR", "", "", ""),
    ("코인원", "kr_exchange", "KR", "", "", ""),
    ("Wintermute", "mm_quant", "global", "lever", "wintermute-trading", ""),
    ("Jump Trading", "mm_quant", "global", "greenhouse", "jumptrading", ""),
    ("Flow Traders", "mm_quant", "global", "greenhouse", "flowtraders", ""),
    ("GSR", "mm_quant", "global", "", "", ""),
    ("Amber Group", "mm_quant", "global", "", "", ""),
    ("Keyrock", "mm_quant", "global", "", "", ""),
    ("B2C2", "mm_quant", "global", "", "", ""),
    ("Anthropic", "ai_growth", "global", "greenhouse", "anthropic", ""),
    ("OpenAI", "ai_growth", "global", "ashby", "openai", ""),
    ("Perplexity", "ai_growth", "global", "ashby", "perplexity", ""),
    ("Cursor", "ai_growth", "global", "ashby", "cursor", ""),
    ("Databricks", "ai_growth", "global", "greenhouse", "databricks", ""),
    ("Stripe", "ai_growth", "global", "greenhouse", "stripe", ""),
    ("당근", "ai_growth", "KR", "greenhouse", "daangn", ""),
    ("토스", "ai_growth", "KR", "", "", "https://toss.im/career/jobs"),
    ("리벨리온", "ai_growth", "KR", "", "", "https://rebellions.ai/careers/"),
    ("퓨리오사AI", "ai_growth", "KR", "", "", "https://furiosa.ai/careers"),
    ("업스테이지", "ai_growth", "KR", "", "", "https://careers.upstage.ai/"),
    ("Google", "bigtech", "global", "", "", "https://www.google.com/about/careers/applications/jobs/results/?q=intern"),
    ("네이버", "bigtech", "KR", "", "", "https://recruit.navercorp.com/"),
    ("카카오", "bigtech", "KR", "", "", "https://careers.kakao.com/jobs"),
    ("쿠팡", "bigtech", "KR", "", "", "https://www.coupang.jobs/kr/"),
    ("Jane Street", "quant", "global", "", "", "https://www.janestreet.com/join-jane-street/open-roles/?type=internship"),
    ("Citadel", "quant", "global", "", "", "https://www.citadel.com/careers/open-opportunities/students/"),
    ("Hudson River Trading", "quant", "global", "", "", "https://www.hudsonrivertrading.com/campus-recruiting/"),
    ("Optiver", "quant", "global", "", "", "https://optiver.com/working-at-optiver/career-opportunities/"),
    ("IMC", "quant", "global", "", "", "https://www.imc.com/ap/careers/students-graduates"),
    ("Goldman Sachs", "ib", "global", "", "", "https://www.goldmansachs.com/careers/students/programs-and-internships"),
    ("Morgan Stanley", "ib", "global", "", "", "https://www.morganstanley.com/careers/students-graduates"),
    ("J.P. Morgan", "ib", "global", "", "", "https://careers.jpmorgan.com/global/en/students/programs"),
    ("McKinsey", "consulting", "KR", "", "", "https://www.mckinsey.com/careers/search-jobs"),
    ("BCG", "consulting", "KR", "", "", "https://careers.bcg.com/"),
    ("Bain", "consulting", "KR", "", "", "https://www.bain.com/careers/"),
    ("삼성전자", "semi", "KR", "", "", "https://www.samsungcareers.com/"),
    ("SK하이닉스", "semi", "KR", "", "", "https://recruit.skhynix.com/"),
    ("ASML", "semi", "global", "", "", "https://www.asml.com/en/careers/find-your-job"),
    ("링커리어 인턴", "kr_list", "KR", "", "", "https://linkareer.com/list/intern"),
]
ATS = ("greenhouse", "lever", "ashby", "workable", "smartrecruiters")


def norm(name: str) -> str:
    s = (name or "").lower()
    s = re.sub(r"\b(inc|llc|ltd|corp|co|labs?|group|holdings?)\b\.?", "", s)
    s = re.sub(r"\(주\)|주식회사|㈜", "", s)
    return re.sub(r"[^0-9a-z가-힣]", "", s)


def seed(store: Any) -> int:
    n = 0
    for name, cat, region, ats, token, url in SEED:
        n += store.x("INSERT OR IGNORE INTO companies (name, norm, cat, region, ats, token, career_url, growth_note, on_) VALUES (?,?,?,?,?,?,?,?,1)",
                     (name, norm(name), cat, region, ats, token, url, "")) and 1 or 0
    return n


def get_or_add(store: Any, name: str, cat: str = "", region: str = "", *, on: int = 0) -> int:
    nn = norm(name)
    r = store.one("SELECT id FROM companies WHERE norm=?", (nn,))
    if r:
        return int(r["id"])
    return store.x("INSERT INTO companies (name, norm, cat, region, ats, token, career_url, growth_note, on_) VALUES (?,?,?,?,'','','','',?)",
                   (name[:80], nn, cat, region, on))
