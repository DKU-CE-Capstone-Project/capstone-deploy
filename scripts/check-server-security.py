"""Fail CI when server Compose drops required backend security controls."""

import json
import sys


def main() -> None:
    compose = json.load(sys.stdin)
    services = compose["services"]
    expected = {
        "SESSION_STORE_REQUIRED": "true",
        "SESSION_COOKIE_SECURE": "true",
        "SESSION_COOKIE_SAMESITE": "lax",
        "PAID_DEMO_ENABLED": "false",
    }
    for name in ("econmind-api", "econmind-worker"):
        env = services[name]["environment"]
        for key, value in expected.items():
            assert str(env.get(key, "")).lower() == value, f"{name}: {key} must be {value}"

    health = services["econmind-api"]["healthcheck"]["test"]
    assert "d.get('redis')=='on'" in " ".join(health), "API readiness must check Redis"
    print("Server session, cookie, PAID gate, and Redis readiness settings: OK")


if __name__ == "__main__":
    main()
