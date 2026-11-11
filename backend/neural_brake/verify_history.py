"""Discover pool logs through Blockscout, then verify each Swap via Base RPC receipts.

This is an explicit historical audit/import command; it is not the live ingestor.
No observation is persisted until every returned Swap has a successful receipt,
matching event bytes, and a matching RPC block hash/timestamp.
"""

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from typing import Any, AsyncIterator

import httpx

from .config import Settings
from .ingest import SWAP_TOPIC, SourceError, decode_swap_log
from .storage import Store

BLOCKSCOUT_EXPLORER = "https://base-sepolia.blockscout.com/api/v2/addresses"
BLOCKSCOUT_API = "https://api.blockscout.com"
PAGE_SIZE = 100
BATCH_SIZE = 20
RPC_BATCH_DELAY = 0.9


def _history_endpoint(chain_id: int, pool: str, api_key: str | None) -> tuple[str, dict[str, str]]:
    if api_key:
        url = f"{BLOCKSCOUT_API}/{chain_id}/api/v2/addresses/{pool}/logs"
        return url, {"Authorization": f"Bearer {api_key}"}
    return f"{BLOCKSCOUT_EXPLORER}/{pool}/logs", {}


def _page_params(cursor: dict[str, int] | None) -> dict[str, int]:
    params = dict(cursor or {})
    params["items_count"] = PAGE_SIZE
    return params


async def _post(client: httpx.AsyncClient, url: str, payload: Any) -> Any:
    for attempt in range(8):
        try:
            response = await client.post(url, json=payload)
        except httpx.TransportError:
            if attempt == 7:
                raise SourceError("RPC transport failure persisted after bounded retries") from None
            await asyncio.sleep(min(1.0 + attempt, 8.0))
            continue
        if response.status_code == 429:
            retry_after = response.headers.get("retry-after")
            delay = float(retry_after) if retry_after and retry_after.isdigit() else min(1.0 + attempt, 8.0)
            await asyncio.sleep(delay)
            continue
        response.raise_for_status()
        return response.json()
    raise SourceError("upstream rate limit persisted after bounded retries")


