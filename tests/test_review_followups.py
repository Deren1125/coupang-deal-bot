"""리뷰에서 고치지 못하고 남았던 항목 회귀 테스트 (C4 한줄평 검사 · C5 정보 글/요약기 · C6 배포·종료)."""

from __future__ import annotations

import asyncio
import json
import os
import signal
import time
from pathlib import Path
from typing import Any

import pytest

import dealbot.commentary as commentary
import dealbot.scheduler as sched
import dealbot.summarize as summarize
from dealbot.commentary import check_comment, check_short_name, check_take, drop_repeats
from dealbot.config import Settings
from dealbot.infopost import _clean_lines
from dealbot.monitoring.admin import describe_kind
from dealbot.summarize import InfoSummarizer, estimate_discount
from dealbot.utils.text import clean_name

# ================================================================ C4: 한줄평·스레드 판단 검사
FACTS = "쿠팡 최저가 35,100원보다 29% 싸요. / 개당 1,038원"
NAME = "HOLLYS 할리스 바닐라 딜라이트 로우슈거, 24개"


@pytest.mark.parametrize(
    "text",
    ["1,038번 마셔도 돼요.", "29가지 맛이 있어요.", "24명 사무실에 하나씩 돌리기 좋아요.", "24잔 마실 수 있어요.", "24회 나눠 마셔요.",
     "24살 자취생한테 좋아요.", "24 시간 들고 다녀도 돼요.", "29 일 동안 충분해요."],
)
def test_numbers_with_other_counters_are_blocked(text: str) -> None:
    """정보·상품명의 숫자를 다른 단위(번·가지·명·잔·회·살, 띄어 쓴 시간·일)로 바꿔 쓰면 지어낸 수량."""
    assert check_comment(text, FACTS, NAME) is None


def test_pack_count_can_be_called_ea() -> None:
    assert check_comment("24개 들어 있어서 나눠 마시기 좋아요.", "캔당 663원", "코카콜라 제로 355ml 24캔")
    assert check_comment("40개라 사무실에 두기 좋아요.", "병당 300원", "스파클 생수 500ml 40병")
    assert check_comment("12개 들어 있어요.", "캔당 663원", "코카콜라 제로 355ml 24캔") is None
    assert check_short_name("코카콜라 제로 24개", "코카콜라 제로 355ml 24캔") == "코카콜라 제로 24개"


def test_normal_price_is_not_half_price() -> None:
    assert check_comment("일반 가격보다 확 내려왔어요.", FACTS, NAME)
    assert check_comment("반 가격이라 좋아요.", FACTS, NAME) is None  # 실제로 50% 이상 싸지 않음


SHOWN = ["24,900원", "쿠팡보다 15%↓", "개당 1,038원 꼴", "무료 로켓배송"]


@pytest.mark.parametrize(
    "text",
    ["개당 1,038원이면 커피 한 잔 값도 안 돼요.", "개당 1,038원이라 거의 공짜예요.", "무료 로켓배송이라 내일 아침이면 도착해요.",
     "개당 1,038원이라 좋고, 사무실에 두기 편해요."],
)
def test_stripped_remainder_that_needs_the_lead_is_dropped(text: str) -> None:
    """앞머리(단가·배송)를 빼면 딜 전체 값 얘기·근거 없는 약속·깨진 문장이 되는 건 통째로 뺀다."""
    assert drop_repeats(text, SHOWN) is None


def test_independent_remainder_is_kept() -> None:
    assert drop_repeats("개당 1,038원이라 박스로 쟁여두기 편해요.", SHOWN) == "박스로 쟁여두기 편해요."
    assert drop_repeats("사무실에서 쓰기 좋아요.", SHOWN) == "사무실에서 쓰기 좋아요."


def test_urgency_and_casual_banned_forms() -> None:
    assert check_comment("이 가격이면 놓치지 않게 담아두세요.", FACTS, NAME) is None
    assert check_comment("평소보다 싸게 나왔어요.", FACTS, NAME) is None  # H-07
    assert check_take("놓치지 않게 담아둘 만함", FACTS, NAME) is None
    assert check_take("평소보다 확실히 쌈", FACTS, NAME) is None
    for ok in ("강추위에 좋음", "놓치는 사람 없게 공유함", "들고 다니다 보면 금방 씀"):
        assert check_take(ok, FACTS, NAME) == ok, ok
    assert check_take("들고 다닙니다", FACTS, NAME) is None


def test_commentary_alert_has_a_korean_label() -> None:
    assert describe_kind("commentary") == "AI 한줄평"


