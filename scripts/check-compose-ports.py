"""Reject Compose port mappings that expose anything except the HTTPS proxy."""

import json
import sys


config = json.load(sys.stdin)
violations = []
for service, definition in config["services"].items():
    for port in definition.get("ports", []):
        if (service == "caddy" and int(port["target"]) in {80, 443}
                and port.get("published") == str(port["target"])
                and port.get("protocol", "tcp") == "tcp"):
            continue
        if port.get("host_ip") != "127.0.0.1":
            violations.append(f"{service}:{port['target']} bound to {port.get('host_ip') or 'all interfaces'}")

if violations:
    print("Public Compose port bindings are not allowed:\n" + "\n".join(violations), file=sys.stderr)
    sys.exit(1)

print("Compose port bindings: OK")
