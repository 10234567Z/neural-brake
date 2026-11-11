"""Small local cache of validated source observations; chain remains authoritative."""

import json
import sqlite3
from collections.abc import Iterable
from pathlib import Path

from .schemas import SwapObservation


class Store:
    def __init__(self, path: str):
        self.path = path
        if path != ":memory:":
            Path(path).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.execute("""CREATE TABLE IF NOT EXISTS swaps (
                chain_id INTEGER NOT NULL, pool_address TEXT NOT NULL,
                block_number INTEGER NOT NULL, block_hash TEXT NOT NULL,
                transaction_hash TEXT NOT NULL, log_index INTEGER NOT NULL,
                timestamp TEXT, payload TEXT NOT NULL,
                PRIMARY KEY(chain_id, pool_address, transaction_hash, log_index)
            )""")

    def connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        db.row_factory = sqlite3.Row
        return db

    def add_swap(self, observation: SwapObservation) -> None:
        self.add_swaps([observation])

    def add_swaps(self, observations: Iterable[SwapObservation]) -> None:
        rows = [(
            observation.chain_id,
            observation.pool_address.lower(),
            observation.block_number,
            observation.block_hash,
            observation.transaction_hash,
            observation.log_index,
            observation.timestamp.isoformat() if observation.timestamp else None,
            observation.model_dump_json(),
        ) for observation in observations]
        with self.connect() as db:
            db.executemany("INSERT OR IGNORE INTO swaps VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)

    def list_swaps(self, offset: int, limit: int) -> tuple[list[SwapObservation], int]:
        with self.connect() as db:
            rows = db.execute("SELECT payload FROM swaps ORDER BY block_number DESC, log_index DESC LIMIT ? OFFSET ?", (limit, offset)).fetchall()
            count = db.execute("SELECT COUNT(*) FROM swaps").fetchone()[0]
        return [SwapObservation.model_validate_json(row[0]) for row in rows], count

    def audit(self) -> tuple[int, str | None, str | None]:
        with self.connect() as db:
            row = db.execute("SELECT COUNT(*), MIN(timestamp), MAX(timestamp) FROM swaps").fetchone()
        return row[0], row[1], row[2]
