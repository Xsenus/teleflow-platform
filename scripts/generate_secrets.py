from __future__ import annotations

import base64
import secrets


def main() -> None:
    """Запустить the generate secrets command-line workflow. Arguments, exit status and user-
    visible diagnostics are handled here.
    """
    master = base64.urlsafe_b64encode(secrets.token_bytes(32)).decode("ascii")
    jwt_secret = secrets.token_urlsafe(64)
    password = secrets.token_urlsafe(24) + "!9aA"
    print(f"TELEFLOW_MASTER_KEY=base64:{master}")
    print(f"TELEFLOW_JWT_SECRET={jwt_secret}")
    print(f"TELEFLOW_BOOTSTRAP_ADMIN_PASSWORD={password}")
    print(f"POSTGRES_PASSWORD={secrets.token_urlsafe(32)}")
    print(f"REDIS_PASSWORD={secrets.token_urlsafe(32)}")
    print(f"GRAFANA_ADMIN_PASSWORD={secrets.token_urlsafe(32)}")


if __name__ == "__main__":
    main()
