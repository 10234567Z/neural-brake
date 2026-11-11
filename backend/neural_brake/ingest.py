"""Strict decoder for explicitly requested Base Sepolia Uniswap V3 Swap logs."""

import re
import argparse
import asyncio
from datetime import datetime, timezone

import httpx

from .config import Settings
from .schemas import SwapObservation
from .storage import Store

# keccak256("Swap(address,address,int256,int256,uint160,uint128,int24)")
SWAP_TOPIC = "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67"
HEX32 = re.compile(r"0x[0-9a-fA-F]{64}\Z")
ADDRESS_TOPIC = re.compile(r"0x0{24}[0-9a-fA-F]{40}\Z")
QUANTITY = re.compile(r"0x(?:0|[1-9a-fA-F][0-9a-fA-F]*)\Z")


class SourceError(RuntimeError):
    pass


def _signed(word: str, bits: int) -> int:
    value = int(word, 16)
    return value - (1 << bits) if value & (1 << (bits - 1)) else value


def decode_swap_log(log: dict, chain_id: int, pool_address: str, timestamp: datetime | None = None) -> SwapObservation:
    """Decode an actual JSON-RPC log; callers must not feed product fixtures."""
    if log.get("removed") is True:
        raise SourceError("removed/reorged log")
    topics, data = log.get("topics"), log.get("data")
    if (not isinstance(topics, list) or len(topics) != 3
            or not isinstance(topics[0], str) or topics[0].lower() != SWAP_TOPIC
            or not isinstance(topics[1], str) or not ADDRESS_TOPIC.fullmatch(topics[1])
            or not isinstance(topics[2], str) or not ADDRESS_TOPIC.fullmatch(topics[2])):
        raise SourceError("unexpected event signature")
    address = log.get("address")
    if not isinstance(address, str) or address.lower() != pool_address.lower():
        raise SourceError("unexpected pool address")
    if not isinstance(data, str) or not re.fullmatch(r"0x[0-9a-fA-F]{320}", data):
        raise SourceError("invalid Swap event data length")
    words = [data[2 + i * 64: 2 + (i + 1) * 64] for i in range(5)]
    block_hash, tx_hash = log.get("blockHash"), log.get("transactionHash")
    if not isinstance(block_hash, str) or not HEX32.fullmatch(block_hash) or not isinstance(tx_hash, str) or not HEX32.fullmatch(tx_hash):
        raise SourceError("invalid source hash")
    try:
        block_num_raw, log_index_raw = log["blockNumber"], log["logIndex"]
        if (not isinstance(block_num_raw, str) or not QUANTITY.fullmatch(block_num_raw)
                or not isinstance(log_index_raw, str) or not QUANTITY.fullmatch(log_index_raw)):
            raise ValueError("non-canonical log position")
        block_num, log_index = int(block_num_raw, 16), int(log_index_raw, 16)
        # int24 is sign-extended in ABI word; enforce canonical sign extension.
        tick_word = int(words[4], 16)
        tick = _signed(words[4], 256)
        if tick < -(1 << 23) or tick >= (1 << 23):
            raise ValueError("tick out of int24 range")
        if tick_word != (tick % (1 << 256)):
            raise ValueError("non-canonical tick")
        amount0, amount1 = _signed(words[0], 256), _signed(words[1], 256)
        sqrt_price, liquidity = int(words[2], 16), int(words[3], 16)
        if sqrt_price >= (1 << 160) or liquidity >= (1 << 128):
            raise ValueError("field out of ABI range")
        if block_num < 0 or log_index < 0:
            raise ValueError("negative source position")
        return SwapObservation(chain_id=chain_id, pool_address=pool_address.lower(), block_number=block_num,
            block_hash=block_hash.lower(), transaction_hash=tx_hash.lower(), log_index=log_index,
            sender="0x" + topics[1][-40:].lower(), recipient="0x" + topics[2][-40:].lower(),
            timestamp=timestamp, amount0=str(amount0), amount1=str(amount1), sqrt_price_x96=str(sqrt_price),
            liquidity=str(liquidity), tick=tick)
    except (KeyError, TypeError, ValueError) as exc:
        raise SourceError("invalid Swap log fields") from exc


