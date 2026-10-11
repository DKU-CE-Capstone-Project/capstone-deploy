#!/usr/bin/env python3
"""Compare backend v2 defaults and both examples; verify API/worker Compose."""
import argparse
import ast
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def example(path):
    return dict(line.split("=", 1) for line in path.read_text().splitlines()
                if line.startswith("V2_") or line.startswith("PROTOTYPE_TIER="))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-root", required=True, type=Path)
    args = parser.parse_args()
    defaults = example(ROOT / ".env.example")
    assert defaults == example(args.backend_root / ".env.example"), "Backend/deploy v2 example mismatch"
    declared = {}
    for node in ast.walk(ast.parse((args.backend_root / "app/config.py").read_text())):
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and (node.target.id.startswith("v2_") or node.target.id == "prototype_tier"):
            value = node.value
            if isinstance(value, ast.Call):
                value = next(k.value for k in value.keywords if k.arg == "default")
            declared[node.target.id.upper()] = ast.literal_eval(value)
    assert declared.keys() == defaults.keys()
    for key, value in declared.items():
        assert defaults[key].lower() == str(value).lower(), key
    env = {k: v for k, v in os.environ.items() if not k.startswith("V2_") and k != "PROTOTYPE_TIER"}
    env.update({"MONGODB_URI": "mongodb://fixture:fixture@127.0.0.1:27017/test_fixture?authSource=admin",
                "MONGODB_ROOT_USERNAME": "fixture", "MONGODB_ROOT_PASSWORD": "fixture",
                "MONGODB_APP_USERNAME": "fixture", "MONGODB_APP_PASSWORD": "fixture",
                "NAVER_CLIENT_ID": "fixture", "NAVER_CLIENT_SECRET": "fixture", "DIFFBOT_TOKEN": "fixture",
                "GOOGLE_API_KEY": "fixture", "APP_ORIGIN": "https://example.invalid"})
    overrides = {key: str(int(value) + 1) for key, value in defaults.items() if value.isdigit()}
    for files in (["compose.server.yaml"], ["docker-compose.yaml"], ["docker-compose.yaml", "docker-compose.burst.yaml"], ["docker-compose.yaml", "compose.server.yaml"]):
        for label, custom in (("defaults", {}), ("overrides", overrides)):
            command = ["docker", "compose", "--env-file", "/dev/null", "--profile", "*"]
            for file in files:
                command += ["-f", file]
            command += ["config", "--format", "json"]
            output = subprocess.check_output(command, env={**env, **custom}, cwd=ROOT, text=True)
            compose = json.loads(output)
            expected = {**defaults, **custom}
            for service in ("econmind-api", "econmind-worker", "econmind-v2-worker"):
                actual = compose["services"][service]["environment"]
                for key, value in expected.items():
                    assert str(actual[key]).lower() == value.lower(), f"{service}: {key}"
            subprocess.run(["python3", "scripts/check-compose-ports.py"], input=output, cwd=ROOT, text=True, check=True, capture_output=True)
            if files == ["compose.server.yaml"]:
                subprocess.run(["python3", "scripts/check-server-security.py"], input=output, cwd=ROOT, text=True, check=True, capture_output=True)
            print(f"{' + '.join(files)} {label}: {len(defaults)} v2 settings and ports OK")
    local = subprocess.check_output(["docker", "compose", "--env-file", "/dev/null", "-f", "compose.local-test.yaml", "config", "--format", "json"], cwd=ROOT, text=True)
    subprocess.run(["python3", "scripts/check-compose-ports.py"], input=local, cwd=ROOT, text=True, check=True)
    assert all("econmind-v2-local-test" in v["name"] for v in json.loads(local)["volumes"].values())
    print("Isolated local volumes and loopback ports: OK")


if __name__ == "__main__":
    main()
