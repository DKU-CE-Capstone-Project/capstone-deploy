"""Fail CI when server Compose drops required backend security controls."""

import json
from ipaddress import ip_address, ip_network
from pathlib import Path
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
        for key in (
            "SESSION_ISSUE_CLIENT_HOURLY_LIMIT", "SESSION_ISSUE_GLOBAL_HOURLY_LIMIT",
            "JOBS_CLIENT_HOURLY_LIMIT", "REPORTS_CLIENT_HOURLY_LIMIT",
            "STRATEGIES_CLIENT_HOURLY_LIMIT", "ANALYZE_CLIENT_HOURLY_LIMIT",
        ):
            assert int(env.get(key, 0)) > 0, f"{name}: {key} must be positive"

    health = services["econmind-api"]["healthcheck"]["test"]
    assert "d.get('redis')=='on'" in " ".join(health), "API readiness must check Redis"

    proxy_net = ip_network(compose["networks"]["proxy"]["ipam"]["config"][0]["subnet"])
    caddy_ip = services["caddy"]["networks"]["proxy"]["ipv4_address"]
    frontend_ip = services["frontend"]["networks"]["proxy"]["ipv4_address"]
    assert ip_address(caddy_ip) in proxy_net and ip_address(frontend_ip) in proxy_net
    assert caddy_ip != frontend_ip
    assert "proxy" in services["econmind-api"]["networks"]
    assert not services["frontend"].get("ports"), "frontend must not have a direct host port"
    command = services["econmind-api"]["command"]
    assert f"--forwarded-allow-ips={frontend_ip}" in command
    assert "--forwarded-allow-ips=*" not in command

    caddyfile = Path("Caddyfile").read_text()
    nginx = Path("nginx.server.conf").read_text()
    assert "header_up X-Forwarded-For {http.request.remote.host}" in caddyfile
    assert f"set_real_ip_from {caddy_ip};" in nginx
    assert "real_ip_header X-Forwarded-For;" in nginx
    assert f"{caddy_ip} $http_x_forwarded_proto;" in nginx
    assert "default $scheme;" in nginx
    assert "proxy_set_header X-Forwarded-For $remote_addr;" in nginx
    assert "$proxy_add_x_forwarded_for" not in nginx
    print("Server session, cookie, PAID gate, Redis readiness, and proxy trust: OK")


if __name__ == "__main__":
    main()
