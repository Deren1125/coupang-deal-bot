"""수집기 레지스트리 (플러그인 로딩)."""

from __future__ import annotations

import importlib
from collections.abc import Callable

from dealbot.collectors.base import BaseCollector, CollectorContext
from dealbot.config import CollectorConfig

_REGISTRY: dict[str, type[BaseCollector]] = {}


def register(type_name: str) -> Callable[[type[BaseCollector]], type[BaseCollector]]:
    def deco(cls: type[BaseCollector]) -> type[BaseCollector]:
        _REGISTRY[type_name] = cls
        return cls

    return deco


def available_types() -> list[str]:
    _ensure_builtins()
    return sorted(_REGISTRY)


def _ensure_builtins() -> None:
    # 내장 수집기 모듈을 import 해서 데코레이터가 실행되게 함
    for mod in ("coupang_goldbox", "coupang_category_best", "ppomppu", "adpick_hotdeal", "algumon"):
        importlib.import_module(f"dealbot.collectors.{mod}")


def resolve_type(type_name: str) -> type[BaseCollector]:
    _ensure_builtins()
    if type_name in _REGISTRY:
        return _REGISTRY[type_name]
    if ":" in type_name:
        mod_name, cls_name = type_name.split(":", 1)
        module = importlib.import_module(mod_name)
        cls = getattr(module, cls_name)
        if not (isinstance(cls, type) and issubclass(cls, BaseCollector)):
            raise TypeError(f"{type_name} is not a BaseCollector subclass")
        return cls
    raise KeyError(f"unknown collector type '{type_name}'. available: {available_types()}")


def build_collector(cfg: CollectorConfig, ctx: CollectorContext) -> BaseCollector:
    cls = resolve_type(cfg.type)
    return cls(cfg.name, cfg.options, ctx)
