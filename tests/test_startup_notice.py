"""자동 업데이트로 자주 재시작해도 '봇이 켜졌습니다' 알림이 같은 내용으로 반복되지 않는지."""

from __future__ import annotations

from datetime import timedelta

from dealbot.scheduler import mark_startup_notice, startup_notice_due
from dealbot.storage.db import Database
from dealbot.utils.timeutil import utcnow

LINES = ["✅ 쿠팡 API 연결 (골드박스 12건)", "❌ 스레드: API access blocked", "⚠️ 휴대폰 푸시: 미설정 (텔레그램 알림만)"]


def test_first_start_is_announced(db: Database) -> None:
    assert startup_notice_due(db, LINES)


def test_same_checks_are_not_repeated_within_a_day(db: Database) -> None:
    now = utcnow()
    mark_startup_notice(db, LINES, now)
    assert not startup_notice_due(db, LINES, now + timedelta(hours=3))
    # 매번 바뀌는 숫자나 순서만 다른 건 같은 결과로 본다
    changed_count = ["✅ 쿠팡 API 연결 (골드박스 7건)", *LINES[1:]][::-1]
    assert not startup_notice_due(db, changed_count, now + timedelta(hours=3))


def test_new_or_fixed_problem_is_announced(db: Database) -> None:
    now = utcnow()
    mark_startup_notice(db, LINES, now)
    fixed = [LINES[0], "✅ 스레드 @demiyum", LINES[2]]
    assert startup_notice_due(db, fixed, now + timedelta(minutes=5))
    broken = [*LINES, "❌ 템플릿: boom"]
    assert startup_notice_due(db, broken, now + timedelta(minutes=5))


def test_same_checks_are_announced_again_after_a_day(db: Database) -> None:
    now = utcnow()
    mark_startup_notice(db, LINES, now)
    assert startup_notice_due(db, LINES, now + timedelta(hours=24, minutes=1))


def test_broken_saved_value_does_not_block_the_notice(db: Database) -> None:
    db.kv_set("startup_notice", "not json")
    assert startup_notice_due(db, LINES)
