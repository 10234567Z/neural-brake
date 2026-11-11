from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class StatusComponent(BaseModel):
    configured: bool
    available: bool
    detail: str


class HealthResponse(BaseModel):
    status: str
    components: dict[str, StatusComponent] = Field(default_factory=dict)


class PublicConfig(BaseModel):
    chain_id: int = Field(ge=1)
    pool_address: str
    token0_address: str
    token1_address: str
    fee: int
    pool_selection: str
    score_scale: str | None = None
    model_version: str | None = None
    threshold: int | None = None
    deployments: dict[str, str | None]
    publication_enabled: bool = False


class SwapObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    chain_id: int = Field(ge=1)
    pool_address: str
    block_number: int = Field(ge=0)
    block_hash: str
    transaction_hash: str
    log_index: int = Field(ge=0)
    sender: str
    recipient: str
    timestamp: datetime | None = None
    amount0: str
    amount1: str
    sqrt_price_x96: str
    liquidity: str
    tick: int


class SwapPage(BaseModel):
    items: list[SwapObservation]
    next_cursor: str | None


class AuditReport(BaseModel):
    observation_count: int
    first_timestamp: datetime | None
    last_timestamp: datetime | None
    source_configured: bool
    model_configured: bool
    deployments_configured: bool


class CandidateUnavailable(BaseModel):
    items: list[dict] = Field(default_factory=list)
    available: bool = False
    reason: str


class PublicationDisabled(BaseModel):
    state: str = "disabled"
    reason: str
