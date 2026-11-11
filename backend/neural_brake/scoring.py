"""Explicit read-only inference for offline research artifacts."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
import math
import pickle
from pathlib import Path
import sys
from typing import Any, cast

from .config import Settings
from .ingest import SourceError
from .model import FEATURE_SCHEMA, SCORE_SCALE, _price_log, _weights_sha256, load_dataset
from .schemas import SwapObservation

DERIVED_MANIFEST_FIELDS = {"artifact_sha256", "manifest_sha256", "model_version"}


@dataclass(frozen=True)
class LoadedResearchModel:
    model: Any
    torch: Any
    feature_mean: tuple[float, ...]
    feature_scale: tuple[float, ...]
    window: int
    threshold: int
    model_version: str
    training_dataset_sha256: str
    test_precision: float | None


def _canonical_hash(value: dict[str, Any]) -> str:
    canonical = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(canonical).hexdigest()


def load_research_model(artifact_path: str, settings: Settings) -> LoadedResearchModel:
    try:
        import torch
    except ImportError as exc:
        raise SourceError("install the optional CPU PyTorch model dependency to score") from exc

    path = Path(artifact_path).expanduser()
    manifest_path = path.with_suffix(path.suffix + ".manifest.json")
    try:
        artifact_bytes = path.read_bytes()
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raise SourceError("model artifact or manifest could not be read") from None
    if not isinstance(manifest, dict):
        raise SourceError("model manifest is malformed")
    if hashlib.sha256(artifact_bytes).hexdigest() != manifest.get("artifact_sha256"):
        raise SourceError("model artifact checksum does not match its manifest")

    manifest_core = {key: value for key, value in manifest.items() if key not in DERIVED_MANIFEST_FIELDS}
    manifest_hash = _canonical_hash(manifest_core)
    if manifest_hash != manifest.get("manifest_sha256"):
        raise SourceError("model manifest checksum is invalid")
    weights_hash = manifest.get("weights_sha256")
    expected_version = hashlib.sha256(f"{weights_hash}{manifest_hash}".encode()).hexdigest()
    if expected_version != manifest.get("model_version"):
        raise SourceError("model version checksum is invalid")

    expected_source = {
        "chain_id": settings.chain_id,
        "pool_address": settings.pool_address.lower(),
        "token0_address": settings.token0_address.lower(),
        "token1_address": settings.token1_address.lower(),
        "token_decimals": [settings.token0_decimals, settings.token1_decimals],
        "feature_schema": FEATURE_SCHEMA,
        "score_scale": SCORE_SCALE,
    }
    if any(manifest.get(key) != value for key, value in expected_source.items()):
        raise SourceError("model manifest source or feature configuration does not match runtime")
    if manifest.get("holdout_gate") != "passed-pending-operator-review":
        raise SourceError("model did not pass the minimum chronological holdout gate")

    try:
        checkpoint = torch.load(path, map_location="cpu", weights_only=True)
        window = int(checkpoint["window"])
        threshold = int(checkpoint["score_threshold"])
        feature_mean = tuple(float(value) for value in checkpoint["feature_mean"])
        feature_scale = tuple(float(value) for value in checkpoint["feature_scale"])
        state_dict = checkpoint["state_dict"]
        if (
            checkpoint["feature_schema"] != FEATURE_SCHEMA
            or checkpoint["score_scale"] != SCORE_SCALE
            or manifest.get("feature_window_swaps") != window
            or manifest.get("label_horizon_swaps") != checkpoint["horizon"]
            or manifest.get("validated_score_threshold") != threshold
            or len(feature_mean) != window * 3
            or len(feature_scale) != window * 3
            or any(not math.isfinite(value) for value in feature_mean + feature_scale)
            or any(value <= 0 for value in feature_scale)
            or not 0 <= threshold <= SCORE_SCALE
        ):
            raise ValueError("incompatible model metadata")
        if not isinstance(state_dict, dict) or set(state_dict) != {"0.weight", "0.bias", "2.weight", "2.bias"}:
            raise ValueError("unexpected model weights")
        if _weights_sha256(state_dict, torch) != weights_hash:
            raise ValueError("weight checksum mismatch")
        input_features = window * 3
        hidden_units = int(state_dict["0.weight"].shape[0])
        if (
            state_dict["0.weight"].shape != (hidden_units, input_features)
            or state_dict["0.bias"].shape != (hidden_units,)
            or state_dict["2.weight"].shape != (1, hidden_units)
            or state_dict["2.bias"].shape != (1,)
        ):
            raise ValueError("unexpected model weight dimensions")
        model = torch.nn.Sequential(
            torch.nn.Linear(input_features, hidden_units),
            torch.nn.Tanh(),
            torch.nn.Linear(hidden_units, 1),
        )
        model.load_state_dict(state_dict, strict=True)
        model.eval()
    except (KeyError, TypeError, ValueError, RuntimeError, IndexError, OSError, pickle.UnpicklingError) as exc:
        raise SourceError("model artifact failed validation") from exc

    test_metrics = manifest.get("test_metrics")
    precision = test_metrics.get("precision") if isinstance(test_metrics, dict) else None
    if precision is not None and (
        not isinstance(precision, (int, float)) or not math.isfinite(precision) or not 0 <= precision <= 1
    ):
        raise SourceError("model manifest has invalid holdout precision")
    test_precision = float(precision) if precision is not None else None
    return LoadedResearchModel(
        model=model,
        torch=torch,
        feature_mean=feature_mean,
        feature_scale=feature_scale,
        window=window,
        threshold=threshold,
        model_version=manifest["model_version"],
        training_dataset_sha256=manifest["dataset_sha256"],
        test_precision=test_precision,
    )


def latest_features(records: list[SwapObservation], settings: Settings, window: int) -> list[float]:
    if len(records) < window + 1:
        raise SourceError("too few timestamped swaps for the model feature window")
    context = records[-(window + 1):]
    timestamps: list[float] = []
    prices: list[float] = []
    for record in context:
        if record.timestamp is None or record.timestamp.tzinfo is None or record.timestamp.utcoffset() is None:
            raise SourceError("model scoring requires timezone-aware source timestamps")
        try:
            timestamps.append(record.timestamp.timestamp())
            prices.append(_price_log(record, settings.token0_decimals, settings.token1_decimals))
        except (OverflowError, TypeError, ValueError):
            raise SourceError("source observation has invalid price or timestamp for scoring") from None

    features = []
    for index in range(1, len(context)):
        delta = timestamps[index] - timestamps[index - 1]
        if delta < 0:
            raise SourceError("source observations are not chronological")
        try:
            amount0 = int(context[index].amount0)
        except (TypeError, ValueError):
            raise SourceError("source observation has invalid amount data for scoring") from None
        direction = 1.0 if amount0 > 0 else -1.0 if amount0 < 0 else 0.0
        features.extend((prices[index] - prices[index - 1], math.log1p(delta), direction))
    return features


def score_with_loaded_model(
    records: list[SwapObservation],
    settings: Settings,
    model: LoadedResearchModel,
) -> dict[str, Any]:
    ordered = sorted(records, key=lambda item: (item.block_number, item.log_index))
    features = latest_features(ordered, settings, model.window)
    normalized = [
        (value - mean) / scale
        for value, mean, scale in zip(features, model.feature_mean, model.feature_scale, strict=True)
    ]
    with model.torch.no_grad():
        logit = float(model.model(model.torch.tensor([normalized], dtype=model.torch.float32)).reshape(-1)[0])
        raw_score = float(model.torch.sigmoid(model.torch.tensor(logit)))
    score = max(0, min(SCORE_SCALE, int(round(raw_score * SCORE_SCALE))))
    latest = ordered[-1]
    identity = f"{model.model_version}:{latest.chain_id}:{latest.pool_address}:{latest.block_number}:{latest.transaction_hash}:{latest.log_index}"
    warning = "Research-only signal; not a probability and not approved to pause deposits."
    if model.test_precision is not None:
        warning += f" Prior chronological holdout precision: {model.test_precision:.1%}."
    return {
        "status": "research_only",
        "automatic_action_enabled": False,
        "candidate_id": hashlib.sha256(identity.encode()).hexdigest(),
        "model_version": model.model_version,
        "training_dataset_sha256": model.training_dataset_sha256,
        "chain_id": latest.chain_id,
        "pool_address": latest.pool_address,
        "as_of": cast(datetime, latest.timestamp).isoformat(),
        "block_number": latest.block_number,
        "transaction_hash": latest.transaction_hash,
        "log_index": latest.log_index,
        "risk_score": score,
        "score_scale": SCORE_SCALE,
        "threshold": model.threshold,
        "high_volatility_candidate": score >= model.threshold,
        "warning": warning,
    }


def score_records(
    records: list[SwapObservation],
    settings: Settings,
    artifact_path: str,
) -> dict[str, Any]:
    return score_with_loaded_model(records, settings, load_research_model(artifact_path, settings))


def score_dataset(dataset_path: str, artifact_path: str, settings: Settings) -> dict[str, Any]:
    records, _, _ = load_dataset(dataset_path, settings)
    return score_records(records, settings, artifact_path)


def main() -> None:
    parser = argparse.ArgumentParser(description="Score a verified swap snapshot with a research-only model")
    parser.add_argument("--dataset", required=True, help="JSONL snapshot of receipt-verified Swap observations")
    parser.add_argument("--artifact", required=True, help="trained model weights with companion manifest")
    args = parser.parse_args()
    try:
        result = score_dataset(args.dataset, args.artifact, Settings.from_env())
    except SourceError as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}), file=sys.stderr)
        raise SystemExit(2) from exc
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
