"""TEST-ONLY fixtures; they are never product observations or model inputs."""

import asyncio
import json
import sqlite3
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from neural_brake import api
from neural_brake.config import Settings
from neural_brake.ingest import SWAP_TOPIC, RpcSource, SourceError, _ingest, decode_swap_log
from neural_brake.storage import Store
from neural_brake.verify_history import (
    _get_page, _history_endpoint, _page_params, _pool_log, _post, _recover_transaction_hash, _rpc_batches,
    RPC_BATCH_DELAY,
)

CHAIN_ID = 84532
POOL = "0x46880b404CD35c165EDdefF7421019F8dD25F4Ad"
BLOCK_HASH = "0x" + "ab" * 32
TX_HASH = "0x" + "cd" * 32


def _word(value: int, signed: bool = False) -> str:
    if signed and value < 0:
        value %= 1 << 256
    return f"{value:064x}"


def _fixture_swap_log() -> dict:
    return {
        "address": POOL,
        "topics": [SWAP_TOPIC, "0x" + "0" * 24 + "11" * 20, "0x" + "0" * 24 + "22" * 20],
        "data": "0x" + "".join((_word(-5, True), _word(7, True), _word(9), _word(10), _word(3))),
        "blockNumber": hex(56570453), "blockHash": BLOCK_HASH, "transactionHash": TX_HASH,
        "logIndex": hex(7), "removed": False,
    }


def test_decodes_three_canonical_topics_and_fields():
    result = decode_swap_log(_fixture_swap_log(), CHAIN_ID, POOL)
    assert result.sender == "0x" + "11" * 20
    assert result.recipient == "0x" + "22" * 20
    assert (result.amount0, result.amount1, result.tick) == ("-5", "7", 3)
    assert result.timestamp is None  # decoder never fabricates a block timestamp


def test_blockscout_null_topic_slot_is_normalized_for_receipt_verification():
    raw = _fixture_swap_log()
    item = {
        "address": {"hash": POOL},
        "topics": raw["topics"] + [None],
        "data": raw["data"],
        "block_number": int(raw["blockNumber"], 16),
        "block_hash": BLOCK_HASH,
        "transaction_hash": TX_HASH,
        "index": 7,
        "block_timestamp": "2026-11-09T14:52:39.000000Z",
    }
    normalized = _pool_log(item, POOL)
    assert normalized is not None
    assert normalized["topics"] == raw["topics"]
    assert normalized["log_index"] == 7
    assert normalized["timestamp"] == datetime(2026, 11, 9, 14, 52, 39, tzinfo=timezone.utc)

    item["topics"] = raw["topics"] + ["0x" + "00" * 32]
    with pytest.raises(SourceError, match="topic list"):
        _pool_log(item, POOL)


def test_pool_log_marks_empty_transaction_hash_for_rpc_recovery():
    raw = _fixture_swap_log()
    item = {
        "address": {"hash": POOL},
        "topics": raw["topics"],
        "data": raw["data"],
        "block_number": int(raw["blockNumber"], 16),
        "block_hash": BLOCK_HASH,
        "transaction_hash": "",
        "index": 7,
        "block_timestamp": "2026-11-09T14:52:39Z",
    }
    normalized = _pool_log(item, POOL)
    assert normalized is not None
    assert normalized["transaction_hash"] is None


def test_recovers_missing_explorer_transaction_hash_from_matching_rpc_log():
    expected = {
        "block_number": 56570453,
        "block_hash": BLOCK_HASH,
        "log_index": 7,
        "topics": _fixture_swap_log()["topics"],
        "data": _fixture_swap_log()["data"],
    }

    class MockClient:
        async def post(self, url, json):
            assert json["method"] == "eth_getLogs"
            assert json["params"][0]["fromBlock"] == hex(56570453)
            request = httpx.Request("POST", url)
            return httpx.Response(200, json={"result": [_fixture_swap_log()]}, request=request)

    result = asyncio.run(_recover_transaction_hash(MockClient(), "https://rpc.test", CHAIN_ID, POOL, expected))
    assert result == TX_HASH.lower()


