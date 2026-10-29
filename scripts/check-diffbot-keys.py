"""Verify optional Diffbot key delivery using dummy credentials, without services."""
import argparse
import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-root", required=True, type=Path)
    args = parser.parse_args()
    for root in (ROOT, args.backend_root):
        lines = (root / ".env.example").read_text().splitlines()
        assert "DIFFBOT_TOKEN_SECONDARY=" in lines
    env = {key: value for key, value in os.environ.items() if not key.startswith("DIFFBOT_")}
    env.update({
        "MONGODB_URI": "mongodb://fixture:fixture@127.0.0.1:27017/test_fixture?authSource=admin",
        "MONGODB_ROOT_USERNAME": "fixture", "MONGODB_ROOT_PASSWORD": "fixture",
        "MONGODB_APP_USERNAME": "fixture", "MONGODB_APP_PASSWORD": "fixture",
        "NAVER_CLIENT_ID": "fixture", "NAVER_CLIENT_SECRET": "fixture",
        "DIFFBOT_TOKEN": "fixture-primary", "GOOGLE_API_KEY": "fixture",
        "ECONMIND_TEAM_USER": "fixture-team", "ECONMIND_TEAM_PASSWORD_HASH": "fixture-hash",
    })
    combinations = [
        ["compose.server.yaml"], ["docker-compose.yaml"],
        ["docker-compose.yaml", "docker-compose.burst.yaml"],
        ["docker-compose.yaml", "compose.server.yaml"],
    ]
    for files in combinations:
        for secondary in ("", "fixture-secondary"):
            command = ["docker", "compose", "--env-file", "/dev/null", "--profile", "*"]
            for file in files:
                command.extend(["-f", file])
            command.extend(["config", "--format", "json"])
            output = subprocess.check_output(command, cwd=ROOT, env={**env, "DIFFBOT_TOKEN_SECONDARY": secondary}, text=True)
            compose = json.loads(output)
            for service in ("econmind-api", "econmind-worker", "econmind-v2-worker"):
                actual = compose["services"][service]["environment"]
                assert actual["DIFFBOT_TOKEN"] == "fixture-primary", service
                assert actual["DIFFBOT_TOKEN_SECONDARY"] == secondary, service
            print(f"{' + '.join(files)}: optional secondary {'set' if secondary else 'empty'}; API/v1/v2 delivery OK")


if __name__ == "__main__":
    main()
