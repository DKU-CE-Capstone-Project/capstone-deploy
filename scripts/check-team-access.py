"""Exercise the actual Caddy auth rules against an isolated, local fake upstream.

No application services, production credentials, databases, or paid APIs are used.
"""
import base64
import json
import os
from pathlib import Path
import secrets
import subprocess
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
IMAGE = os.environ.get("CADDY_IMAGE", "caddy:2-alpine")


def docker(*args, **kwargs):
    return subprocess.run(["docker", *args], check=True, capture_output=True, text=True, **kwargs).stdout.strip()


def request(base, path, method="GET", auth=None):
    headers = {"Authorization": auth} if auth else {}
    req = Request(base + path, headers=headers, method=method)
    try:
        response = urlopen(req, timeout=5)
    except HTTPError as exc:
        response = exc
    with response:
        return response.status, response.headers, response.read().decode()


def main():
    password = secrets.token_urlsafe(24)
    hashed = docker("run", "--rm", "-i", IMAGE, "caddy", "hash-password",
                    "--bcrypt-cost", "10", input=password + "\n")
    with tempfile.TemporaryDirectory(prefix="econmind-auth-check-") as directory:
        temp = Path(directory)
        envfile = temp / "caddy.env"
        envfile.write_text(f"ECONMIND_TEAM_USER=fixture-team\nECONMIND_TEAM_PASSWORD_HASH={hashed}\n")
        envfile.chmod(0o600)
        # Preserve the production routes and handlers; change only local endpoints.
        source = (ROOT / "Caddyfile").read_text()
        config = source.replace("econmind.duckdns.org {", "http://:8080 {")
        config = config.replace("frontend:80", "127.0.0.1:8081")
        config = config.replace("http://35.216.13.110 {", "http://:8082 {")
        config += '\nhttp://:8081 {\n respond "upstream-authorization={http.request.header.Authorization}" 200\n}\n'
        (temp / "Caddyfile").write_text(config)
        mount = ["-v", f"{temp / 'Caddyfile'}:/etc/caddy/Caddyfile:ro"]
        docker("run", "--rm", "--env-file", str(envfile), *mount, IMAGE,
               "caddy", "validate", "--config", "/etc/caddy/Caddyfile")
        # Compose must refuse missing/empty credentials before starting Caddy.
        compose_env = {**os.environ, "MONGODB_URI": "mongodb://fixture:fixture@127.0.0.1/test",
                       "NAVER_CLIENT_ID": "fixture", "NAVER_CLIENT_SECRET": "fixture",
                       "DIFFBOT_TOKEN": "fixture", "GOOGLE_API_KEY": "fixture",
                       "ECONMIND_TEAM_USER": "fixture-team", "ECONMIND_TEAM_PASSWORD_HASH": hashed}
        for key in ("ECONMIND_TEAM_USER", "ECONMIND_TEAM_PASSWORD_HASH"):
            for value in (None, ""):
                missing_env = dict(compose_env)
                if value is None:
                    missing_env.pop(key)
                else:
                    missing_env[key] = value
                missing = subprocess.run(["docker", "compose", "--env-file", "/dev/null",
                                          "-f", "compose.server.yaml", "config", "-q"],
                                         cwd=ROOT, env=missing_env, capture_output=True, text=True)
                assert missing.returncode != 0, f"Missing/empty {key} accepted"
        dotenv = temp / "compose.env"
        dotenv.write_text(f"ECONMIND_TEAM_USER=fixture-team\nECONMIND_TEAM_PASSWORD_HASH='{hashed}'\n")
        dotenv.chmod(0o600)
        compose_env.pop("ECONMIND_TEAM_USER")
        compose_env.pop("ECONMIND_TEAM_PASSWORD_HASH")
        rendered = json.loads(docker("compose", "--env-file", str(dotenv), "-f", "compose.server.yaml",
                                     "config", "--format", "json", cwd=ROOT, env=compose_env))
        # Compose 5 escapes $ as $$ in reusable config output, but not container env.
        rendered_hash = rendered["services"]["caddy"]["environment"]["ECONMIND_TEAM_PASSWORD_HASH"]
        assert rendered_hash.replace("$$", "$") == hashed
        container = docker("run", "-d", "--rm", "--env-file", str(envfile), *mount,
                           "-p", "127.0.0.1::8080", IMAGE)
        try:
            ports = json.loads(docker("inspect", "--format", "{{json .NetworkSettings.Ports}}", container))
            base = "http://127.0.0.1:" + ports["8080/tcp"][0]["HostPort"]
            for attempt in range(40):
                try:
                    request(base, "/")
                    break
                except URLError:
                    if attempt == 39:
                        raise
                    time.sleep(0.1)
            wrong = "Basic " + base64.b64encode(b"fixture-team:wrong-password").decode()
            valid = "Basic " + base64.b64encode(f"fixture-team:{password}".encode()).decode()
            cases = [("GET", "/"), ("GET", "/assets/app.js"), ("GET", "/health"),
                     ("GET", "/ready"), ("GET", "/api/v2/session"),
                     ("POST", "/api/v2/news-maps"), ("POST", "/api/v2/report-jobs"),
                     ("POST", "/api/v2/strategy-jobs"), ("POST", "/analyze"),
                     ("GET", "/jobs/example"), ("OPTIONS", "/api/v2/session")]
            for method, path in cases:
                for auth in (None, wrong):
                    status, headers, _ = request(base, path, method, auth)
                    assert status == 401, (method, path, status)
                    assert headers.get("WWW-Authenticate", "").startswith("Basic ")
                status, _, body = request(base, path, method, valid)
                assert status == 200 and body == "upstream-authorization=", (method, path, status)
            print(f"Caddy: {len(cases)} UI/API/method cases block missing/wrong credentials; valid credentials reach upstream without Authorization")
            print("Caddy: syntax/provisioning valid; Compose refuses missing/empty credentials and preserves bcrypt $ characters")
        finally:
            docker("rm", "-f", container)


if __name__ == "__main__":
    main()
