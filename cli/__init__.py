"""CLI 適配器模組封裝。"""

from cli.adapters.base import BaseCLIAdapter
from cli.factory import (
    get_adapter,
    get_models_map,
    switch_adapter,
    switch_effort,
    switch_model,
)

__all__ = [
    "BaseCLIAdapter",
    "get_adapter",
    "get_models_map",
    "switch_adapter",
    "switch_effort",
    "switch_model",
]
