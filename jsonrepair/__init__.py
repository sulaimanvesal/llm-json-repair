"""llm-json-repair: repair malformed JSON produced by LLMs."""

from .repair import RepairReport, loads_repair, repair_json

__all__ = ["RepairReport", "loads_repair", "repair_json"]
__version__ = "0.1.0"
