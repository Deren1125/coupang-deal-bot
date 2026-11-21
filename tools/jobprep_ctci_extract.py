"""CtCI 문제 목록 한 번 뽑기 (주인 11/22 취준 탭 — docs/jobprep_plan.md 5장). Claude·Gemini 안 씀 · 손으로 한 번 · `nice -n 19`.

    nice -n 19 python tools/jobprep_ctci_extract.py [--data ~/jobprep-data]

읽는 것: <data>/ctci/CtCI_part1_*.pdf · CtCI_part2_*.pdf (pypdf). 쓰는 것 (git 밖 · 폴더 700 · 파일 600):
  <data>/ctci/index.json   [{qid, ch, ch_title, title, q_part, q_page, sol_part, sol_page, sol_end, kind}]
  <data>/ctci/text/<qid>.json   {question, book_hints_raw, solution}   ← 책 내용 (로그인 아래 API 로만)
손으로 고칠 것: <data>/ctci/overrides.json  {"1.5": {"title": …, "q_page": …, "sol_page": …}} — 다시 돌리면 덮음.
검증: 장별 개수 9,8,6,12,8,10,12,14,8,11,6,11,8,7,7,26,26 (합 189) · 짝 없는 풀이 → 표로 찍고 종료 코드 1.
책 글은 화면(표준 출력)에 찍지 않는다 (qid·제목·쪽 번호만).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from dealbot.dashboard.jobprep import ctci  # noqa: E402


def write(p: Path, data: object) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(p.parent, 0o700)
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)


def outline_pages(reader: object) -> dict[str, int]:
    out: dict[str, int] = {}

    def walk(items: list) -> None:
        for x in items:
            if isinstance(x, list):
                walk(x)
                continue
            try:
                out[str(x.title).strip()] = int(reader.get_destination_page_number(x))  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                pass

    walk(reader.outline)  # type: ignore[attr-defined]
    return out


def chapter_starts(ol: dict[str, int]) -> list[int]:
    starts = []
    for ch in range(1, 18):
        hit = [p for t, p in ol.items() if re.fullmatch(rf"{ch}\.\s+{re.escape(ctci.CH_TITLES[ch])}", t)]
        if not hit:
            raise SystemExit(f"목차에서 {ch}장 문제 시작을 못 찾음")
        starts.append(hit[0])
    sol = [p for t, p in ol.items() if re.fullmatch(r"X\.\s+Solutions", t)]
    if not sol:
        raise SystemExit("목차에서 'X. Solutions' 를 못 찾음")
    return [*starts, sol[0]]


def solution_starts(pages: list[str], begin: int) -> list[int]:
    """풀이 장 시작 = 쪽 머리글 'Solutions to Chapter N' 이 처음 나온 쪽의 앞 쪽 (장 표지). 끝 = 'Advanced Topics' 머리 쪽."""
    first: dict[int, int] = {}
    for i in range(begin, len(pages)):
        t = pages[i] or ""
        m = re.search(r"Solutions to Chapter\s*(\d[\d ]?)", t[:200]) or re.search(r"Solutions to Chapter\s*(\d[\d ]?)", t[-200:])
        if m:
            c = int(m.group(1).replace(" ", ""))
            if 1 <= c <= 17:
                first.setdefault(c, i)
    if len(first) != 17:
        raise SystemExit(f"풀이 장 머리글을 {len(first)}개만 찾음")
    end = next((i for i in range(first[17], len(pages)) if "Advanced Topics" in (pages[i] or "")[:200]), len(pages))
    return [first[c] - 1 for c in range(1, 18)] + [end]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default=os.environ.get("JOBPREP_DATA_DIR") or "~/jobprep-data")
    a = ap.parse_args()
    try:
        from pypdf import PdfReader
    except ImportError:
        print("pypdf 가 없어요 — pip install -e . 뒤 다시 (pyproject 에 있음)")
        return 2
    d = Path(os.path.expanduser(a.data)) / "ctci"
    p1 = sorted(d.glob("CtCI_part1*.pdf"))
    p2 = sorted(d.glob("CtCI_part2*.pdf"))
    if not p1 or not p2:
        print(f"PDF 두 권이 {d} 에 없어요")
        return 2
    r1, r2 = PdfReader(str(p1[0])), PdfReader(str(p2[0]))
    pages = [(p.extract_text() or "") for p in r1.pages]
    n1 = len(pages)
    pages += [(p.extract_text() or "") for p in r2.pages]
    ch_pages = chapter_starts(outline_pages(r1))
    sol_pages = solution_starts(pages, ch_pages[-1])
    qs = ctci.split_questions(pages, ch_pages)
    missing = ctci.find_solutions(pages, sol_pages, qs)
    over_p = d / "overrides.json"
    if over_p.exists():
        qs = ctci.apply_overrides(qs, json.loads(over_p.read_text(encoding="utf-8")))
        missing = [m for m in missing if not (json.loads(over_p.read_text(encoding="utf-8")).get(m) or {}).get("solution")]
    errs = ctci.verify(qs, missing)
    rows = ctci.index_rows(qs, n1)
    for q in qs:
        write(d / "text" / f"{q['qid']}.json", {"question": q.get("question") or "", "book_hints_raw": q.get("book_hints_raw") or "",
                                                 "solution": q.get("solution") or ""})
    write(d / "index.json", rows)
    print(f"문제 {len(rows)}개 · 코드 {sum(1 for r in rows if r['kind'] == 'code')} · 설명 {sum(1 for r in rows if r['kind'] == 'explain')}")
    for r in rows:
        if not r["title"] or r["sol_page"] is None:
            print(f"  확인: {r['qid']} 제목='{r['title']}' 문제쪽={r['q_part']}-{r['q_page']} 풀이쪽={r['sol_part']}-{r['sol_page']}")
    if errs:
        print("맞지 않음:")
        for e in errs:
            print("  -", e)
        return 1
    print("검증 통과 (장별 개수 · 풀이 짝)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
