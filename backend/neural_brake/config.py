"""Runtime configuration. Public candidate addresses are provisional, not verified deployments."""

from dataclasses import dataclass
import os


PROVISIONAL_CHAIN_ID = 84532
PROVISIONAL_POOL = "0x46880b404CD35c165EDdefF7421019F8dD25F4Ad"
PROVISIONAL_TOKEN0 = "0x036CbD53842c5426634e7929541eC2318f3dCF7e"
PROVISIONAL_TOKEN1 = "0x4200000000000000000000000000000000000006"


@dataclass(frozen=True)
class Settings:
    rpc_url: str | None
    database_path: str
    chain_id: int
    pool_address: str
    token0_address: str
    token1_address: str
    token0_decimals: int
    token1_decimals: int
    fee: int
    model_artifact: str | None
    registry_address: str | None
    reactive_address: str | None
    vault_address: str | None

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            rpc_url=os.getenv("NB_RPC_URL") or None,
            database_path=os.getenv("NB_DATABASE_PATH", "./neural_brake.sqlite3"),
            chain_id=int(os.getenv("NB_CHAIN_ID", str(PROVISIONAL_CHAIN_ID))),
            pool_address=os.getenv("NB_POOL_ADDRESS", PROVISIONAL_POOL),
            token0_address=os.getenv("NB_TOKEN0_ADDRESS", PROVISIONAL_TOKEN0),
            token1_address=os.getenv("NB_TOKEN1_ADDRESS", PROVISIONAL_TOKEN1),
            token0_decimals=int(os.getenv("NB_TOKEN0_DECIMALS", "6")),
            token1_decimals=int(os.getenv("NB_TOKEN1_DECIMALS", "18")),
            fee=int(os.getenv("NB_POOL_FEE", "3000")),
            model_artifact=os.getenv("NB_MODEL_ARTIFACT") or None,
            registry_address=os.getenv("NB_SCORE_REGISTRY_ADDRESS") or None,
            reactive_address=os.getenv("NB_REACTIVE_ADDRESS") or None,
            vault_address=os.getenv("NB_VAULT_ADDRESS") or None,
        )
