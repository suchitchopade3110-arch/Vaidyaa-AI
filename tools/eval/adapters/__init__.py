"""Adapter and scorer registries (``ADAPTERS``, ``SCORERS``; P1 plan 2.1, 6.2).

The only part of ``tools/eval`` allowed to import ``app`` lives in this package, and only lazily: each registry
entry is a factory that imports its module when called, so importing the registry (as ``run.py`` does) never
imports the product and the hermetic environment can be set first. Judge-side and GT-side modules never import
this package (GN-11); they use the result objects by duck typing (see ``base``).

P2 adds ``extracted_rows`` -> ``ExtractedRowsAdapter`` and ``confirmed_v1`` -> its scorer here. The oracle
``tools.eval.clinical.ClinicalRulesScorer`` is deliberately not registered: it is for metric unit tests and
M5-oracle only, never the active ``--scorer`` (plan 6.4).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

DEFAULT_ADAPTER = "pipeline_v0"
DEFAULT_SCORER = "engine_floor_v0"


def _pipeline_v0(**kwargs: Any) -> Any:
    from tools.eval.adapters.pipeline_v0 import PipelineV0Adapter

    return PipelineV0Adapter(**kwargs)


def _engine_floor_v0(**kwargs: Any) -> Any:
    from tools.eval.adapters.pipeline_v0 import EngineFloorScorerV0

    return EngineFloorScorerV0(**kwargs)


ADAPTERS: dict[str, Callable[..., Any]] = {"pipeline_v0": _pipeline_v0}
SCORERS: dict[str, Callable[..., Any]] = {"engine_floor_v0": _engine_floor_v0}


def get_adapter(name: str = DEFAULT_ADAPTER, **kwargs: Any) -> Any:
    try:
        factory = ADAPTERS[name]
    except KeyError:
        raise KeyError(f"unknown adapter {name!r}; known: {', '.join(sorted(ADAPTERS))}") from None
    return factory(**kwargs)


def get_scorer(name: str = DEFAULT_SCORER, **kwargs: Any) -> Any:
    try:
        factory = SCORERS[name]
    except KeyError:
        raise KeyError(f"unknown scorer {name!r}; known: {', '.join(sorted(SCORERS))}") from None
    return factory(**kwargs)


__all__ = ["ADAPTERS", "DEFAULT_ADAPTER", "DEFAULT_SCORER", "SCORERS", "get_adapter", "get_scorer"]
