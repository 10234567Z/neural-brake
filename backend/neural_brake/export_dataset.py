"""Export only timestamped observations cached for the configured source."""

import argparse
import hashlib
import json
from pathlib import Path

from .config import Settings
from .schemas import SwapObservation
from .storage import Store


def export_snapshot(path: str, settings: Settings) -> dict[str, object]:
    store = Store(settings.database_path)
    with store.connect() as db:
        rows = db.execute(
            "SELECT payload FROM swaps WHERE chain_id = ? AND pool_address = ? ORDER BY block_number, log_index",
            (settings.chain_id, settings.pool_address.lower()),
        ).fetchall()
    records = [SwapObservation.model_validate_json(row[0]) for row in rows]
    if not records:
        raise ValueError("no cached observations for the configured chain and pool")
    if any(record.timestamp is None for record in records):
        raise ValueError("snapshot contains observations without RPC-verified timestamps")

    lines = []
    for record in records:
        row = record.model_dump(mode="json")
        row.update({
            "token0_address": settings.token0_address.lower(),
            "token1_address": settings.token1_address.lower(),
            "token0_decimals": settings.token0_decimals,
            "token1_decimals": settings.token1_decimals,
            "fee": settings.fee,
        })
        lines.append(json.dumps(row, sort_keys=True, separators=(",", ":")))
    content = ("\n".join(lines) + "\n").encode("utf-8")
    output = Path(path).expanduser()
    if output.suffix in {".db", ".sqlite", ".sqlite3"}:
        raise ValueError("snapshot output must be a JSONL file, not a database path")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + ".tmp")
    temporary.write_bytes(content)
    temporary.replace(output)
    return {
        "path": str(output),
        "observation_count": len(records),
        "chain_id": settings.chain_id,
        "pool_address": settings.pool_address,
        "first_timestamp": records[0].timestamp.isoformat(),
        "last_timestamp": records[-1].timestamp.isoformat(),
        "sha256": hashlib.sha256(content).hexdigest(),
        "source": "local RPC-observation cache; chain remains authoritative",
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Export cached real Swap observations to JSONL")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(json.dumps(export_snapshot(args.output, Settings.from_env()), indent=2))


if __name__ == "__main__":
    main()
