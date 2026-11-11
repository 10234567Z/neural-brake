"""Read-only testnet identity checks; never deploys, signs, or funds anything."""

import argparse
import asyncio
import json
import os
import re
import sys

import httpx

from .config import Settings
from .ingest import SourceError
from .verify_history import _rpc

BASE_SEPOLIA_CHAIN_ID = 84532
LASNA_CHAIN_ID = 5318007
LASNA_RPC = "https://lasna-rpc.rnk.dev/"
BASE_FACTORY = "0x4752ba5DBc23f44D87826276BF6Fd6b1C372aD24"
BASE_CALLBACK_PROXY = "0x476F44b11F5C942c58f4c8E3d0300E5710743585"
LASNA_SYSTEM = "0x8888888888888888888888888888888888888888"
WORD = re.compile(r"0x[0-9a-fA-F]{64}\Z")
ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}\Z")


def _word(value: object) -> int:
    if not isinstance(value, str) or not WORD.fullmatch(value):
        raise SourceError("RPC eth_call returned an invalid ABI word")
    return int(value, 16)


def _address(value: object) -> str:
    raw = _word(value)
    if raw >> 160:
        raise SourceError("RPC eth_call returned a noncanonical address")
    return "0x" + f"{raw:040x}"


async def _check_code(client: httpx.AsyncClient, rpc: str, address: str, label: str) -> None:
    code = await _rpc(client, rpc, "eth_getCode", [address, "latest"])
    if not isinstance(code, str) or not re.fullmatch(r"0x(?:[0-9a-fA-F]{2})+", code):
        raise SourceError(f"{label} has no contract code at the documented address")


async def _call(client: httpx.AsyncClient, rpc: str, address: str, data: str) -> object:
    return await _rpc(client, rpc, "eth_call", [{"to": address, "data": data}, "latest"])


async def _check_network(client: httpx.AsyncClient, rpc: str, chain_id: int, label: str) -> None:
    returned = await _rpc(client, rpc, "eth_chainId", [])
    try:
        actual = int(returned, 16)
    except (TypeError, ValueError):
        raise SourceError(f"{label} RPC returned an invalid chain identity") from None
    if actual != chain_id:
        raise SourceError(f"{label} RPC chain mismatch: expected {chain_id}, received {actual}")


async def _run_preflight(client: httpx.AsyncClient, settings: Settings, lasna_rpc: str) -> dict[str, object]:
    if settings.chain_id != BASE_SEPOLIA_CHAIN_ID or not settings.rpc_url or not lasna_rpc:
        raise SourceError("read-only preflight requires Base Sepolia and a Lasna RPC")
    if any(not ADDRESS.fullmatch(addr) for addr in (settings.pool_address, settings.token0_address, settings.token1_address)):
        raise SourceError("preflight has an invalid configured pool or token address")
    if settings.fee != 3000:
        raise SourceError("preflight is restricted to the selected USDC/WETH 0.3% pool")

    await _check_network(client, settings.rpc_url, BASE_SEPOLIA_CHAIN_ID, "Base Sepolia")
    await _check_network(client, lasna_rpc, LASNA_CHAIN_ID, "Reactive Lasna")
    for label, rpc, address in (
        ("Uniswap v3 factory", settings.rpc_url, BASE_FACTORY),
        ("selected Uniswap v3 pool", settings.rpc_url, settings.pool_address),
        ("Base Sepolia callback proxy", settings.rpc_url, BASE_CALLBACK_PROXY),
        ("Reactive Lasna system", lasna_rpc, LASNA_SYSTEM),
    ):
        await _check_code(client, rpc, address, label)

    token0 = _address(await _call(client, settings.rpc_url, settings.pool_address, "0x0dfe1681"))
    token1 = _address(await _call(client, settings.rpc_url, settings.pool_address, "0xd21220a7"))
    fee = _word(await _call(client, settings.rpc_url, settings.pool_address, "0xddca3f43"))
    if (token0, token1, fee) != (settings.token0_address.lower(), settings.token1_address.lower(), settings.fee):
        raise SourceError("pool token order or fee differs from configured source")

    # getPool(token0, token1, 3000) must resolve to the exact configured source.
    factory_data = "0x1698ee82" + f"{int(token0, 16):064x}{int(token1, 16):064x}{fee:064x}"
    factory_pool = _address(await _call(client, settings.rpc_url, BASE_FACTORY, factory_data))
    if factory_pool != settings.pool_address.lower():
        raise SourceError("Uniswap factory lookup does not match the configured pool")

    return {
        "status": "read_only_preflight_passed",
        "base_sepolia_chain_id": BASE_SEPOLIA_CHAIN_ID,
        "reactive_lasna_chain_id": LASNA_CHAIN_ID,
        "pool": settings.pool_address,
        "fee": fee,
        "callback_proxy": BASE_CALLBACK_PROXY,
        "reactive_system": LASNA_SYSTEM,
        "limitations": "Latest-block identity and code checks only; no balances, funding, deployment, score publication, callback delivery, or vault pause verified",
    }


async def preflight(settings: Settings, lasna_rpc: str) -> dict[str, object]:
    async with httpx.AsyncClient(timeout=30) as client:
        return await _run_preflight(client, settings, lasna_rpc)


def main() -> None:
    parser = argparse.ArgumentParser(description="Check testnet RPC identities and contract addresses without signing")
    parser.parse_args()
    try:
        result = asyncio.run(preflight(Settings.from_env(), os.getenv("NB_LASNA_RPC_URL", LASNA_RPC)))
    except (SourceError, httpx.HTTPError, OSError) as exc:
        # httpx exceptions may contain credential-bearing RPC URLs; do not include them.
        reason = str(exc) if isinstance(exc, SourceError) else "network read failed (RPC URL suppressed)"
        print(json.dumps({"status": "blocked", "reason": reason}), file=sys.stderr)
        raise SystemExit(2) from None
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
