from __future__ import annotations

import argparse
import sys

import httpx


def main() -> int:
    """Запустить the smoke test command-line workflow. Arguments, exit status and user-visible
    diagnostics are handled here.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://localhost:8080")
    parser.add_argument("--email", default="admin@example.com")
    parser.add_argument("--password", default="ChangeMe_123456!")
    args = parser.parse_args()

    with httpx.Client(base_url=args.base_url, timeout=15.0, follow_redirects=True) as client:
        ready = client.get("/api/v1/health/ready")
        ready.raise_for_status()
        login = client.post(
            "/api/v1/auth/login",
            json={"email": args.email, "password": args.password},
        )
        login.raise_for_status()
        me = client.get("/api/v1/auth/me")
        me.raise_for_status()
        csrf = client.cookies.get("teleflow_csrf")
        if not csrf:
            raise RuntimeError("CSRF cookie not issued")
        summary = client.get("/api/v1/dashboard/summary")
        summary.raise_for_status()
        logout = client.post("/api/v1/auth/logout", headers={"X-CSRF-Token": csrf})
        logout.raise_for_status()
        print("Smoke test passed:", me.json()["email"], summary.json())
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"Smoke test failed: {exc}", file=sys.stderr)
        raise