async def _rpc(client: httpx.AsyncClient, url: str, method: str, params: list[Any]) -> Any:
    result = await _post(client, url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    if not isinstance(result, dict) or result.get("error") or "result" not in result:
        raise SourceError(f"RPC {method} failed")
    return result["result"]


async def _rpc_batch(client: httpx.AsyncClient, url: str, calls: list[tuple[str, list[Any]]]) -> list[Any]:
    payload = [
        {"jsonrpc": "2.0", "id": index, "method": method, "params": params}
        for index, (method, params) in enumerate(calls, 1)
    ]
    response = await _post(client, url, payload)
    if not isinstance(response, list):
        # Some RPC gateways silently reject or unwrap larger batches. Retry each
        # exact read singly; never interpret a partial batch as a complete result.
        return [await _rpc(client, url, method, params) for method, params in calls]
    by_id = {}
    for item in response:
        if not isinstance(item, dict):
            continue
        identifier = item.get("id")
        if not isinstance(identifier, (int, str)) or not str(identifier).isdigit():
            continue
        try:
            by_id[int(identifier)] = item
        except (TypeError, ValueError):
            continue
    values = []
    for index, (method, params) in enumerate(calls, 1):
        item = by_id.get(index)
        if item is None or item.get("error") or "result" not in item:
            values.append(await _rpc(client, url, method, params))
            continue
        values.append(item["result"])
    return values


async def _rpc_batches(
    client: httpx.AsyncClient,
    url: str,
    calls: list[tuple[str, list[Any]]],
) -> AsyncIterator[tuple[int, list[tuple[str, list[Any]]], list[Any]]]:
    for offset in range(0, len(calls), BATCH_SIZE):
        batch = calls[offset:offset + BATCH_SIZE]
        results = await _rpc_batch(client, url, batch)
        yield offset, batch, results
        if offset + len(batch) < len(calls):
            await asyncio.sleep(RPC_BATCH_DELAY)


async def _get_page(client: httpx.AsyncClient, url: str, params: dict[str, int] | None) -> Any:
    for attempt in range(8):
        try:
            response = await client.get(url, params=params)
        except httpx.TransportError:
            if attempt == 7:
                raise SourceError("history service transport failure persisted after bounded retries") from None
            await asyncio.sleep(min(1.0 + attempt, 8.0))
            continue
        if response.status_code == 429:
            retry_after = response.headers.get("retry-after")
            delay = float(retry_after) if retry_after and retry_after.isdigit() else min(1.0 + attempt, 8.0)
            await asyncio.sleep(delay)
            continue
        response.raise_for_status()
        return response.json()
    raise SourceError("history service rate limit persisted after bounded retries")


def _pool_log(item: dict[str, Any], pool: str) -> dict[str, Any] | None:
    address = item.get("address")
    address_hash = address.get("hash") if isinstance(address, dict) else None
    topics = item.get("topics")
    if not isinstance(topics, list) or not topics or not isinstance(topics[0], str):
        return None
    # Blockscout serializes four topic slots and uses null for an unused topic3;
    # EVM JSON-RPC receipts correctly contain exactly the three present topics.
    if len(topics) == 4 and topics[3] is None:
        topics = topics[:3]
    if topics[0].lower() != SWAP_TOPIC:
        return None
    if not isinstance(address_hash, str) or address_hash.lower() != pool.lower():
        raise SourceError("explorer returned a log for a different pool")
    if len(topics) != 3 or any(not isinstance(topic, str) or len(topic) != 66 for topic in topics):
        raise SourceError("explorer returned a malformed Swap topic list")
    data = item.get("data")
    if not isinstance(data, str) or len(data) != 322:
        raise SourceError("explorer returned malformed Swap data")
    try:
        block_number = int(item["block_number"])
        log_index = int(item["index"])
        block_hash = item["block_hash"].lower()
        tx_hash = item["transaction_hash"].lower()
        explorer_time = datetime.fromisoformat(item["block_timestamp"].replace("Z", "+00:00"))
    except (KeyError, AttributeError, TypeError, ValueError) as exc:
        raise SourceError("explorer returned malformed Swap provenance") from exc
    if block_number < 0 or log_index < 0 or len(block_hash) != 66 or (tx_hash and len(tx_hash) != 66):
        raise SourceError("explorer returned invalid Swap provenance")
    return {
        "block_number": block_number,
        "block_hash": block_hash,
        "transaction_hash": tx_hash or None,
        "log_index": log_index,
        "timestamp": explorer_time.astimezone(timezone.utc),
        "topics": [topic.lower() for topic in topics],
        "data": data.lower(),
    }


async def _recover_transaction_hash(
    client: httpx.AsyncClient,
    rpc_url: str,
    chain_id: int,
    pool: str,
    expected: dict[str, Any],
) -> str:
    block_number = expected["block_number"]
    logs = await _rpc(client, rpc_url, "eth_getLogs", [{
        "fromBlock": hex(block_number),
        "toBlock": hex(block_number),
        "address": pool,
        "topics": [SWAP_TOPIC],
    }])
    if not isinstance(logs, list):
        raise SourceError("RPC returned malformed logs while recovering transaction provenance")

    matches = []
    for log in logs:
        if not isinstance(log, dict):
            raise SourceError("RPC returned a malformed log while recovering transaction provenance")
        try:
            observation = decode_swap_log(log, chain_id, pool)
        except SourceError:
            raise SourceError("RPC returned an invalid Swap while recovering transaction provenance") from None
        if observation.block_number != block_number or observation.log_index != expected["log_index"]:
            continue
        if (
            observation.block_hash != expected["block_hash"]
            or [topic.lower() for topic in log["topics"]] != expected["topics"]
            or log["data"].lower() != expected["data"]
        ):
            raise SourceError("RPC Swap differs from the explorer event missing its transaction hash")
        matches.append(observation.transaction_hash)
    if len(matches) != 1:
        raise SourceError("RPC did not uniquely resolve the explorer event missing its transaction hash")
    return matches[0]


async def verify_history(since: datetime, max_pages: int = 1000, page_delay: float = 0.8) -> dict[str, Any]:
    settings = Settings.from_env()
    if since.tzinfo is None or since.utcoffset() is None:
        raise SourceError("--since must include a UTC offset")
    since = since.astimezone(timezone.utc)
    if not settings.rpc_url:
        raise SourceError("NB_RPC_URL is required for historical receipt verification")
    if settings.chain_id != 84532:
        raise SourceError("historical verifier is restricted to the selected Base Sepolia source")

    api_key = os.environ.get("NB_BLOCKSCOUT_API_KEY")
    endpoint, request_headers = _history_endpoint(settings.chain_id, settings.pool_address, api_key)
    async with httpx.AsyncClient(timeout=60, headers=request_headers) as client:
        chain_id = int(await _rpc(client, settings.rpc_url, "eth_chainId", []), 16)
        if chain_id != settings.chain_id:
            raise SourceError("RPC chain identity mismatch")

        cursor: dict[str, int] | None = None
        swaps: dict[tuple[int, str, int], dict[str, Any]] = {}
        recovered_tx_hashes = 0
        pages = 0
        previous_oldest_block: int | None = None
        reached_boundary = False
        while pages < max_pages:
            payload = await _get_page(client, endpoint, _page_params(cursor))
            items = payload.get("items")
            if not isinstance(items, list):
                raise SourceError("Blockscout returned a malformed history page")
            page_block_numbers: list[int] = []
            for item in items:
                try:
                    item_block = int(item["block_number"])
                    item_time = datetime.fromisoformat(item["block_timestamp"].replace("Z", "+00:00"))
                except (KeyError, AttributeError, TypeError, ValueError) as exc:
                    raise SourceError("Blockscout returned a malformed page position") from exc
                page_block_numbers.append(item_block)
                if item_time.astimezone(timezone.utc) < since:
                    reached_boundary = True
                    break
                log = _pool_log(item, settings.pool_address)
                if log is None:
                    continue
                if log["transaction_hash"] is None:
                    log["transaction_hash"] = await _recover_transaction_hash(
                        client, settings.rpc_url, settings.chain_id, settings.pool_address, log
                    )
                    recovered_tx_hashes += 1
                key = (log["block_number"], log["transaction_hash"], log["log_index"])
                if key in swaps:
                    raise SourceError("duplicate source log across history pages")
                swaps[key] = log
            if any(a < b for a, b in zip(page_block_numbers, page_block_numbers[1:])):
                raise SourceError("Blockscout history page is not ordered newest to oldest")
            if previous_oldest_block is not None and page_block_numbers and page_block_numbers[0] > previous_oldest_block:
                raise SourceError("Blockscout page cursor moved forward in history")
            if page_block_numbers:
                previous_oldest_block = page_block_numbers[-1]
            pages += 1
            if pages % 25 == 0:
                print(f"Read {pages} history pages; {len(swaps)} Swap logs are within the requested window.", flush=True)
            if reached_boundary:
                cursor = None
                break
            next_cursor = payload.get("next_page_params")
            if not next_cursor:
                cursor = None
                break
            cursor = next_cursor
            await asyncio.sleep(max(0.0, page_delay))

        if cursor:
            raise SourceError(f"history exceeds bounded page limit ({max_pages}); no rows persisted")
        if not swaps:
            raise SourceError("no Swap logs were returned for the selected pool")

        print(
            f"Fetched {pages} indexed pages with {len(swaps)} unique Swap logs; "
            f"recovered {recovered_tx_hashes} missing transaction hash(es) from RPC logs; "
            "verifying RPC receipts and headers.",
            flush=True,
        )

        # Verify receipts (including log bytes/index and successful execution) in bounded batches.
        logs_by_tx: dict[str, list[tuple[tuple[int, str, int], dict[str, Any]]]] = {}
        for key, log in swaps.items():
            logs_by_tx.setdefault(log["transaction_hash"], []).append((key, log))
        transactions = sorted(logs_by_tx)
        receipts: dict[str, dict[str, Any]] = {}
        receipt_calls = [("eth_getTransactionReceipt", [tx_hash]) for tx_hash in transactions]
        async for _, batch, results in _rpc_batches(client, settings.rpc_url, receipt_calls):
            for (_, params), receipt in zip(batch, results, strict=True):
                tx_hash = params[0]
                if not isinstance(receipt, dict) or receipt.get("transactionHash", "").lower() != tx_hash:
                    raise SourceError("RPC transaction receipt missing or mismatched")
                if receipt.get("status") != "0x1":
                    raise SourceError("Swap transaction receipt did not succeed")
                receipts[tx_hash] = receipt
            if len(receipts) % 1000 == 0 or len(receipts) == len(transactions):
                print(f"Verified receipts: {len(receipts)}/{len(transactions)}", flush=True)

        block_numbers = sorted({key[0] for key in swaps})
        headers: dict[int, dict[str, Any]] = {}
        header_calls = [("eth_getBlockByNumber", [hex(block_number), False]) for block_number in block_numbers]
        async for _, batch, results in _rpc_batches(client, settings.rpc_url, header_calls):
            for (_, params), header in zip(batch, results, strict=True):
                block_number = int(params[0], 16)
                if not isinstance(header, dict) or int(header.get("number", "0x-1"), 16) != block_number:
                    raise SourceError("RPC block header missing or mismatched")
                headers[block_number] = header
            if len(headers) % 1000 == 0 or len(headers) == len(block_numbers):
                print(f"Verified block headers: {len(headers)}/{len(block_numbers)}", flush=True)

        verified = []
        for key, expected in swaps.items():
            block_number, tx_hash, log_index = key
            receipt = receipts[tx_hash]
            header = headers[block_number]
            if receipt.get("blockHash", "").lower() != expected["block_hash"]:
                raise SourceError("RPC receipt block hash differs from indexed event")
            if int(receipt.get("blockNumber", "0x-1"), 16) != block_number:
                raise SourceError("RPC receipt block number differs from indexed event")
            if header.get("hash", "").lower() != expected["block_hash"]:
                raise SourceError("RPC header hash differs from indexed event")
            timestamp = datetime.fromtimestamp(int(header["timestamp"], 16), tz=timezone.utc)
            if int(timestamp.timestamp()) != int(expected["timestamp"].timestamp()):
                raise SourceError("indexed timestamp differs from the authoritative block timestamp")
            receipt_log = next((
                item for item in receipt.get("logs", [])
                if int(item.get("logIndex", "0x-1"), 16) == log_index
            ), None)
            if not receipt_log:
                raise SourceError("Swap log index missing from RPC transaction receipt")
            if (
                receipt_log.get("address", "").lower() != settings.pool_address.lower()
                or [topic.lower() for topic in receipt_log.get("topics", [])] != expected["topics"]
                or receipt_log.get("data", "").lower() != expected["data"]
                or receipt_log.get("blockHash", "").lower() != expected["block_hash"]
                or receipt_log.get("transactionHash", "").lower() != tx_hash
            ):
                raise SourceError("indexed Swap bytes/provenance differ from RPC receipt")
            observation = decode_swap_log(receipt_log, settings.chain_id, settings.pool_address, timestamp)
            verified.append(observation)

    # Store only after the entire paginated dataset has been receipt/block verified.
    store = Store(settings.database_path)
    store.add_swaps(verified)
    timestamps = sorted(item.timestamp for item in verified if item.timestamp)
    return {
        "pool_address": settings.pool_address,
        "chain_id": settings.chain_id,
        "indexed_window_since": since.isoformat(),
        "pages": pages,
        "transactions_verified": len(receipts),
        "swap_logs_verified": len(verified),
        "rpc_recovered_transaction_hashes": recovered_tx_hashes,
        "first_timestamp": timestamps[0].isoformat() if timestamps else None,
        "last_timestamp": timestamps[-1].isoformat() if timestamps else None,
        "source": "Blockscout pagination; every Swap matched to official Base Sepolia RPC receipt and block header",
        "persisted": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify and cache genuine historical Swap receipts")
    parser.add_argument("--since", required=True, help="inclusive ISO-8601 timestamp with UTC offset")
    parser.add_argument("--max-pages", type=int, default=1000)
    parser.add_argument("--page-delay", type=float, default=0.8)
    args = parser.parse_args()
    if args.max_pages < 1 or args.max_pages > 5000 or args.page_delay < 0 or args.page_delay > 60:
        parser.error("max-pages must be 1..5000 and page-delay 0..60 seconds")
    try:
        since = datetime.fromisoformat(args.since.replace("Z", "+00:00"))
    except ValueError:
        parser.error("--since must be ISO-8601 and include a UTC offset")
    if since.tzinfo is None or since.utcoffset() is None:
        parser.error("--since must include a UTC offset")
    print(json.dumps(asyncio.run(verify_history(since, args.max_pages, args.page_delay)), indent=2))


if __name__ == "__main__":
    main()
