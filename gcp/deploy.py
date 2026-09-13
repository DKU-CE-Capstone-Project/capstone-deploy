#!/usr/bin/env python3
"""Pull public main revisions, require CI, build/test together, deploy or roll back."""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import urllib.parse
import urllib.request

ORG = "DKU-CE-Capstone-Project"
REPOS = {"backend": "capstone-backend", "frontend": "capstone-frontend",
         "news-logic": "capstone-news-logic", "deploy": "capstone-deploy"}
SERVICES = ["econmind-api", "econmind-worker", "frontend"]
ROOT = Path(os.environ.get("ECONMIND_CD_ROOT", "/home/econmind/econmind-cd"))
LIVE = Path(os.environ.get("ECONMIND_LIVE_ROOT", "/home/econmind/econmind-gcp-deploy"))
DOCKER = ["sudo", "-n", "docker"]


def run(*args, capture=False, timeout=1800):
    print("+", " ".join(map(str, args)), flush=True)
    return subprocess.run(list(map(str, args)), check=True, text=True,
                          stdout=subprocess.PIPE if capture else None,
                          timeout=timeout).stdout


def read(path, default=None):
    return json.loads(path.read_text()) if path.exists() else default


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2) + "\n")
    temporary.replace(path)


def json_url(url, data=None):
    request = urllib.request.Request(
        url, data=json.dumps(data).encode() if data is not None else None,
        headers={"Accept": "application/vnd.github+json" if "api.github.com" in url else "application/json",
                 "Content-Type": "application/json", "User-Agent": "econmind-deploy"})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.load(response)


def revisions():
    result = {}
    for key, repo in REPOS.items():
        output = run("git", "ls-remote", f"https://github.com/{ORG}/{repo}.git",
                     "refs/heads/main", capture=True, timeout=60)
        sha = output.split()[0]
        if len(sha) != 40 or any(c not in "0123456789abcdef" for c in sha):
            raise RuntimeError(f"Invalid main revision for {repo}")
        result[key] = sha
    return result


def ci_ready(refs):
    """Cache successful SHAs; pending/failing runs are checked at most every 5 min."""
    path = ROOT / "state/ci.json"
    cache = read(path, {})
    ready = True
    for key, sha in refs.items():
        cache_key = f"{REPOS[key]}:{sha}"
        saved = cache.get(cache_key, {})
        if saved.get("success"):
            continue
        if time.time() - saved.get("checked", 0) < 300:
            ready = False
            continue
        query = urllib.parse.urlencode({"branch": "main", "head_sha": sha,
                                        "event": "push", "per_page": 1})
        result = json_url(f"https://api.github.com/repos/{ORG}/{REPOS[key]}/actions/workflows/ci.yml/runs?{query}")
        runs = result.get("workflow_runs", [])
        success = bool(runs and runs[0].get("head_sha") == sha
                       and runs[0].get("status") == "completed"
                       and runs[0].get("conclusion") == "success")
        cache[cache_key] = {"checked": time.time(), "success": success}
        write(path, cache)
        print(f"CI {REPOS[key]} {sha[:12]}: {'passed' if success else 'waiting/failed'}", flush=True)
        ready = ready and success
    return ready


def release_id(refs):
    return hashlib.sha256(json.dumps(refs, sort_keys=True).encode()).hexdigest()[:20]


def export_sources(refs, directory):
    for key, sha in refs.items():
        cache = ROOT / "repos" / key
        if not cache.exists():
            cache.parent.mkdir(parents=True, exist_ok=True)
            run("git", "init", "--bare", cache)
        run("git", "-C", cache, "fetch", "--depth=1",
            f"https://github.com/{ORG}/{REPOS[key]}.git", sha, timeout=180)
        destination = directory / key
        destination.mkdir(parents=True, exist_ok=True)
        archive = directory / f"{key}.tar"
        run("git", "-C", cache, "archive", "--format=tar", f"--output={archive}", sha)
        run("tar", "-xf", archive, "-C", destination)
        archive.unlink()


def compose(override, *args):
    return run(*DOCKER, "compose", "--project-directory", LIVE, "-p", "econmind",
               "-f", LIVE / "compose.yaml", "-f", override, *args)


def override_file(path, backend, frontend):
    write(path, {"services": {"econmind-api": {"image": backend},
                             "econmind-worker": {"image": backend},
                             "frontend": {"image": frontend}}})


def baseline():
    current = read(ROOT / "state/current.json")
    if current:
        return current
    images = {}
    for service, name in [("econmind-api", "backend"), ("frontend", "frontend")]:
        container = run(*DOCKER, "compose", "--project-directory", LIVE,
                        "-f", LIVE / "compose.yaml", "ps", "-q", service, capture=True).strip()
        image = run(*DOCKER, "inspect", "--format={{.Image}}", container, capture=True).strip()
        tag = f"econmind-{name}-cd:initial"
        run(*DOCKER, "tag", image, tag)
        images[name] = tag
    override = ROOT / "state/initial-compose.json"
    override_file(override, images["backend"], images["frontend"])
    current = {"id": "initial", "revisions": {}, "images": images, "override": str(override)}
    write(ROOT / "state/current.json", current)
    return current


