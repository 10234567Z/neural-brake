"""Opt-in, receipt-checked research observer; no signing or on-chain writes."""

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
from typing import NotRequired, TypedDict

import httpx

from .config import Settings
from .ingest import RpcSource, SourceError, _ingest

RPC_RANGE = 10  # Configured free-tier RPC rejects wider eth_getLogs queries.
PAGE_DELAY = 0.8


class WatchResult(TypedDict):
    status: str
    next_from_block: int
    head_block: int
    confirmed_through_block: int
    swaps_verified: int
    from_block: NotRequired[int]
    through_block: NotRequired[int]
    limitations: NotRequired[str]


async def watch_once(
    settings: Settings,
    from_block: int,
    to_block: int | None = None,
    max_blocks: int = 100,
    confirmations: int = 64,
) -> WatchResult:
    if settings.chain_id != 84532 or not settings.rpc_url:
        raise SourceError("observer requires a Base Sepolia RPC")
    if from_block < 0 or (to_block is not None and to_block < from_block):
        raise SourceError("invalid observation block range")
    if max_blocks < 1 or max_blocks > 2001 or confirmations < 64:
        raise SourceError("observer must scan 1..2001 blocks with at least 64 confirmation blocks")
    head = await RpcSource(settings).get_head()
    finalized = head - confirmations
    if to_block is not None and to_block > finalized:
        raise SourceError("requested end block is not past the configured confirmation depth")
    end = min(finalized, from_block + max_blocks - 1, to_block if to_block is not None else finalized)
    if end < from_block:
        return {"status": "waiting_for_confirmations", "next_from_block": from_block,
                "head_block": head, "confirmed_through_block": finalized, "swaps_verified": 0}

    swaps_verified = 0
    for start in range(from_block, end + 1, RPC_RANGE):
        stop = min(start + RPC_RANGE - 1, end)
        swaps_verified += await _ingest(start, stop, settings)
        if stop < end:
            await asyncio.sleep(PAGE_DELAY)

    return {"status": "research_observation_scanned", "from_block": from_block, "through_block": end,
            "next_from_block": end + 1, "head_block": head, "confirmed_through_block": finalized,
            "swaps_verified": swaps_verified,
            "limitations": "Per-range RPC logs and receipts checked; RPC completeness, finality, live scoring, publication, and callback not established"}


async def watch(settings: Settings, from_block: int, to_block: int | None,
                max_blocks: int, confirmations: int, poll_seconds: float, follow: bool) -> None:
    cursor = from_block
    while True:
        result = await watch_once(settings, cursor, to_block, max_blocks, confirmations)
        print(json.dumps(result, sort_keys=True), flush=True)
        cursor = result["next_from_block"]
        if not follow or (to_block is not None and cursor > to_block):
            return
        await asyncio.sleep(poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Monitor confirmed Base Sepolia Swap ranges, read-only")
    parser.add_argument("--from-block", type=int, required=True, help="explicit starting block, including on restart")
    parser.add_argument("--to-block", type=int, help="last block for a single bounded scan")
    parser.add_argument("--max-blocks", type=int, default=100, help="maximum blocks per scan cycle (1..2001)")
    parser.add_argument("--confirmations", type=int, default=64, help="minimum 64-block safety lag")
    parser.add_argument("--poll-seconds", type=float, default=20, help="follow-mode idle wait in seconds")
    parser.add_argument("--follow", action="store_true", help="keep scanning; restart from the last printed next_from_block")
    args = parser.parse_args()
    if args.follow and args.to_block is not None:
        parser.error("--follow and --to-block cannot be combined")
    if not 5 <= args.poll_seconds <= 300:
        parser.error("--poll-seconds must be 5..300")
    if not os.getenv("NB_DATABASE_PATH") or not Path(os.environ["NB_DATABASE_PATH"]).is_absolute():
        parser.error("set NB_DATABASE_PATH explicitly to an absolute, operator-approved local cache path")
    try:
        asyncio.run(watch(Settings.from_env(), args.from_block, args.to_block,
                          args.max_blocks, args.confirmations, args.poll_seconds, args.follow))
    except (SourceError, httpx.HTTPError, OSError) as exc:
        # httpx exceptions may include credential-bearing RPC URLs.
        reason = str(exc) if isinstance(exc, SourceError) else "network or cache read failed (RPC URL suppressed)"
        print(json.dumps({"status": "blocked", "reason": reason}), file=sys.stderr)
        raise SystemExit(2) from None
    except KeyboardInterrupt:
        print("Observer stopped; restart with the last printed next_from_block.", file=sys.stderr)


if __name__ == "__main__":
    main()