# ================================================================ C5: 요약기·정보 글
@pytest.mark.parametrize(
    ("text", "amount"),
    [
        ("5,000원 할인 가능", 5000), ("신규 회원 1만원 할인 가능", 10000), ("5천원 할인가능한 쿠폰 지급", 5000),
        ("5천원 쿠폰 받아가세요", 5000), ("5,000원 쿠폰 사용 가능", 5000), ("3,000원 할인 적용", 3000),
        ("구매 시 1만원 할인 적용!", 10000), ("카드 결제시 3만원 할인 후 최종가 99,000원", 30000),
        # 가격을 가리키는 꼴은 여전히 혜택이 아님
        ("39,000원 할인 적용시", None), ("29,000원 할인적용가", None), ("29,000원 할인 후 가격", None), ("1,599,000원 쿠폰 사용시", None),
    ],
)
def test_benefit_amounts_are_read_again(text: str, amount: int | None) -> None:
    assert estimate_discount(text) == (None, amount)


def _fake_claude(d: Path, monkeypatch: pytest.MonkeyPatch, out: str, *, sleep: float = 0) -> Path:
    d.mkdir(parents=True, exist_ok=True)
    (d / "out.json").write_text(out, encoding="utf-8")
    script = d / "claude"
    script.write_text(
        "#!/bin/sh\n"
        f'echo $$ > "{d}/pid.txt"\n'
        f'echo x >> "{d}/calls.txt"\n'
        f'cat > /dev/null\n'
        + (f"exec sleep {sleep}\n" if sleep else "")
        + f'cat "{d}/out.json"\n',
        encoding="utf-8",
    )
    script.chmod(0o755)
    monkeypatch.setattr(commentary, "find_claude", lambda: str(script))
    return script


