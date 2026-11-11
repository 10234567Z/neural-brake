"""Report actual local cache extent; no sufficiency threshold is imposed."""

import json

from .config import Settings
from .storage import Store


def main() -> None:
    settings = Settings.from_env()
    count, first, last = Store(settings.database_path).audit()
    print(json.dumps({"observation_count": count, "first_timestamp": first, "last_timestamp": last,
        "rpc_configured": bool(settings.rpc_url), "model_artifact_configured": bool(settings.model_artifact),
        "deployments_configured": all((settings.registry_address, settings.reactive_address, settings.vault_address)),
        "threshold": None}, indent=2))


if __name__ == "__main__":
    main()