def smoke():
    base = "http://127.0.0.1:8080"
    health = json_url(base + "/health")
    if health.get("status") != "ok":
        raise RuntimeError("API health check failed")
    with urllib.request.urlopen(base + "/", timeout=15) as response:
        if b'<div id="root"' not in response.read():
            raise RuntimeError("Frontend HTML is missing")
    # Mock mode: also verify the API -> NATS -> worker -> Redis path without paid calls.
    mock = run(*DOCKER, "exec", "econmind-econmind-api-1", "python", "-c",
               "from app.config import settings; print(settings.mock_news_active)", capture=True).strip()
    if mock != "True":
        return
    search = json_url(base + "/api/v1/news/search?q=semiconductor&size=3")
    if not search.get("news_cards"):
        raise RuntimeError("News search returned no mock cards")
    job = json_url(base + "/jobs", {"keyword": "semiconductor"})
    for _ in range(30):
        result = json_url(base + "/jobs/" + job["job_id"])
        if result.get("status") == "done" and result.get("result", {}).get("articles"):
            return
        if result.get("status") == "error":
            raise RuntimeError("Worker smoke job failed")
        time.sleep(2)
    raise RuntimeError("Worker smoke job timed out")


def activate(record):
    # Recreate nginx after the API changes address, so its upstream resolves again.
    compose(record["override"], "up", "-d", "--no-build", "--no-deps",
            "--force-recreate", "--wait", "--wait-timeout", "180", "econmind-api")
    compose(record["override"], "up", "-d", "--no-build", "--no-deps",
            "--force-recreate", "--wait", "--wait-timeout", "90", "econmind-worker", "frontend")
    smoke()


def promote(record, previous):
    pending = ROOT / "state/pending.json"
    write(pending, {"previous": previous, "candidate": record})
    try:
        activate(record)
    except Exception:
        print("Deployment failed; restoring previous images", flush=True)
        activate(previous)
        pending.unlink()
        raise
    write(ROOT / "state/previous.json", previous)
    write(ROOT / "state/current.json", record)
    pending.unlink()


def cleanup(current, previous):
    protected = {current["id"], previous["id"], "initial"}
    manifests = sorted((ROOT / "releases").glob("*/release.json"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
    protected.update(read(p)["id"] for p in manifests[:3])
    for manifest in manifests:
        record = read(manifest)
        if record["id"] in protected:
            continue
        for image in record["images"].values():
            subprocess.run([*DOCKER, "image", "rm", image], check=False)
        shutil.rmtree(manifest.parent)


def deploy(retry=False):
    if (ROOT / "state/paused").exists():
        print("Automatic deployment is paused", flush=True)
        return
    pending = read(ROOT / "state/pending.json")
    if pending:
        activate(pending["previous"])
        write(ROOT / "state/current.json", pending["previous"])
        write(ROOT / "state/failed.json", {"id": pending["candidate"]["id"], "reason": "interrupted deployment"})
        (ROOT / "state/pending.json").unlink()
    refs = revisions()
    identity = release_id(refs)
    current = read(ROOT / "state/current.json", {})
    if refs == current.get("revisions"):
        print(f"Already deployed: {identity}", flush=True)
        return
    if not retry and read(ROOT / "state/failed.json", {}).get("id") == identity:
        print("This revision failed; waiting for a new commit or --retry", flush=True)
        return
    if not ci_ready(refs):
        return
    directory = ROOT / "releases" / identity
    try:
        if shutil.disk_usage(ROOT).free < 3 * 1024**3:
            raise RuntimeError("Less than 3 GiB disk space available for a build")
        if directory.exists():
            shutil.rmtree(directory)
        export_sources(refs, directory)
        images = {name: f"econmind-{name}-cd:{identity}" for name in ("backend", "frontend")}
        dockerfile = directory / "deploy/gcp/Dockerfile.backend"
        run(*DOCKER, "build", "--target", "test", "-f", dockerfile, directory)
        run(*DOCKER, "build", "--target", "runtime", "-t", images["backend"], "-f", dockerfile, directory)
        run(*DOCKER, "build", "--build-arg", "VITE_API_BASE=", "-t", images["frontend"], directory / "frontend")
        override = directory / "compose-images.json"
        override_file(override, images["backend"], images["frontend"])
        compose(override, "config", "-q")
        if revisions() != refs:
            print("main advanced during build; the next run will build the new snapshot", flush=True)
            return
        previous = baseline()
        record = {"id": identity, "revisions": refs, "images": images,
                  "override": str(override), "deployed_at": time.time()}
        write(directory / "release.json", record)
        promote(record, previous)
    except Exception as error:
        write(ROOT / "state/failed.json", {"id": identity, "reason": str(error), "at": time.time()})
        raise
    (ROOT / "state/failed.json").unlink(missing_ok=True)
    print(f"Deployed successfully: {identity}", flush=True)
    cleanup(record, previous)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--retry", action="store_true", help="Retry a failed snapshot after CI passes")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--rollback", action="store_true", help="Roll back and pause auto deployment")
    args = parser.parse_args()
    (ROOT / "state").mkdir(parents=True, exist_ok=True)
    with (ROOT / "state/lock").open("w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("A deployment is already running", flush=True)
            return
        if args.status:
            print(json.dumps({"current": read(ROOT / "state/current.json"),
                              "failed": read(ROOT / "state/failed.json"),
                              "paused": (ROOT / "state/paused").exists()}, indent=2))
        elif args.rollback:
            previous = read(ROOT / "state/previous.json")
            if not previous:
                raise RuntimeError("No previous release")
            (ROOT / "state/paused").touch()
            promote(previous, baseline())
        else:
            deploy(args.retry)


if __name__ == "__main__":
    main()
