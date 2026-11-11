"""Test-only JSON-RPC responses, never deployment or product evidence."""

import asyncio
from dataclasses import replace
import json

import httpx
import pytest

from neural_brake.config import Settings
from neural_brake.ingest import SourceError
from neural_brake.preflight import BASE_FACTORY, _run_preflight


def _settings() -> Settings:
    return Settings(
        rpc_url="https://base.test", database_path=":memory:", chain_id=84532,
        pool_address="0x46880b404CD35c165EDdefF7421019F8dD25F4Ad",
        token0_address="0x036CbD53842c5426634e7929541eC2318f3dCF7e",
        token1_address="0x4200000000000000000000000000000000000006",
        token0_decimals=6, token1_decimals=18, fee=3000,
        model_artifact=None, registry_address=None, reactive_address=None, vault_address=None,
    )


def _word(value: int) -> str:
    return f"0x{value:064x}"


def test_preflight_only_reads_expected_chains_and_addresses():
    settings = _settings()
    seen = []

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        method, params = body["method"], body["params"]
        seen.append(method)
        assert method in {"eth_chainId", "eth_getCode", "eth_call"}
        if method == "eth_chainId":
            result = hex(84532 if request.url.host == "base.test" else 5318007)
        elif method == "eth_getCode":
            assert params[1] == "latest"
            result = "0x6000"
        else:
            assert params[1] == "latest"
            target, data = params[0]["to"].lower(), params[0]["data"]
            if target == BASE_FACTORY.lower():
                assert data.startswith("0x1698ee82")
                result = _word(int(settings.pool_address, 16))
            else:
                assert target == settings.pool_address.lower()
                result = {
                    "0x0dfe1681": _word(int(settings.token0_address, 16)),
                    "0xd21220a7": _word(int(settings.token1_address, 16)),
                    "0xddca3f43": _word(3000),
                }[data]
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await _run_preflight(client, settings, "https://lasna.test")

    result = asyncio.run(check())
    assert result["status"] == "read_only_preflight_passed"
    assert result["fee"] == 3000
    assert "eth_sendRawTransaction" not in seen


def test_preflight_rejects_wrong_network_and_pool_metadata():
    settings = _settings()

    def respond(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        method, params = body["method"], body["params"]
        if method == "eth_chainId":
            result = hex(84532 if request.url.host == "base.test" else 1)
        else:
            raise AssertionError(f"unexpected RPC request after network mismatch: {method} {params}")
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            return await _run_preflight(client, settings, "https://lasna.test")

    with pytest.raises(SourceError, match="Reactive Lasna RPC chain mismatch"):
        asyncio.run(check())
    with pytest.raises(SourceError, match="selected USDC/WETH"):
        asyncio.run(_run_preflight(None, replace(settings, fee=500), "https://lasna.test"))
