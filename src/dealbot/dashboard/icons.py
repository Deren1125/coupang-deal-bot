"""카드 머리의 작은 표시 (인라인 SVG, 브랜드 색 단순 도형). 외부 이미지·이모지 없이 페이지 안에서만 그린다.
새 SNS 를 붙일 때는 여기 한 줄을 더하거나 모듈 안에 직접 적는다 (코드에 박은 값만 — 설정 값은 넣지 않음)."""

from __future__ import annotations


def _mark(body: str) -> str:
    return f'<svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true" focusable="false">{body}</svg>'


def _letter(bg: str, fg: str, letter: str, *, size: int = 14, stroke: str | None = None) -> str:
    border = f' stroke="{stroke}" stroke-width="1"' if stroke else ""
    return _mark(
        f'<rect x="0.5" y="0.5" width="23" height="23" rx="6" fill="{bg}"{border}/>'
        f'<text x="12" y="16.6" text-anchor="middle" font-size="{size}" font-weight="800" '
        f'font-family="Arial,Helvetica,sans-serif" fill="{fg}">{letter}</text>'
    )


TELEGRAM = _mark(
    '<circle cx="12" cy="12" r="12" fill="#229ED9"/>'
    '<path d="M5.4 11.7l11.2-4.3c.5-.2 1 .1.8.9l-1.9 9c-.1.6-.5.8-1 .5l-2.9-2.1-1.4 1.3c-.2.2-.3.3-.6.3l.2-2.9'
    ' 5.3-4.8c.2-.2 0-.3-.4-.1l-6.5 4.1-2.8-.9c-.6-.2-.6-.6.1-.9z" fill="#fff"/>'
)
INSTAGRAM = _mark(
    '<rect width="24" height="24" rx="6" fill="#E1306C"/>'
    '<rect x="5.5" y="5.5" width="13" height="13" rx="4" fill="none" stroke="#fff" stroke-width="1.8"/>'
    '<circle cx="12" cy="12" r="3.1" fill="none" stroke="#fff" stroke-width="1.8"/>'
    '<circle cx="15.9" cy="8.1" r="1" fill="#fff"/>'
)
THREADS = _letter("#000000", "#ffffff", "@", size=15)
COUPANG = _letter("#ffffff", "#E4252A", "c", size=17, stroke="#e5e7eb")
DEALBOT = _mark(
    '<rect width="24" height="24" rx="6" fill="#FF6B00"/>'
    '<path d="M13.2 4.5L6.8 13h4.4l-1 6.5 6.6-8.6h-4.5z" fill="#fff"/>'
)
NAVER_BLOG = _letter("#03C75A", "#ffffff", "b", size=15)
KAKAO = _mark(
    '<rect width="24" height="24" rx="6" fill="#FEE500"/>'
    '<path d="M12 6c-4 0-7.3 2.6-7.3 5.7 0 2 1.4 3.8 3.4 4.8l-.7 2.6c-.1.3.2.5.4.3l3.1-2.1h1.1c4 0 7.3-2.6'
    ' 7.3-5.7S16 6 12 6z" fill="#3C1E1E"/>'
)
# 발행 대기 글: 짙은 회색 바탕에 쌓인 글 세 줄
WAITING = _mark(
    '<rect width="24" height="24" rx="6" fill="#374151"/>'
    '<path d="M7 8h10M7 12h10M7 16h6" stroke="#fff" stroke-width="2" stroke-linecap="round" fill="none"/>'
)
# 링크 기다리는 딜: 주황 바탕에 사슬 고리
LINK = _mark(
    '<rect width="24" height="24" rx="6" fill="#FF6B00"/>'
    '<path d="M10.6 13.4a3 3 0 0 0 4.2 0l2.4-2.4a3 3 0 0 0-4.2-4.2l-1 1M13.4 10.6a3 3 0 0 0-4.2 0L6.8 13a3 3 0 0 0 4.2 4.2l1-1"'
    ' stroke="#fff" stroke-width="2" stroke-linecap="round" fill="none"/>'
)
GENERIC = _mark('<rect width="24" height="24" rx="6" fill="#9ca3af"/><circle cx="12" cy="12" r="4" fill="#fff"/>')

REFRESH = (
    '<svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true" focusable="false">'
    '<path d="M19.5 12a7.5 7.5 0 1 1-2.2-5.3" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round"/>'
    '<path d="M19.6 4.2v4.6H15" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" '
    'stroke-linejoin="round"/></svg>'
)
CHECK = (
    '<svg viewBox="0 0 24 24" width="13" height="13" aria-hidden="true" focusable="false">'
    '<path d="M5 12.5l4.2 4.2L19 7" fill="none" stroke="#15803d" stroke-width="2.6" stroke-linecap="round" '
    'stroke-linejoin="round"/></svg>'
)


# ---------------------------------------------------------------- 앱 아래 탭 (Sns-board, 주인 10/18) — 선 아이콘, 글자색(currentColor)
def _tab(body: str) -> str:
    return (f'<svg viewBox="0 0 24 24" width="24" height="24" aria-hidden="true" focusable="false" fill="none" '
            f'stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round">{body}</svg>')


TAB_TODO = _tab('<rect x="4" y="3.5" width="16" height="17" rx="3"/><path d="M8 9.5l2 2 4-4M8 15.5h8"/>')
TAB_DEAL = _tab('<path d="M3.5 12.6V5a1.5 1.5 0 0 1 1.5-1.5h7.6l8 8a1.5 1.5 0 0 1 0 2.1l-6.4 6.4a1.5 1.5 0 0 1-2.1 0z"/>'
                '<circle cx="8.3" cy="8.3" r="1.4"/>')
TAB_BLOG = _tab('<path d="M14.5 4.5l5 5L9 20H4v-5z"/><path d="M12.5 6.5l5 5"/>')
# SNS (10/26): 말풍선 안 하트 — 스레드·인스타 · X · 룩북 릴스
TAB_SNS = _tab('<path d="M12 4c4.6 0 8.2 3.1 8.2 7s-3.6 7-8.2 7c-1 0-2-.15-2.9-.45L4.6 19.6l1.2-3.6C4.6 14.7 3.8 13 3.8 11c0-3.9 3.6-7 8.2-7z"/>'
               '<path d="M12 14.1l-2.4-2.3a1.45 1.45 0 0 1 2.05-2.05l.35.35.35-.35a1.45 1.45 0 0 1 2.05 2.05z"/>')
TAB_STATS = _tab('<path d="M4 20h16"/><rect x="5.5" y="11" width="3" height="6" rx="1"/><rect x="10.5" y="6" width="3" height="11" rx="1"/>'
                 '<rect x="15.5" y="9" width="3" height="8" rx="1"/>')
TAB_CONNECT = _tab('<path d="M10 14a4 4 0 0 0 5.7 0l3-3a4 4 0 0 0-5.7-5.7l-1 1"/><path d="M14 10a4 4 0 0 0-5.7 0l-3 3a4 4 0 0 0 5.7 5.7l1-1"/>')
# 아래 탭 6개 (주인 10/26 '밑에 배너의 탭을 늘리거나') — 390px 에서 한 칸 65px, 글자 한 줄
TABS = (("todo", "할 일", TAB_TODO), ("deal", "딜", TAB_DEAL), ("blog", "블로그", TAB_BLOG), ("sns", "SNS", TAB_SNS),
        ("stats", "통계", TAB_STATS), ("connect", "연결", TAB_CONNECT))
