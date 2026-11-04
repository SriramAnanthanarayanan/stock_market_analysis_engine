"""Volatility Contraction Pattern detector (docs/vcp-methodology.md)."""

from .detector import STATUS_LABELS, detect_vcp

__all__ = ["STATUS_LABELS", "detect_vcp"]
