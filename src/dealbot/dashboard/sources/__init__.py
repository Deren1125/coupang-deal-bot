"""대시보드 소스 모듈들. 이 폴더의 모듈은 모두 자동으로 읽혀 `@register` 된 종류가 SOURCE_TYPES 에 들어간다."""

from __future__ import annotations

import importlib
import pkgutil

from dealbot.dashboard.sources.base import SOURCE_TYPES, CardData, Source, SourceContext, register

__all__ = ["SOURCE_TYPES", "CardData", "Source", "SourceContext", "load_all", "register"]


def load_all() -> dict[str, type[Source]]:
    """폴더 안의 모듈을 모두 import 해서 등록된 종류를 돌려준다 (새 모듈을 넣기만 하면 됨)."""
    for mod in pkgutil.iter_modules(__path__):
        if mod.name != "base" and not mod.name.startswith("_"):
            importlib.import_module(f"{__name__}.{mod.name}")
    return dict(SOURCE_TYPES)
