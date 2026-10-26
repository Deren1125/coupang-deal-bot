"""연결 탭 정리 (주인 10/26 — 로그인 관리가 맨 위에 생긴 뒤).

- 맨 위 '로그인 관리'(logins.js)가 네이버 블로그 셋의 로그인 상태·원격 로그인을 보여 주므로, 아래의 '네이버 로그인' 칸은 뺀다.
  그 칸에만 있던 것은 자리를 옮겨 남긴다: 쇼핑커넥트 줄(오늘 링크·검색·대기 — 로그인 칸이 안 보여 주는 것)은 '봇 상태' 안,
  블로그별 '로그인 확인'은 '봇 상태' 더보기.
- 카톡(예비)은 '알림' 칸 안 한 줄 (테스트 카톡은 알림 더보기).
- 중복 없이 (주인 10/26): 로그인 관리에는 '카톡 알림 토큰' · '딜봇 인스타·스레드 (API 연결)' 가리킴 줄을 두지 않는다 — 상태·단추는 제 칸에.
- '인스타 · 스레드'(딜봇 API 연결 · 다시 연결 · 더보기의 테스트 글·토큰/코드 넣기 · 초대 링크)는 그대로.
"""

from __future__ import annotations

from dealbot.dashboard import logins, webapp

STATIC = webapp.STATIC_DIR


def _connect() -> str:
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    start = js.index("function vConnect()")
    return js[start:js.index("\n}\n", start)]


def test_connect_tab_has_no_second_login_list_and_keeps_what_only_it_had() -> None:
    v = _connect()
    assert "sec('네이버 로그인'" not in v and "'지금 확인'" not in v and "sec('카톡 (예비)'" not in v
    # 순서: (logins.js 가 맨 위에 붙임) 알림 → 인스타 · 스레드 → 봇 상태
    assert v.index("sec('알림'") < v.index("sec('인스타 · 스레드'") < v.index("sec('봇 상태'")
    alarm = v[v.index("sec('알림'"):v.index("const sns = S.sns")]
    assert "(k.actions || []).filter(a => a !== kAuth && a !== kConn)" in alarm  # 테스트 카톡 = 알림 더보기
    assert "row('카톡 (예비)'" in alarm and "'카카오 열기 ↗'" in alarm and "panel(kKid, kConn" in alarm
    sns = v[v.index("const sns = S.sns"):v.index("sec('봇 상태'")]
    assert "sns.actions" in sns and "'다시 연결'" in sns and "초대 링크" in sns and "copy(v.url)" in sns
    bots = v[v.index("sec('봇 상태'"):]
    assert "row('쇼핑커넥트', x.text" in bots and "(c.sc)" in bots
    assert "nv[b].action" in bots and "l + ' 로그인 확인'" in bots  # 블로그별 로그인 확인은 더보기에


def test_login_panel_does_not_repeat_kakao_or_the_meta_connection() -> None:
    src = (STATIC.parent / "logins.py").read_text(encoding="utf-8")
    for gone in ("def _kakao_row", "def _sns_row", '"카톡 알림 토큰"', '"딜봇 인스타·스레드 (API 연결)"', "연결 탭 › 인스타 · 스레드"):
        assert gone not in src, gone
    assert logins.STATE_TEXT["needed"] == "로그인 필요"  # 칸 자체는 그대로