def test_rpc_batches_are_throttled_between_provider_sized_batches(monkeypatch):
    batch_sizes = []
    sleeps = []

    async def fake_rpc_batch(client, url, calls):
        batch_sizes.append(len(calls))
        return list(range(len(calls)))

    async def record_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr("neural_brake.verify_history._rpc_batch", fake_rpc_batch)
    monkeypatch.setattr("neural_brake.verify_history.asyncio.sleep", record_sleep)

    async def collect():
        return [batch async for batch in _rpc_batches(None, "https://rpc.test", [("eth_chainId", [])] * 41)]

    batches = asyncio.run(collect())
    assert batch_sizes == [25, 16]
    assert len(batches) == 2
    assert sleeps == [RPC_BATCH_DELAY]


def test_history_endpoint_uses_authenticated_multichain_api_without_key_in_url():
    key = "proapi-test-secret"
    url, headers = _history_endpoint(CHAIN_ID, POOL, key)
    assert url == f"https://api.blockscout.com/{CHAIN_ID}/api/v2/addresses/{POOL}/logs"
    assert headers == {"Authorization": f"Bearer {key}"}
    assert key not in url

    fallback_url, fallback_headers = _history_endpoint(CHAIN_ID, POOL, None)
    assert fallback_url == f"https://base-sepolia.blockscout.com/api/v2/addresses/{POOL}/logs"
    assert fallback_headers == {}


def test_history_requests_maximum_supported_page_size_with_every_cursor():
    assert _page_params(None) == {"items_count": 100}
    assert _page_params({"block_number": 123, "items_count": 50}) == {
        "block_number": 123,
        "items_count": 100,
    }


def test_history_page_retries_transient_transport_error(monkeypatch):
    class FlakyClient:
        calls = 0

        async def get(self, url, params=None):
            self.calls += 1
            if self.calls == 1:
                raise httpx.ReadError("temporary disconnect")
            request = httpx.Request("GET", url)
            return httpx.Response(200, json={"items": []}, request=request)

    sleeps = []

    async def record_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr("neural_brake.verify_history.asyncio.sleep", record_sleep)
    client = FlakyClient()
    result = asyncio.run(_get_page(client, "https://explorer.test/logs", None))
    assert result == {"items": []}
    assert client.calls == 2
    assert sleeps == [1.0]


def test_rpc_post_retries_transient_transport_error(monkeypatch):
    class FlakyClient:
        calls = 0

        async def post(self, url, json):
            self.calls += 1
            if self.calls == 1:
                raise httpx.ReadError("temporary disconnect")
            request = httpx.Request("POST", url)
            return httpx.Response(200, json={"result": "0x1"}, request=request)

    sleeps = []

    async def record_sleep(delay):
        sleeps.append(delay)

    monkeypatch.setattr("neural_brake.verify_history.asyncio.sleep", record_sleep)
    client = FlakyClient()
    result = asyncio.run(_post(client, "https://rpc.test", {"method": "eth_chainId"}))
    assert result == {"result": "0x1"}
    assert client.calls == 2
    assert sleeps == [1.0]


@pytest.mark.parametrize("mutation", [
    lambda log: log.update(topics=log["topics"][:2]),
    lambda log: log.update(topics=[SWAP_TOPIC]),
    lambda log: log["topics"].__setitem__(1, "0x" + "1" * 64),
    lambda log: log.update(address="0x" + "33" * 20),
    lambda log: log.update(data="0x" + "0" * 319 + "g"),
    lambda log: log.update(data="0x" + "".join((_word(0), _word(0), _word(1 << 160), _word(0), _word(0)))),
    lambda log: log.update(data="0x" + "".join((_word(0), _word(0), _word(0), _word(0), _word(1 << 24)))),
    lambda log: log.update(blockNumber="0x00"),
])
def test_rejects_invalid_log(mutation):
    log = _fixture_swap_log()
    mutation(log)
    with pytest.raises(SourceError):
        decode_swap_log(log, CHAIN_ID, POOL)


