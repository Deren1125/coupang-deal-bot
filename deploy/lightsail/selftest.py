"""자동 업데이트(auto-update.sh)가 재시작 전에 돌리는 점검 — 새 코드로 설정을 읽고 템플릿을 그려 본다.

    .venv/bin/python deploy/lightsail/selftest.py

- 봇 코드 전체를 import 한다 (문법 오류·없는 모듈).
- config.yaml 을 실제로 읽는다 (설정 오류면 재시작하자마자 죽으니 여기서 걸러냄).
- 모든 템플릿을 문법 검사하고, 딜 글 템플릿(deal_*.j2 + 채널 글 템플릿)은 샘플 딜로 끝까지 그려 본다.
  (템플릿과 코드가 안 맞으면 import 는 되는데 발행할 때만 터지기 때문)
- DB·네트워크·텔레그램은 건드리지 않는다 (돌고 있는 봇과 같은 DB 를 새 코드로 열지 않게).
실패하면 0 이 아닌 값으로 끝나고 마지막 줄에 이유 한 줄을 남긴다 (auto-update.sh 가 알림에 붙임). 설정값은 출력하지 않는다.
"""

from __future__ import annotations

import logging
import sys


class CheckFailed(Exception):
    """이유가 이미 한 줄로 정리된 실패."""


def _reason(e: BaseException) -> str:
    """알림에 붙일 한 줄. pydantic 오류는 입력값 없이 '위치: 이유' 만."""
    if isinstance(e, CheckFailed):
        return str(e)[:300]
    errors = getattr(e, "errors", None)
    if callable(errors):
        try:
            items = errors(include_input=False)
            return "설정 오류 — " + "; ".join(
                f"{'.'.join(str(x) for x in it.get('loc', ()))}: {it.get('msg', '')}" for it in items[:3]
            )
        except Exception:  # noqa: BLE001
            pass
    first = (str(e).strip().splitlines() or [""])[0]
    return f"{type(e).__name__}: {first}"[:300]


def run() -> list[str]:
    """점검한 템플릿 이름 목록. 문제가 있으면 예외."""
    import dealbot.scheduler  # noqa: F401  — 봇이 시작할 때 읽는 모듈 전부 (app·admin·발행기)
    from dealbot.cli import sample_deal
    from dealbot.config import load_settings
    from dealbot.publisher.templates import TemplateRenderer

    s = load_settings()
    r = TemplateRenderer(s.templates_dir, s.app.timezone, s.channels.as_dict())
    names = sorted(p.name for p in s.templates_dir.glob("*.j2"))
    if not names:
        raise CheckFailed(f"템플릿이 없음: {s.templates_dir}")
    for env in (r.env, r.env_plain):  # 문법 (필터 이름까지) 확인
        for name in names:
            env.get_template(name)

    deal = sample_deal()
    link = deal.affiliate_url or deal.product.url
    shop = s.shop_registry().get(deal.product.shop)
    # 채널 글은 텔레그램 HTML(이스케이프), 나머지 딜 글(스레드·인스타·카카오·블로그)은 평문
    plans = {s.publish.template: True}
    for name in names:
        if name.startswith("deal_"):
            plans.setdefault(name, False)
    for name, autoescape in plans.items():
        try:
            text = r.render_deal(deal, link, shop=shop, template=name, autoescape=autoescape)
        except Exception as e:
            raise CheckFailed(f"{name}: {_reason(e)}") from e
        if not text.strip():
            raise CheckFailed(f"{name}: 샘플 딜로 그렸더니 빈 글")
    return names


def main() -> int:
    logging.disable(logging.CRITICAL)  # 설정을 읽다 남기는 경고(환경변수 값 등)는 알림에 섞이지 않게
    try:
        names = run()
    except Exception as e:  # noqa: BLE001
        print(_reason(e), file=sys.stderr)
        return 1
    print(f"점검 통과: 설정 + 템플릿 {len(names)}개")
    return 0


if __name__ == "__main__":
    sys.exit(main())