async def test_cli_verbose_list_output_is_read(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CLI 설정이 verbose 면 메시지 목록(JSON 배열)으로 나온다 — 마지막 result 를 읽는다 (한줄평과 같게)."""
    answer = "할인율: 60\n할인액: 0\n\n라코스테 최대 60% 세일\n- 기간: 9/11~9/14"
    out = json.dumps([{"type": "system"}, {"type": "assistant"}, {"type": "result", "is_error": False, "result": answer}], ensure_ascii=False)
    _fake_claude(tmp_path, monkeypatch, out)
    r = await InfoSummarizer(None).summarize(title="라코스테", source="뽐뿌", text="본문 " * 20)
    assert r.error is None and r.discount_rate == 60 and r.text and r.text.startswith("라코스테 최대 60% 세일")


def _dead(pid: int) -> bool:
    status = Path(f"/proc/{pid}/status")
    if not status.exists():
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        return False
    return "State:\tZ" in status.read_text()  # 죽고 거둬지기만 기다리는 상태


async def test_cancelled_summary_does_not_leave_the_cli_running(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_claude(tmp_path, monkeypatch, "{}", sleep=30)
    s = InfoSummarizer(None, cli_timeout=60)
    task = asyncio.create_task(s.summarize(title="t", source="s", text="본문 " * 10))
    for _ in range(200):
        if (tmp_path / "pid.txt").exists():
            break
        await asyncio.sleep(0.02)
    pid = int((tmp_path / "pid.txt").read_text().strip())
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    for _ in range(100):
        if _dead(pid):
            break
        await asyncio.sleep(0.03)
    assert _dead(pid)


async def test_cli_login_failure_retries_later_without_restart(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _fake_claude(tmp_path, monkeypatch, json.dumps({"is_error": True, "result": "Invalid API key · Please run /login"}))
    s = InfoSummarizer(None)
    r = await s.summarize(title="t", source="s", text="본문 " * 10)
    assert "다시 로그인하면" in (r.error or "") and not s.available and s.disabled_reason is None
    assert (await s.summarize(title="t", source="s", text="본문 " * 10)).error == r.error
    assert len((tmp_path / "calls.txt").read_text().splitlines()) == 1  # 쉬는 동안은 안 부름
    now = time.monotonic()
    monkeypatch.setattr(summarize.time, "monotonic", lambda: now + summarize.CLI_AUTH_RETRY_SECONDS + 1)
    assert s.available
    await s.summarize(title="t", source="s", text="본문 " * 10)
    assert len((tmp_path / "calls.txt").read_text().splitlines()) == 2  # 시간이 지나면 다시 해 봄 (재시작 불필요)


def test_summarizer_off_switch_and_cli_wiring(settings: Settings, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from dealbot.app import DealBot

    _fake_claude(tmp_path, monkeypatch, "{}")
    settings.collectors = []
    settings.secrets.anthropic_api_key = None
    settings.info_posts.summarizer.enabled = False
    bot = DealBot(settings)
    try:
        assert not bot.summarizer.configured and bot.summarizer.backend is None  # 설정에서 끄면 CLI 도 안 씀
        assert "요약 꺼짐 (설정)" in bot.reporter._info_summary_text()
    finally:
        bot.db.close()
    settings.info_posts.summarizer.enabled = True
    bot = DealBot(settings)
    try:
        assert bot.summarizer.backend == "cli" and bot.summarizer.cli_model == settings.publish.commentary.model
        assert bot.summarizer.cli_timeout >= 120
        line = bot.reporter._info_summary_text()
        assert "서버 claude CLI" in line and "ANTHROPIC_API_KEY 없음" not in line
    finally:
        bot.db.close()


def test_inline_links_stop_at_korean_text_and_drop_bare_labels() -> None:
    lines = _clean_lines(
        "쿠폰은 https://link.coupang.com/a/abc에서 받으세요\n"
        "11번가 https://www.11st.co.kr/p/1?lptag=A&x=1에서 확인\n"
        "링크: https://link.coupang.com/a/OTHERS\n"
        "구매링크: https://link.coupang.com/a/x\n"
        "주소 https://example.com/a, 그리고 끝"
    )
    assert lines == ["쿠폰은 원문 링크에서 받으세요", "11번가 https://www.11st.co.kr/p/1?x=1에서 확인", "주소 https://example.com/a, 그리고 끝"]
    assert not any(x.rstrip().endswith((":", "：")) for x in lines) and "%EC" not in " ".join(lines)


def test_hype_only_brackets_disappear() -> None:
    assert clean_name("[LF몰] 라코스테 최대 60%세일 (역대급)") == "[LF몰] 라코스테 최대 60%세일"
    assert clean_name("배민 1만원 쿠폰 [역대급]") == "배민 1만원 쿠폰"
    assert clean_name("비비고 왕교자 (2개)") == "비비고 왕교자 (2개)"


# ================================================================ C6: setup.sh · https.sh · 종료
def _server(tmp_path: Path):  # type: ignore[no-untyped-def]
    from test_deploy_lightsail import Server

    return Server(tmp_path)


KEYS = "".join(f"{k}=x\n" for k in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_CHANNEL_ID", "TELEGRAM_ADMIN_CHAT_ID", "COUPANG_ACCESS_KEY",
                                      "COUPANG_SECRET_KEY"))


def test_setup_rerun_does_not_deploy_a_commit_the_selftest_rejected(tmp_path: Path) -> None:
    """점검에 떨어진 커밋을 setup.sh 다시 실행이 깔아 버리거나 그 기록을 지우면 안 된다."""
    server = _server(tmp_path)
    good = server.head()
    bad = server.commit("broken", {"BROKEN": "x"})
    r = server.run()  # 자동 업데이트: 점검 실패 → 되돌리고 표식
    assert r.returncode == 1 and server.head() == good
    marker = server.srv / "var" / f"update_bad_{server.git('rev-parse', '--short', bad)}"
    assert marker.exists()
    (server.srv / ".env").write_text(KEYS, encoding="utf-8")
    server.env["HOME"] = str(tmp_path / "home")
    r = server.run(script="setup.sh")
    assert r.returncode == 0, r.stdout + r.stderr
    assert server.head() == good and server.runs(good) and marker.exists()
    assert "최신 코드를 못 받았어요" in r.stdout
    script = (server.srv / "deploy" / "lightsail" / "setup.sh").read_text(encoding="utf-8")
    assert not [ln for ln in script.splitlines() if "git pull" in ln and not ln.lstrip().startswith("#")]  # 직접 받지 않음


def _caddy_run(tmp_path: Path, caddyfile: str, ip: str = "3.35.1.2", validate_ok: bool = True):  # type: ignore[no-untyped-def]
    server = _server(tmp_path)
    sim = server.sim
    sudo = (sim / "bin" / "sudo").read_text(encoding="utf-8")
    (sim / "bin" / "sudo").write_text(sudo.replace("  cp) shift; exec cp \"$@\" ;;", "  cp) shift; exec cp \"$@\" ;;\n  caddy) shift; exec caddy \"$@\" ;;"),
                                      encoding="utf-8")
    (sim / "bin" / "caddy").write_text(
        '#!/usr/bin/env bash\necho "caddy $*" >> "$SIM/calls.log"\n'
        + ("" if validate_ok else '[ "$1" = validate ] && exit 1\n') + "exit 0\n", encoding="utf-8")
    (sim / "etc" / "Caddyfile").write_text(caddyfile, encoding="utf-8")
    (server.srv / ".env").write_text("TELEGRAM_BOT_TOKEN=fake\n", encoding="utf-8")
    server.env["CADDY_DIR"] = str(sim / "etc")
    server.env["FAKE_IP"] = ip
    return server, server.run(script="https.sh")


OTHER = "blog.example.com {\n\treverse_proxy 127.0.0.1:3000\n}\n"


@pytest.mark.parametrize("old_ip", ["3-35-1-2", "52-79-136-64"])  # 같은 IP 로 다시 / IP 가 바뀐 뒤
def test_https_removes_its_old_inline_block_next_to_other_sites(tmp_path: Path, old_ip: str) -> None:
    before = f"{old_ip}.sslip.io {{\n\treverse_proxy 127.0.0.1:8080\n}}\n\n" + OTHER
    server, r = _caddy_run(tmp_path, before)
    assert r.returncode == 0, r.stdout + r.stderr
    caddy = (server.sim / "etc" / "Caddyfile").read_text(encoding="utf-8")
    assert "sslip.io {" not in caddy and OTHER.strip() in caddy  # 봇 주소 정의는 dealbot.caddy 한 곳에만
    assert caddy.count(f"import {server.sim / 'etc'}/dealbot.caddy") == 1
    assert (server.sim / "etc" / "dealbot.caddy").read_text(encoding="utf-8").count("3-35-1-2.sslip.io {") == 1
    assert "caddy validate" in server.log("calls.log")


def test_https_restores_the_caddyfile_when_validation_fails(tmp_path: Path) -> None:
    server, r = _caddy_run(tmp_path, OTHER, validate_ok=False)
    assert r.returncode == 1 and "되돌렸어요" in r.stdout
    assert (server.sim / "etc" / "Caddyfile").read_text(encoding="utf-8") == OTHER
    assert "restart caddy" not in server.log("calls.log")


async def test_shutdown_does_not_wait_for_a_running_collector(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """재시작 때 기다리는 건 발행 루프뿐 — 긁어 오던 수집기는 바로 끊는다."""
    from test_scheduler_shutdown import FakeBot

    monkeypatch.setattr(sched, "VAR_DIR", tmp_path / "var")
    monkeypatch.setattr(sched, "CODE_SHA", "abc1234")
    monkeypatch.setattr(sched, "SHUTDOWN_GRACE_SECONDS", 30.0)
    monkeypatch.setenv("INVOCATION_ID", "x")  # systemd 아래처럼 (기다릴 수 있는 시간이 길어도)
    collector_started = asyncio.Event()
    collector_cancelled: list[bool] = []

    async def stuck_collector(bot: Any, stop: asyncio.Event) -> None:
        collector_started.set()
        try:
            await asyncio.sleep(3600)  # stop 을 안 보고 오래 걸리는 수집
        except asyncio.CancelledError:
            collector_cancelled.append(True)
            raise

    async def idle(bot: Any, stop: asyncio.Event) -> None:
        await stop.wait()

    monkeypatch.setattr(sched, "collector_loop", stuck_collector)
    for name in ("daily_summary_loop", "blog_digest_loop", "maintenance_loop", "heartbeat_loop", "soldout_loop"):
        monkeypatch.setattr(sched, name, idle)
    bot = FakeBot(publish_seconds=0.2)
    task = asyncio.create_task(sched.run_forever(bot))  # type: ignore[arg-type]
    await asyncio.wait_for(asyncio.gather(collector_started.wait(), bot.publishing.wait()), 5)
    started = time.monotonic()
    os.kill(os.getpid(), signal.SIGTERM)
    await asyncio.wait_for(task, 10)
    assert time.monotonic() - started < 5 and collector_cancelled == [True]
    assert "threads_reply_with_link" in bot.calls and bot.calls[-1] == "close"  # 발행은 마치고 끔


def test_shutdown_grace_outside_systemd_fits_docker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("INVOCATION_ID", raising=False)
    monkeypatch.delenv("DEALBOT_SHUTDOWN_GRACE_SECONDS", raising=False)
    assert sched.shutdown_grace() < 10  # docker compose 기본 10초 안에 close() 까지
    monkeypatch.setenv("INVOCATION_ID", "x")
    assert sched.shutdown_grace() == sched.SHUTDOWN_GRACE_SECONDS
    monkeypatch.setenv("DEALBOT_SHUTDOWN_GRACE_SECONDS", "3")
    assert sched.shutdown_grace() == 3.0