def _settings(database: str, rpc_url: str = "http://rpc.test", chain_id: int = CHAIN_ID) -> Settings:
    return Settings(rpc_url=rpc_url, database_path=database, chain_id=chain_id, pool_address=POOL,
        token0_address="0x" + "01" * 20, token1_address="0x" + "02" * 20,
        token0_decimals=6, token1_decimals=18, fee=3000,
        model_artifact=None, registry_address=None, reactive_address=None, vault_address=None)


def _mock_rpc(monkeypatch, chain_id: int, block_hash: str):
    log = _fixture_swap_log()
    original_client = httpx.AsyncClient

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        method = body["method"]
        if method == "eth_chainId":
            result = hex(chain_id)
        elif method == "eth_getLogs":
            result = [log]
        elif method == "eth_getBlockByNumber":
            result = {"number": hex(56570453), "hash": block_hash, "timestamp": hex(1791234567)}
        else:  # The mocked adapter must expose read-only JSON-RPC calls only.
            raise AssertionError(f"unexpected JSON-RPC method: {method}")
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": result})

    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: original_client(
        *args, transport=httpx.MockTransport(handler), **kwargs))


def test_ingest_persists_rpc_timestamp_and_is_idempotent(monkeypatch, tmp_path):
    path = str(tmp_path / "observations.sqlite3")
    settings = _settings(path)
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: settings))
    _mock_rpc(monkeypatch, CHAIN_ID, BLOCK_HASH)
    assert asyncio.run(_ingest(56570453, 56570453)) == 1
    assert asyncio.run(_ingest(56570453, 56570453)) == 1
    count, first, last = Store(path).audit()
    assert count == 1
    assert first == last == datetime.fromtimestamp(1791234567, timezone.utc).isoformat()


def test_bulk_store_rolls_back_entire_batch_on_insert_failure(tmp_path):
    path = str(tmp_path / "atomic.sqlite3")
    store = Store(path)
    first_log = _fixture_swap_log()
    second_log = _fixture_swap_log()
    second_log["transactionHash"] = "0x" + "ef" * 32
    timestamp = datetime(2026, 11, 9, 14, 52, 39, tzinfo=timezone.utc)
    first = decode_swap_log(first_log, CHAIN_ID, POOL, timestamp)
    second = decode_swap_log(second_log, CHAIN_ID, POOL, timestamp)

    with store.connect() as db:
        db.execute(
            f"CREATE TRIGGER reject_second BEFORE INSERT ON swaps "
            f"WHEN NEW.transaction_hash = '{second.transaction_hash}' "
            "BEGIN SELECT RAISE(ABORT, 'intentional test failure'); END"
        )

    with pytest.raises(sqlite3.IntegrityError, match="intentional test failure"):
        store.add_swaps([first, second])
    assert store.audit()[0] == 0


def test_ingest_rejects_chain_mismatch_without_persisting(monkeypatch, tmp_path):
    path = str(tmp_path / "observations.sqlite3")
    settings = _settings(path)
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: settings))
    _mock_rpc(monkeypatch, CHAIN_ID + 1, BLOCK_HASH)
    with pytest.raises(SourceError, match="chain identity"):
        asyncio.run(_ingest(56570453, 56570453))
    assert Store(path).audit()[0] == 0


def test_ingest_rejects_block_hash_mismatch_without_persisting(monkeypatch, tmp_path):
    path = str(tmp_path / "observations.sqlite3")
    settings = _settings(path)
    monkeypatch.setattr(Settings, "from_env", classmethod(lambda cls: settings))
    _mock_rpc(monkeypatch, CHAIN_ID, "0x" + "ef" * 32)
    with pytest.raises(SourceError, match="block hash"):
        asyncio.run(_ingest(56570453, 56570453))
    assert Store(path).audit()[0] == 0


def test_api_liveness_and_fail_closed_readiness_and_publish(monkeypatch, tmp_path):
    monkeypatch.setattr(api, "store", Store(str(tmp_path / "api.sqlite3")))
    client = TestClient(api.app)
    assert client.get("/api/v1/health/live").json() == {"status": "live"}
    ready = client.get("/api/v1/health/ready")
    assert ready.status_code == 503
    assert ready.json()["detail"]["components"]["model"]["available"] is False
    response = client.post("/api/v1/candidates/not-a-candidate/publish")
    assert response.status_code == 503
