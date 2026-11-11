from base64 import urlsafe_b64decode, urlsafe_b64encode
from datetime import datetime

from fastapi import FastAPI, HTTPException, Query

from .config import Settings
from .ingest import RpcSource
from .schemas import (AuditReport, CandidateUnavailable, HealthResponse, PublicConfig,
                      PublicationDisabled, StatusComponent, SwapPage)
from .storage import Store

settings = Settings.from_env()
store = Store(settings.database_path)
app = FastAPI(title="Neural Brake read-only API", version="0.1.0")


def _cursor(value: str | None) -> int:
    if value is None:
        return 0
    if len(value) > 32:
        raise HTTPException(400, "invalid cursor")
    try:
        raw = urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode("ascii")
        if not raw.isdecimal() or int(raw) > 10_000_000:
            raise ValueError
        return int(raw)
    except (ValueError, UnicodeDecodeError):
        raise HTTPException(400, "invalid cursor") from None


def _status() -> HealthResponse:
    rpc_configured = bool(settings.rpc_url)
    model_configured = bool(settings.model_artifact)
    deployment_configured = all((settings.registry_address, settings.reactive_address, settings.vault_address))
    components = {
        "store": StatusComponent(configured=True, available=True, detail="local SQLite store available"),
        "source_reader": StatusComponent(configured=rpc_configured, available=False,
            detail="not checked" if rpc_configured else "NB_RPC_URL is missing"),
        "model": StatusComponent(configured=model_configured, available=False,
            detail="no validated model artifact loaded" if not model_configured else "artifact configured but not validated or loaded"),
        "deployments": StatusComponent(configured=deployment_configured, available=False,
            detail="no deployed integration verified" if not deployment_configured else "references configured but not verified"),
    }
    return HealthResponse(status="not_ready", components=components)


@app.get("/api/v1/health/live")
def live():
    return {"status": "live"}


@app.get("/api/v1/health/ready", response_model=HealthResponse)
def ready():
    result = _status()
    # No model/deployment readiness implementation exists, so remain fail-closed.
    raise HTTPException(status_code=503, detail=result.model_dump(mode="json"))


@app.get("/api/v1/config", response_model=PublicConfig)
def config():
    return PublicConfig(chain_id=settings.chain_id, pool_address=settings.pool_address,
        token0_address=settings.token0_address, token1_address=settings.token1_address, fee=settings.fee,
        pool_selection="provisional; not deployment or integration proof", deployments={
            "score_registry": settings.registry_address, "reactive": settings.reactive_address, "vault": settings.vault_address,
        })


@app.get("/api/v1/swaps", response_model=SwapPage)
def swaps(cursor: str | None = None, limit: int = Query(default=50, ge=1, le=100)):
    offset = _cursor(cursor)
    records, total = store.list_swaps(offset, limit)
    next_value = offset + len(records)
    next_cursor = urlsafe_b64encode(str(next_value).encode()).decode().rstrip("=") if next_value < total else None
    return SwapPage(items=records, next_cursor=next_cursor)


@app.get("/api/v1/candidates/latest", response_model=CandidateUnavailable)
def candidates_latest():
    return {"items": [], "available": False, "reason": "no validated model artifact or computed candidates"}


@app.get("/api/v1/candidates/{candidate_id}")
def candidate_detail(candidate_id: str):
    if not candidate_id or len(candidate_id) > 128:
        raise HTTPException(400, "invalid candidate id")
    raise HTTPException(404, "candidate not found")


@app.post("/api/v1/candidates/{candidate_id}/publish", response_model=PublicationDisabled)
def publish(candidate_id: str):
    # There is intentionally no signer, transaction provider, or write path.
    raise HTTPException(503, "publication is disabled: no authenticated operator, signer, or verified deployment")


@app.get("/api/v1/audit", response_model=AuditReport)
def audit():
    count, first, last = store.audit()
    return AuditReport(observation_count=count,
        first_timestamp=datetime.fromisoformat(first) if first else None,
        last_timestamp=datetime.fromisoformat(last) if last else None,
        source_configured=bool(settings.rpc_url), model_configured=bool(settings.model_artifact),
        deployments_configured=all((settings.registry_address, settings.reactive_address, settings.vault_address)))
