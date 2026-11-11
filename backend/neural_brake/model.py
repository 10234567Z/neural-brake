"""Small reproducible PyTorch risk-signal pipeline for receipt-verified Swap data.

Scores are bounded model signals, not probabilities or proofs of honest inference.
Training is an explicit offline command and never publishes a score on-chain.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import statistics
import sys
from typing import Any, cast
from urllib.parse import urlsplit

from .config import Settings
from .ingest import SourceError
from .schemas import SwapObservation

SCORE_SCALE = 10_000
FEATURE_SCHEMA = "usdc-weth-swap-window-v1"


@dataclass(frozen=True)
class Sample:
    index: int
    timestamp: datetime
    features: tuple[float, ...]
    future_volatility: float


def load_dataset(path: str, settings: Settings) -> tuple[list[SwapObservation], str, bytes]:
    source = Path(path).read_bytes()
    records: list[SwapObservation] = []
    seen: set[tuple[int, str, int]] = set()
    fields = set(SwapObservation.model_fields)
    allowed_extra = {"token0_address", "token1_address", "token0_decimals", "token1_decimals", "fee"}
    for line_number, line in enumerate(source.splitlines(), 1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
            if not isinstance(raw, dict) or set(raw) - fields - allowed_extra:
                raise ValueError("unexpected fields")
            if raw.get("chain_id") != settings.chain_id or raw.get("pool_address", "").lower() != settings.pool_address.lower():
                raise ValueError("wrong source identity")
            for key, expected in (
                ("token0_address", settings.token0_address),
                ("token1_address", settings.token1_address),
                ("token0_decimals", settings.token0_decimals),
                ("token1_decimals", settings.token1_decimals),
                ("fee", settings.fee),
            ):
                if key in raw and str(raw[key]).lower() != str(expected).lower():
                    raise ValueError(f"{key} does not match configured source")
            record = SwapObservation.model_validate({key: value for key, value in raw.items() if key in fields})
            timestamp = record.timestamp
            if timestamp is None or timestamp.tzinfo is None or timestamp.utcoffset() is None:
                raise ValueError("timestamp missing or timezone-naive")
            record = record.model_copy(update={"timestamp": timestamp.astimezone(timezone.utc)})
            key = (record.block_number, record.transaction_hash.lower(), record.log_index)
            if key in seen:
                raise ValueError("duplicate chain log identity")
            seen.add(key)
            records.append(record)
        except (json.JSONDecodeError, ValueError, TypeError) as exc:
            raise SourceError(f"invalid dataset row {line_number}: {exc}") from exc

    records.sort(key=lambda row: (row.block_number, row.log_index))
    if len(records) < 3:
        raise SourceError("too few real Swap observations to build chronological features")
    last_timestamp: datetime | None = None
    for row in records:
        timestamp = cast(datetime, row.timestamp)
        if last_timestamp is not None and timestamp < last_timestamp:
            raise SourceError("source timestamps are not chronological")
        last_timestamp = timestamp
        sqrt_price = int(row.sqrt_price_x96)
        if sqrt_price <= 0 or sqrt_price >= (1 << 160):
            raise SourceError("invalid sqrtPriceX96 in source data")
    return records, hashlib.sha256(source).hexdigest(), source


def _price_log(record: SwapObservation, decimals0: int, decimals1: int) -> float:
    sqrt_price = int(record.sqrt_price_x96)
    # Uniswap v3 reports sqrt(raw token1 / raw token0) in Q64.96 units.
    return (
        2.0 * (math.log(sqrt_price) - 96.0 * math.log(2.0))
        + (decimals0 - decimals1) * math.log(10.0)
    )


def build_samples(
    records: list[SwapObservation], decimals0: int, decimals1: int, window: int = 10, horizon: int = 10
) -> list[Sample]:
    if window < 2 or horizon < 2 or len(records) <= window + horizon + 2:
        raise SourceError("insufficient observations for the configured feature and label windows")
    prices = [_price_log(row, decimals0, decimals1) for row in records]
    timestamps = [cast(datetime, row.timestamp).timestamp() for row in records]
    returns = [0.0] + [prices[index] - prices[index - 1] for index in range(1, len(records))]
    elapsed = [0.0] + [max(0.0, timestamps[index] - timestamps[index - 1]) for index in range(1, len(records))]
    direction = [0.0] + [
        1.0 if int(records[index].amount0) > 0 else -1.0 if int(records[index].amount0) < 0 else 0.0
        for index in range(1, len(records))
    ]

    samples: list[Sample] = []
    for index in range(window, len(records) - horizon):
        feature_indices = range(index - window + 1, index + 1)
        features: list[float] = []
        for position in feature_indices:
            features.extend((returns[position], math.log1p(elapsed[position]), direction[position]))
        future = returns[index + 1:index + horizon + 1]
        volatility = statistics.pstdev(future)
        samples.append(Sample(index=index, timestamp=cast(datetime, records[index].timestamp), features=tuple(features), future_volatility=volatility))
    return samples


def chronological_splits(samples: list[Sample], record_count: int) -> tuple[list[Sample], list[Sample], list[Sample]]:
    train_end = int(record_count * 0.70)
    validation_end = int(record_count * 0.85)
    # Infer the configured horizon from the last sample index; samples end at N-horizon-1.
    horizon = record_count - 1 - samples[-1].index
    train = [sample for sample in samples if sample.index + horizon < train_end]
    validation = [sample for sample in samples if train_end <= sample.index and sample.index + horizon < validation_end]
    test = [sample for sample in samples if validation_end <= sample.index and sample.index + horizon < record_count]
    if not train or not validation or not test:
        raise SourceError("chronological split produced an empty train, validation, or test partition")
    return train, validation, test


def _quantile(values: list[float], quantile: float) -> float:
    if not values or not 0 < quantile < 1:
        raise SourceError("invalid label cutoff inputs")
    ordered = sorted(values)
    return ordered[math.ceil(quantile * len(ordered)) - 1]


def _labels(samples: list[Sample], cutoff: float) -> list[int]:
    return [1 if sample.future_volatility >= cutoff else 0 for sample in samples]


def _normalize(train_x: list[list[float]], other_x: list[list[float]]) -> tuple[list[list[float]], list[list[float]], list[float], list[float]]:
    columns = len(train_x[0])
    means = [statistics.fmean(row[column] for row in train_x) for column in range(columns)]
    scales = []
    for column in range(columns):
        scale = statistics.pstdev(row[column] for row in train_x)
        scales.append(scale if scale > 1e-12 else 1.0)

    def apply(rows: list[list[float]]) -> list[list[float]]:
        return [[(row[column] - means[column]) / scales[column] for column in range(columns)] for row in rows]

    return apply(train_x), apply(other_x), means, scales


def _metrics(labels: list[int], predictions: list[int]) -> dict[str, float | int]:
    tp = sum(y == 1 and p == 1 for y, p in zip(labels, predictions, strict=True))
    tn = sum(y == 0 and p == 0 for y, p in zip(labels, predictions, strict=True))
    fp = sum(y == 0 and p == 1 for y, p in zip(labels, predictions, strict=True))
    fn = sum(y == 1 and p == 0 for y, p in zip(labels, predictions, strict=True))
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    tnr = tn / (tn + fp) if tn + fp else 0.0
    return {
        "observations": len(labels),
        "positives": sum(labels),
        "true_positive": tp,
        "true_negative": tn,
        "false_positive": fp,
        "false_negative": fn,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "balanced_accuracy": (recall + tnr) / 2.0,
    }


def _weights_sha256(state_dict: dict[str, Any], torch: Any) -> str:
    digest = hashlib.sha256()
    for name, tensor in sorted(state_dict.items()):
        contiguous = tensor.detach().cpu().contiguous()
        digest.update(name.encode("utf-8") + b"\0")
        digest.update(str(contiguous.dtype).encode("ascii") + b"\0")
        digest.update(json.dumps(list(contiguous.shape), separators=(",", ":")).encode("ascii") + b"\0")
        digest.update(bytes(contiguous.view(torch.uint8).reshape(-1).tolist()))
    return digest.hexdigest()


def train_artifact(
    dataset_path: str,
    output_path: str,
    window: int = 10,
    horizon: int = 10,
    seed: int = 7,
    epochs: int = 120,
    allow_research_artifact: bool = False,
) -> dict[str, Any]:
    try:
        import torch
    except ImportError as exc:
        raise SourceError("install the optional CPU PyTorch model dependency to train") from exc

    settings = Settings.from_env()
    if (settings.token0_address.lower(), settings.token1_address.lower()) != (
        "0x036cbd53842c5426634e7929541ec2318f3dcf7e",
        "0x4200000000000000000000000000000000000006",
    ):
        raise SourceError("model feature decimal semantics are only verified for the configured USDC/WETH pair")
    if (settings.token0_decimals, settings.token1_decimals) != (6, 18):
        raise SourceError("verified USDC/WETH decimals must be 6 and 18")

    records, dataset_hash, _ = load_dataset(dataset_path, settings)
    samples = build_samples(records, settings.token0_decimals, settings.token1_decimals, window, horizon)
    train, validation, test = chronological_splits(samples, len(records))
    volatility_cutoff = _quantile([sample.future_volatility for sample in train], 0.90)
    train_y = _labels(train, volatility_cutoff)
    validation_y = _labels(validation, volatility_cutoff)
    test_y = _labels(test, volatility_cutoff)
    if len(set(train_y)) != 2 or len(set(validation_y)) != 2 or len(set(test_y)) != 2:
        raise SourceError("at least one chronological split lacks both volatility classes; refusing to train")

    torch.set_num_threads(1)
    torch.manual_seed(seed)
    torch.use_deterministic_algorithms(True)
    train_x, validation_and_test, means, scales = _normalize(
        [list(sample.features) for sample in train],
        [list(sample.features) for sample in validation + test],
    )
    validation_x = validation_and_test[:len(validation)]
    test_x = validation_and_test[len(validation):]
    model = torch.nn.Sequential(
        torch.nn.Linear(len(train_x[0]), 16),
        torch.nn.Tanh(),
        torch.nn.Linear(16, 1),
    )
    positive = sum(train_y)
    negative = len(train_y) - positive
    loss_fn = torch.nn.BCEWithLogitsLoss(pos_weight=torch.tensor([negative / positive], dtype=torch.float32))
    optimizer = torch.optim.Adam(model.parameters(), lr=0.01)
    features_tensor = torch.tensor(train_x, dtype=torch.float32)
    labels_tensor = torch.tensor(train_y, dtype=torch.float32).reshape(-1, 1)
    model.train()
    for _ in range(epochs):
        optimizer.zero_grad(set_to_none=True)
        loss = loss_fn(model(features_tensor), labels_tensor)
        loss.backward()
        optimizer.step()
    model.eval()

    def scores(rows: list[list[float]]) -> list[float]:
        with torch.no_grad():
            logits = model(torch.tensor(rows, dtype=torch.float32)).reshape(-1)
            return torch.sigmoid(logits).tolist()

    validation_scores = scores(validation_x)
    candidates = sorted({0.0, 1.0, *validation_scores})
    best_threshold, best_f1 = 0.5, -1.0
    for threshold in candidates:
        metric = _metrics(validation_y, [int(score >= threshold) for score in validation_scores])
        if metric["f1"] > best_f1 or (metric["f1"] == best_f1 and threshold > best_threshold):
            best_threshold, best_f1 = threshold, float(metric["f1"])
    test_scores = scores(test_x)
    test_metrics = _metrics(test_y, [int(score >= best_threshold) for score in test_scores])
    validation_metrics = _metrics(validation_y, [int(score >= best_threshold) for score in validation_scores])
    holdout_pass = test_metrics["true_positive"] > 0 and test_metrics["balanced_accuracy"] > 0.5
    if not holdout_pass and not allow_research_artifact:
        raise SourceError(
            "chronological holdout failed the minimum research gate; no model artifact was written. "
            "Use --allow-research-artifact only to save a clearly marked offline experiment."
        )
    score_threshold = max(0, min(SCORE_SCALE, int(round(best_threshold * SCORE_SCALE))))
    state_dict = model.state_dict()
    weights_hash = _weights_sha256(state_dict, torch)

    artifact_path = Path(output_path)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = artifact_path.with_suffix(artifact_path.suffix + ".tmp")
    torch.save({
        "state_dict": state_dict,
        "feature_mean": means,
        "feature_scale": scales,
        "window": window,
        "horizon": horizon,
        "score_threshold": score_threshold,
        "score_scale": SCORE_SCALE,
        "feature_schema": FEATURE_SCHEMA,
    }, temporary_path)
    temporary_path.replace(artifact_path)
    artifact_hash = hashlib.sha256(artifact_path.read_bytes()).hexdigest()
    manifest_core: dict[str, Any] = {
        "weights_sha256": weights_hash,
        "dataset_sha256": dataset_hash,
        "source_rpc_host": urlsplit(settings.rpc_url).hostname if settings.rpc_url else None,
        "chain_id": settings.chain_id,
        "pool_address": settings.pool_address.lower(),
        "token0_address": settings.token0_address.lower(),
        "token1_address": settings.token1_address.lower(),
        "token_decimals": [settings.token0_decimals, settings.token1_decimals],
        "feature_schema": FEATURE_SCHEMA,
        "feature_window_swaps": window,
        "label_horizon_swaps": horizon,
        "label_definition": "future population stddev of log returns >= training 90th percentile",
        "training_label_cutoff_log_return_volatility": volatility_cutoff,
        "chronological_split": {"train": 0.70, "validation": 0.15, "test": 0.15},
        "score_scale": SCORE_SCALE,
        "validated_score_threshold": score_threshold,
        "validation_metrics": validation_metrics,
        "test_metrics": test_metrics,
        "holdout_gate": "passed-pending-operator-review" if holdout_pass else "failed-research-only",
        "observation_count": len(records),
        "first_observation": cast(datetime, records[0].timestamp).isoformat(),
        "last_observation": cast(datetime, records[-1].timestamp).isoformat(),
        "train_samples": len(train),
        "validation_samples": len(validation),
        "test_samples": len(test),
        "random_seed": seed,
        "epochs": epochs,
        "torch_version": torch.__version__,
        "limitations": [
            "score is a model signal, not a probability or proof of inference",
            "dataset is the supplied RPC-verified snapshot; provider retention may omit older history",
            "metrics are one chronological holdout on testnet pool data and do not establish production utility",
        ],
    }
    canonical = json.dumps(manifest_core, sort_keys=True, separators=(",", ":")).encode()
    manifest_hash = hashlib.sha256(canonical).hexdigest()
    model_version = hashlib.sha256((weights_hash + manifest_hash).encode()).hexdigest()
    manifest = {
        **manifest_core,
        "artifact_sha256": artifact_hash,
        "manifest_sha256": manifest_hash,
        "model_version": model_version,
    }
    manifest_path = artifact_path.with_suffix(artifact_path.suffix + ".manifest.json")
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"artifact": str(artifact_path), "manifest": str(manifest_path), "model_version": manifest["model_version"],
            "threshold": score_threshold, "validation_metrics": validation_metrics, "test_metrics": test_metrics,
            "observations": len(records), "samples": {"train": len(train), "validation": len(validation), "test": len(test)}}


def main() -> None:
    parser = argparse.ArgumentParser(description="Train an offline risk model only from a verified real Swap snapshot")
    parser.add_argument("--dataset", required=True, help="JSONL output from a real RPC history audit")
    parser.add_argument("--output", required=True, help="path for model weights; manifest is written beside it")
    parser.add_argument("--window", type=int, default=10)
    parser.add_argument("--horizon", type=int, default=10)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--epochs", type=int, default=120)
    parser.add_argument("--allow-research-artifact", action="store_true",
                        help="allow a holdout-failed artifact for offline research only; never for publication")
    args = parser.parse_args()
    if not 2 <= args.window <= 100 or not 2 <= args.horizon <= 100 or not 1 <= args.epochs <= 2000:
        parser.error("window/horizon must be 2..100 and epochs 1..2000")
    try:
        result = train_artifact(args.dataset, args.output, args.window, args.horizon, args.seed, args.epochs,
                                args.allow_research_artifact)
    except SourceError as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}), file=sys.stderr)
        raise SystemExit(2) from exc
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
