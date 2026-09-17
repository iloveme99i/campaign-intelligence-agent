"""Server-owned snapshot locations; clients and models never supply a path."""

import re
from pathlib import Path

from analytics_agent.config import get_config_dir


def snapshot_directory() -> Path:
    return get_config_dir() / "merchant_snapshots"


def snapshot_path(snapshot_id: str) -> Path:
    if not re.fullmatch(r"[a-f0-9]{64}", snapshot_id):
        raise ValueError("Invalid snapshot id")
    return snapshot_directory() / f"{snapshot_id}.db"


def create_snapshot_engine(config: dict):
    from analytics_agent.merchant.engine import MerchantQueryEngine

    return MerchantQueryEngine(
        snapshot_path(config["snapshot_id"]),
        synthetic=bool(config.get("synthetic", False)),
    )
