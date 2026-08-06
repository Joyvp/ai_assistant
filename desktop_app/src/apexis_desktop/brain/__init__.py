"""Replaceable Brain Provider interfaces and implementations."""

from apexis_desktop.brain.base import BrainProvider
from apexis_desktop.brain.mock import MockProvider

__all__ = ["BrainProvider", "MockProvider"]
