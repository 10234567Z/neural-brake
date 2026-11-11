"""Read-only range observer tests with mock chain reads."""

import asyncio
from dataclasses import replace

import pytest

from neural_brake.config import Settings
from neural_brake.ingest import SourceError
from neural_brake import watch as observer


def _settings() -> Settings:
    return Settings(
        rpc_url="https://base.test", database_path="/tmp/observer-test.sqlite3", chain_id=84532,
        pool_address="0x46880b404CD35c165EDdefF7421019F8dD25F4Ad",
        token0_address="0x036CbD53842c5426634e7929541eC2318f3dCF7e",
        token1_address="0x4200000000000000000000000000000000000006",
        token0_decimals=6, token1_decimals=18, fee=3000,
        model_artifact=None, registry_address=None, reactive_address=None, vault_address=None,
    )


def test_observer_limits_queries_to_ten_blocks_and_waits_for_confirmations(monkeypatch):
    calls = []
    sleeps = []
    settings = _settings()

    async def head(self):
        return 200

    async def ingest(start, end, received_settings):
        assert received_settings is settings
        calls.append((start, end))
        return 1

    async def sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr(observer.RpcSource, "get_head", head)
    monkeypatch.setattr(observer, "_ingest", ingest)
    monkeypatch.setattr(observer.asyncio, "sleep", sleep)
    result = asyncio.run(observer.watch_once(settings, 110, max_blocks=30))
    assert calls == [(110, 119), (120, 129), (130, 136)]
    assert sleeps == [observer.PAGE_DELAY, observer.PAGE_DELAY]
    assert result["next_from_block"] == 137
    assert result["confirmed_through_block"] == 136
    assert result["swaps_verified"] == 3


def test_observer_refuses_unconfirmed_ranges_and_wrong_chain(monkeypatch):
    async def head(self):
        return 200

    monkeypatch.setattr(observer.RpcSource, "get_head", head)
    with pytest.raises(SourceError, match="confirmation depth"):
        asyncio.run(observer.watch_once(_settings(), 130, to_block=140))
    with pytest.raises(SourceError, match="Base Sepolia"):
        asyncio.run(observer.watch_once(replace(_settings(), chain_id=1), 130))
    waiting = asyncio.run(observer.watch_once(_settings(), 137))
    assert waiting["status"] == "waiting_for_confirmations"
    assert waiting["next_from_block"] == 137


def test_follow_mode_waits_between_cycles(monkeypatch):
    sleeps = []

    async def one_cycle(settings, cursor, to_block, max_blocks, confirmations):
        return {"status": "research_observation_scanned", "next_from_block": cursor + 1,
                "head_block": 200, "confirmed_through_block": 136, "swaps_verified": 0}

    async def interrupt_after_wait(seconds):
        sleeps.append(seconds)
        raise KeyboardInterrupt

    monkeypatch.setattr(observer, "watch_once", one_cycle)
    monkeypatch.setattr(observer.asyncio, "sleep", interrupt_after_wait)
    with pytest.raises(KeyboardInterrupt):
        asyncio.run(observer.watch(_settings(), 100, None, 1, 64, 5, True))
    assert sleeps == [5]
