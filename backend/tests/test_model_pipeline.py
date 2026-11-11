"""TEST-ONLY feature records; they are never product observations or model inputs."""

from datetime import datetime, timedelta, timezone
from dataclasses import replace
import json
import math

import pytest

from neural_brake.config import Settings
from neural_brake.export_dataset import export_snapshot
from neural_brake.ingest import SourceError
from neural_brake.model import build_samples, chronological_splits, load_dataset
from neural_brake.schemas import SwapObservation
from neural_brake.storage import Store


POOL = "0x46880b404CD35c165EDdefF7421019F8dD25F4Ad"
USDC = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
WETH = "0x4200000000000000000000000000000000000006"


def _record(index: int) -> SwapObservation:
    # This is a test-only fixture used to verify feature and split mechanics.
    q96 = 1 << 96
    sqrt_price = int(q96 * math.exp(math.sin(index / 5) * 0.001))
    return SwapObservation(
        chain_id=84532,
        pool_address=POOL.lower(),
        block_number=46_500_000 + index,
        block_hash="0x" + f"{index + 1:064x}",
        transaction_hash="0x" + f"{index + 1000:064x}",
        log_index=0,
        sender="0x" + "11" * 20,
        recipient="0x" + "22" * 20,
        timestamp=datetime(2026, 10, 1, tzinfo=timezone.utc) + timedelta(seconds=30 * index),
        amount0=str(-1 if index % 2 else 1),
        amount1=str(1 if index % 2 else -1),
        sqrt_price_x96=str(sqrt_price),
        liquidity="1000000000",
        tick=index % 7,
    )


def _settings() -> Settings:
    return Settings(
        rpc_url=None,
        database_path=":memory:",
        chain_id=84532,
        pool_address=POOL,
        token0_address=USDC,
        token1_address=WETH,
        token0_decimals=6,
        token1_decimals=18,
        fee=3000,
        model_artifact=None,
        registry_address=None,
        reactive_address=None,
        vault_address=None,
    )


def test_loads_only_matching_timestamped_provisional_pool_rows(tmp_path):
    records = [_record(i) for i in range(8)]
    path = tmp_path / "test-only.jsonl"
    path.write_text("\n".join(json.dumps(row.model_dump(mode="json") | {
        "token0_address": USDC,
        "token1_address": WETH,
        "token0_decimals": 6,
        "token1_decimals": 18,
        "fee": 3000,
    }) for row in records) + "\n", encoding="utf-8")

    loaded, digest, raw = load_dataset(str(path), _settings())
    assert len(loaded) == 8
    assert len(digest) == 64
    assert raw == path.read_bytes()

    wrong = json.loads(path.read_text().splitlines()[0])
    wrong["pool_address"] = "0x" + "33" * 20
    path.write_text(json.dumps(wrong), encoding="utf-8")
    with pytest.raises(SourceError, match="wrong source identity"):
        load_dataset(str(path), _settings())


def test_feature_window_and_chronological_labels_do_not_cross_split_boundaries():
    rows = [_record(i) for i in range(200)]
    samples = build_samples(rows, 6, 18, window=10, horizon=10)
    train, validation, test = chronological_splits(samples, len(rows))
    train_end = int(len(rows) * 0.70)
    validation_end = int(len(rows) * 0.85)

    assert len(samples[0].features) == 30
    assert max(sample.index + 10 for sample in train) < train_end
    assert min(sample.index for sample in validation) >= train_end
    assert max(sample.index + 10 for sample in validation) < validation_end
    assert min(sample.index for sample in test) >= validation_end
    assert all(math.isfinite(value) for sample in samples for value in sample.features)


def test_export_snapshot_filters_current_pool_and_emits_real_source_metadata(tmp_path):
    db_path = str(tmp_path / "test-only.sqlite3")
    store = Store(db_path)
    current = _record(1)
    other_pool = current.model_copy(update={"pool_address": "0x" + "33" * 20, "log_index": 1})
    store.add_swap(current)
    store.add_swap(other_pool)
    settings = replace(_settings(), database_path=db_path)
    output = tmp_path / "source.jsonl"

    report = export_snapshot(str(output), settings)
    rows = [json.loads(line) for line in output.read_text().splitlines()]
    assert report["observation_count"] == 1
    assert rows[0]["pool_address"] == POOL.lower()
    assert rows[0]["token0_address"] == USDC.lower()
    assert rows[0]["token1_decimals"] == 18