class RpcSource:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def verify_chain(self) -> None:
        if not self.settings.rpc_url:
            raise SourceError("NB_RPC_URL is missing")
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                response = await client.post(self.settings.rpc_url, json={"jsonrpc": "2.0", "id": 1, "method": "eth_chainId", "params": []})
                response.raise_for_status()
                payload = response.json()
            if int(payload["result"], 16) != self.settings.chain_id:
                raise SourceError("RPC chain identity mismatch")
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise SourceError("RPC chain identity check failed") from exc

    async def get_logs(self, from_block: int, to_block: int) -> list[dict]:
        if from_block < 0 or to_block < from_block or to_block - from_block > 2000:
            raise ValueError("block range must be ordered and at most 2001 blocks")
        await self.verify_chain()
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.post(self.settings.rpc_url, json={"jsonrpc": "2.0", "id": 2,
                "method": "eth_getLogs", "params": [{"fromBlock": hex(from_block), "toBlock": hex(to_block),
                    "address": self.settings.pool_address, "topics": [SWAP_TOPIC]}]})
            response.raise_for_status()
            data = response.json()
        if "error" in data or not isinstance(data.get("result"), list):
            raise SourceError("RPC log read failed")
        return data["result"]

    async def get_block(self, block_number: int) -> dict:
        if block_number < 0:
            raise ValueError("block number must be non-negative")
        async with httpx.AsyncClient(timeout=5) as client:
            response = await client.post(self.settings.rpc_url, json={"jsonrpc": "2.0", "id": 3,
                "method": "eth_getBlockByNumber", "params": [hex(block_number), False]})
            response.raise_for_status()
            data = response.json()
        block = data.get("result")
        if "error" in data or not isinstance(block, dict):
            raise SourceError("RPC block read failed")
        try:
            if (not isinstance(block.get("number"), str) or not QUANTITY.fullmatch(block["number"])
                    or int(block["number"], 16) != block_number
                    or not isinstance(block.get("timestamp"), str) or not QUANTITY.fullmatch(block["timestamp"])
                    or not isinstance(block.get("hash"), str) or not HEX32.fullmatch(block["hash"])):
                raise ValueError("malformed block response")
            seconds = int(block["timestamp"], 16)
            timestamp = datetime.fromtimestamp(seconds, tz=timezone.utc)
            return {"hash": block["hash"].lower(), "timestamp": timestamp}
        except (OverflowError, OSError, ValueError) as exc:
            raise SourceError("invalid RPC block identity or timestamp") from exc


async def _ingest(from_block: int, to_block: int) -> int:
    settings = Settings.from_env()
    source = RpcSource(settings)
    logs = await source.get_logs(from_block, to_block)
    verified = []
    block_cache: dict[int, dict] = {}
    for log in logs:
        if not isinstance(log, dict):
            raise SourceError("RPC returned a malformed log")
        try:
            raw_number = log["blockNumber"]
            if not isinstance(raw_number, str) or not QUANTITY.fullmatch(raw_number):
                raise ValueError("non-canonical block number")
            block_number = int(raw_number, 16)
            if block_number < from_block or block_number > to_block:
                raise ValueError("RPC log lies outside requested block range")
        except (KeyError, TypeError, ValueError) as exc:
            raise SourceError("invalid source block position") from exc
        if block_number not in block_cache:
            block_cache[block_number] = await source.get_block(block_number)
        block = block_cache[block_number]
        if not isinstance(log.get("blockHash"), str) or log["blockHash"].lower() != block["hash"]:
            raise SourceError("log block hash does not match RPC block")
        verified.append(decode_swap_log(log, settings.chain_id, settings.pool_address, block["timestamp"]))

    # Persist only after all records in this bounded response have passed validation.
    store = Store(settings.database_path)
    for observation in verified:
        store.add_swap(observation)
    return len(verified)


def main() -> None:
    """Explicit bounded backfill. No synthetic rows or write RPC methods."""
    parser = argparse.ArgumentParser(description="Read and cache verified Swap logs from configured RPC")
    parser.add_argument("--from-block", type=int, required=True)
    parser.add_argument("--to-block", type=int, required=True)
    args = parser.parse_args()
    count = asyncio.run(_ingest(args.from_block, args.to_block))
    print(f"Validated and stored {count} source Swap log(s); no score or live-state claim is made.")
