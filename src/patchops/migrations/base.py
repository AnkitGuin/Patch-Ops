from pathlib import Path
from typing import Protocol

from ..models import Analysis, Plan, Request, Signal


class Plugin(Protocol):
    migration_id: str

    def inspect(self, path: str, source: str, request: Request) -> list[Signal]: ...

    def plan(self, analysis: Analysis, request: Request, checks: list[str]) -> Plan: ...

    def rewrite(self, path: str, source: str, request: Request) -> str: ...

    def permits(self, root: Path, path: str, plan: Plan) -> bool: ...
