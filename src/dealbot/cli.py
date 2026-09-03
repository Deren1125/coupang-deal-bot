"""CLI: python -m dealbot <command>"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from datetime import timedelta

from dealbot import __version__
from dealbot.config import Settings, load_settings
from dealbot.logging_setup import setup_logging
from dealbot.models import Deal, DealVerdict, Product
from dealbot.utils.timeutil import from_iso, utcnow

log = logging.getLogger("dealbot.cli")


def _settings(args: argparse.Namespace) -> Settings:
    s = load_settings(args.config)
    setup_logging(s.app.log_level, s.log_dir if not args.no_log_file else None)
    return s


def sample_deal() -> Deal:
    p = Product(
        source="goldbox",
        product_id="coupang:0000000",
        shop="coupang",
        headline="샘플 · 오늘의 특가",
        name="[샘플] 스탠리 텀블러 퀜처 H2.0 플로우스테이트 1.18L",
        price=29900,
        url="https://www.coupang.com/vp/products/0000000",
        image_url="https://static.coupangcdn.com/image/coupang/common/logo_coupang_w350.png",
        original_price=49900,
        discount_rate=40.0,
        category="주방용품",
        is_rocket=True,
        is_free_shipping=True,
        affiliate_url="https://link.coupang.com/a/sample",
    )
    v = DealVerdict(
        is_deal=True,
        reasons=["discount_rate>=30%", "below_30d_avg>=15%"],
        discount_rate=40.0,
        avg_price=42000,
        below_avg_pct=28.8,
        sample_count=12,
        score=40.0,
    )
    return Deal(product=p, verdict=v, affiliate_url=p.affiliate_url, detected_at=utcnow())


# ------------------------------------------------------------------ commands
async def cmd_run(args: argparse.Namespace) -> int:
    from dealbot.app import DealBot
    from dealbot.scheduler import run_forever

    s = _settings(args)
    bot = DealBot(s)
    await run_forever(bot)
    return 0


async def cmd_once(args: argparse.Namespace) -> int:
    from dealbot.app import DealBot

    s = _settings(args)
    if args.dry_run:
        s.publish.dry_run = True
    bot = DealBot(s)
    try:
        await bot.start_telegram(polling=False)
        results = await bot.run_once(args.collector or None, publish=not args.no_publish)
        for r in results:
            print(json.dumps(r, ensure_ascii=False))
        print(bot.reporter.status_text().replace("<b>", "").replace("</b>", "").replace("<code>", "").replace("</code>", ""))
    finally:
        await bot.close()
    return 0


async def cmd_check(args: argparse.Namespace) -> int:
    """설정/자격 증명 점검."""
    from dealbot.app import DealBot

    s = _settings(args)
    ok = True
    print(f"dealbot v{__version__}")
    print(f"config: {s.config_path or '(defaults)'}  data_dir: {s.data_dir}  templates: {s.templates_dir}")
    print(f"collectors: {[c.name + ('' if c.enabled else '(off)') for c in s.collectors]}")
    print(f"deal rules: discount>={s.deal.min_discount_rate}% or below {s.deal.history_days}d avg by {s.deal.min_below_average_pct}% (min {s.deal.min_history_samples} samples)")
    print(f"publish: enabled={s.publish.enabled} dry_run={s.publish.dry_run} {s.publish.max_per_hour}/h {s.publish.max_per_day}/d dedup={s.publish.dedup_days}d")

    bot = DealBot(s)
    try:
        print("shops:")
        for shop in bot.registry.all():
            print(f"  - {shop.name:8s} {bot.links.describe(shop)}")
        await bot.start_telegram(polling=False)
        for status, text in await bot.self_check():
            tag = "[OK]" if status else "[SKIP]" if status is None else "[FAIL]"
            ok = ok and status is not False
            print(f"{tag} {text}")
        if bot.notifier.enabled:
            sent = await bot.notifier.send("🔧 dealbot check: 관리자 알림 연결 확인", silent=True)
            print("[OK] 관리자 챗: 메시지 전송" if sent else "[FAIL] 관리자 챗: 전송 실패 (봇에게 /start 를 먼저 보내세요)")
            ok = ok and sent
        else:
            print("[SKIP] 관리자 챗: TELEGRAM_ADMIN_CHAT_ID 미설정")
        if bot.push.enabled:
            sent = await bot.push.send("dealbot check", "휴대폰 푸시 연결 확인", click_url=bot.notifier.telegram_link)
            print(f"[OK] 휴대폰 푸시({bot.push.provider}): 전송" if sent else f"[FAIL] 휴대폰 푸시({bot.push.provider}): 전송 실패")
            ok = ok and sent
    finally:
        await bot.close()
    print("RESULT:", "OK" if ok else "PROBLEMS FOUND")
    return 0 if ok else 1


async def cmd_chat_id(args: argparse.Namespace) -> int:
    """봇에게 최근 메시지를 보낸 채팅들의 ID 출력 (관리자 챗 ID / 채널 ID 확인용)."""
    from telegram import Bot

    s = _settings(args)
    if not s.secrets.telegram_bot_token:
        print("TELEGRAM_BOT_TOKEN 이 설정되어 있지 않습니다.")
        return 1
    bot = Bot(s.secrets.telegram_bot_token)
    async with bot:
        me = await bot.get_me()
        print(f"bot: @{me.username}\n")
        updates = await bot.get_updates(timeout=5, allowed_updates=["message", "channel_post", "my_chat_member"])
        if not updates:
            print("최근 업데이트가 없습니다. 봇에게 개인 메시지를 보내거나(예: /start), 봇이 관리자로 있는 채널에 아무 글이나 올린 뒤 다시 실행하세요.")
            print("(봇이 폴링 중이면 업데이트를 가져올 수 없으니 `dealbot run` 은 잠시 중지)")
            return 0
        seen: set[int] = set()
        for u in updates:
            chat = None
            if u.message:
                chat = u.message.chat
            elif u.channel_post:
                chat = u.channel_post.chat
            elif u.my_chat_member:
                chat = u.my_chat_member.chat
            if chat is None or chat.id in seen:
                continue
            seen.add(chat.id)
            label = chat.title or chat.full_name or chat.username or ""
            print(f"{chat.type:10s} id={chat.id:<16} {label}")
        print("\n개인 챗 id → TELEGRAM_ADMIN_CHAT_ID, 채널 id(-100...) → TELEGRAM_CHANNEL_ID 에 넣으세요.")
    return 0


def cmd_render(args: argparse.Namespace) -> int:
    from dealbot.publisher.templates import TemplateRenderer

    s = _settings(args)
    r = TemplateRenderer(s.templates_dir, s.app.timezone)
    shop = s.shop_registry().get("coupang")
    print(r.render_deal(sample_deal(), "https://link.coupang.com/a/sample", shop=shop, template=s.publish.template))
    return 0


async def cmd_test_post(args: argparse.Namespace) -> int:
    """샘플 딜을 채널(또는 --to admin 이면 관리자 챗)에 실제로 보내 양식을 확인."""
    from dealbot.app import DealBot

    s = _settings(args)
    s.publish.dry_run = False
    bot = DealBot(s)
    try:
        if bot.bot is None:
            print("TELEGRAM_BOT_TOKEN 이 필요합니다.")
            return 1
        await bot.start_telegram(polling=False)
        deal = sample_deal()
        if args.to == "admin":
            if not bot.notifier.enabled:
                print("TELEGRAM_ADMIN_CHAT_ID 가 필요합니다.")
                return 1
            bot.publisher.channel_id = bot.notifier.chat_id
            bot.publisher.dry_run = False
        result = await bot.publisher.publish(deal)
        print(result)
        return 0 if result.ok else 1
    finally:
        await bot.close()


def cmd_status(args: argparse.Namespace) -> int:
    from dealbot.app import DealBot

    s = _settings(args)
    bot = DealBot(s)
    try:
        text = bot.reporter.status_text()
        for tag in ("<b>", "</b>", "<code>", "</code>", "<i>", "</i>"):
            text = text.replace(tag, "")
        print(text)
    finally:
        bot.db.close()
    return 0


def cmd_healthcheck(args: argparse.Namespace) -> int:
    """Docker HEALTHCHECK 용: heartbeat 가 최근 N분 내면 0."""
    from dealbot.storage.db import Database

    s = load_settings(args.config)
    if not s.db_path.exists():
        print("db not found")
        return 1
    db = Database(s.db_path)
    try:
        hb = from_iso(db.kv_get("heartbeat"))
    finally:
        db.close()
    if hb is None:
        print("no heartbeat yet")
        return 1
    age = utcnow() - hb
    # 수집기 1회 실행이 길어질 수 있으므로(뽐뿌 상세 페이지 순회 등) 여유 있게 15분
    limit = timedelta(seconds=max(900, s.app.scheduler_tick_seconds * 10))
    print(f"heartbeat age {int(age.total_seconds())}s (limit {int(limit.total_seconds())}s)")
    return 0 if age <= limit else 1


# --------------------------------------------------------------------- main
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="dealbot", description="쿠팡파트너스 핫딜 자동 발행 봇")
    p.add_argument("--config", "-c", default=None, help="설정 파일 경로 (기본: $DEALBOT_CONFIG 또는 ./config.yaml)")
    p.add_argument("--no-log-file", action="store_true", help="파일 로그 비활성화 (stdout 만)")
    p.add_argument("--version", action="version", version=f"dealbot {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("run", help="24시간 상시 실행 (스케줄러 + 발행 워커 + 관리자 봇)")

    once = sub.add_parser("once", help="수집기 1회 실행 후 대기열 발행하고 종료")
    once.add_argument("--collector", action="append", help="특정 수집기만 (여러 번 지정 가능)")
    once.add_argument("--no-publish", action="store_true", help="수집/판정만 하고 발행은 하지 않음")
    once.add_argument("--dry-run", action="store_true", help="발행을 로그로만")

    sub.add_parser("check", help="설정 · API 키 · 텔레그램 연결 점검")
    sub.add_parser("chat-id", help="관리자 챗 ID / 채널 ID 확인 도우미")
    sub.add_parser("render", help="샘플 딜로 메시지 템플릿 미리보기 (전송 없음)")
    tp = sub.add_parser("test-post", help="샘플 딜을 실제 전송해 양식 확인")
    tp.add_argument("--to", choices=["channel", "admin"], default="admin", help="전송 대상 (기본: 관리자 챗)")
    sub.add_parser("status", help="현재 상태를 콘솔에 출력")
    sub.add_parser("healthcheck", help="컨테이너 헬스체크")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "run":
            return asyncio.run(cmd_run(args))
        if args.command == "once":
            return asyncio.run(cmd_once(args))
        if args.command == "check":
            return asyncio.run(cmd_check(args))
        if args.command == "chat-id":
            return asyncio.run(cmd_chat_id(args))
        if args.command == "render":
            return cmd_render(args)
        if args.command == "test-post":
            return asyncio.run(cmd_test_post(args))
        if args.command == "status":
            return cmd_status(args)
        if args.command == "healthcheck":
            return cmd_healthcheck(args)
    except KeyboardInterrupt:
        return 130
    return 2


if __name__ == "__main__":
    sys.exit(main())
