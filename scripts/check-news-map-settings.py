"""Check all map defaults/overrides in both processes, without starting services.

Uses dummy credentials and --env-file /dev/null. Optionally compare the backend
example and Settings declarations via AST (no backend dependency installation).
"""
import argparse
import ast
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
OVERRIDES = {
    "NEWS_MAP_EMBEDDING_MODEL": "fixture-model",
    "NEWS_MAP_EMBEDDING_DIMENSIONS": "128",
    "NEWS_MAP_EMBEDDING_TASK_TYPE": "CLUSTERING",
    "NEWS_MAP_INITIAL_CANDIDATES": "15",
    "NEWS_MAP_MAX_CANDIDATES": "35",
    "NEWS_MAP_EMBEDDING_CONCURRENCY": "2",
    "NEWS_MAP_MIN_RELEVANCE": "0.7",
    "NEWS_MAP_KEYWORD_WEIGHT": "0.05",
    "NEWS_MAP_ENTITY_ONLY_PENALTY": "0.12",
    "NEWS_MAP_MMR_LAMBDA": "0.8",
    "NEWS_MAP_REPEAT_COSINE": "0.95",
    "NEWS_MAP_REPEAT_TEXT_SIMILARITY": "0.66",
    "NEWS_MAP_REPEAT_TITLE_SIMILARITY": "0.6",
    "NEWS_MAP_REPEAT_SHORT_TEXT_SIMILARITY": "0.91",
    "NEWS_MAP_REPEAT_MAX_HOURS": "24",
    "NEWS_MAP_REPEAT_DESCRIPTION_MIN_CHARS": "80",
    "NEWS_MAP_REPEAT_NOVELTY_RATIO": "0.1",
    "NEWS_MAP_SUPPLEMENT_MAX_SEARCHES": "1",
    "NEWS_MAP_SUPPLEMENT_PAGE_SIZE": "5",
    "NEWS_MAP_SUPPLEMENT_TIMEOUT_SECONDS": "10",
    "NEWS_MAP_SUPPLEMENT_CACHE_TTL_SECONDS": "30",
}


def example(path):
    return dict(line.split("=", 1) for line in path.read_text().splitlines()
                if line.startswith("NEWS_MAP_"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend-root", type=Path)
    args = parser.parse_args()
    defaults = example(ROOT / ".env.example")
    assert defaults.keys() == OVERRIDES.keys(), "Missing/unexpected map settings in example/checker"
    if args.backend_root:
        assert defaults == example(args.backend_root / ".env.example"), "Backend/deploy examples differ"
        declared = {}
        for node in ast.walk(ast.parse((args.backend_root / "app/config.py").read_text())):
            if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id.startswith("news_map_"):
                value = node.value
                if isinstance(value, ast.Call):
                    value = next(k.value for k in value.keywords if k.arg == "default")
                declared[node.target.id.upper()] = ast.literal_eval(value)
        assert declared.keys() == defaults.keys(), "Settings/example keys differ"
        for key, value in declared.items():
            assert (float(defaults[key]) == value if isinstance(value, (float, int)) else defaults[key] == value), key
        print(f"Backend Settings + both examples: {len(defaults)} settings OK")
    env = {k: v for k, v in os.environ.items() if not k.startswith("NEWS_MAP_")}
    env.update({
        "MONGODB_URI": "mongodb://fixture:fixture@127.0.0.1:27017/capstone_news?authSource=admin",
        "MONGODB_DB_NAME": "capstone_news", "MONGODB_ROOT_USERNAME": "fixture-root",
        "MONGODB_ROOT_PASSWORD": "fixture-root-password", "MONGODB_APP_USERNAME": "fixture-app",
        "MONGODB_APP_PASSWORD": "fixture-app-password", "NAVER_CLIENT_ID": "fixture-id",
        "NAVER_CLIENT_SECRET": "fixture-secret", "DIFFBOT_TOKEN": "fixture-diffbot",
        "GOOGLE_API_KEY": "fixture-google", "ANTHROPIC_API_KEY": "", "NEWSAPI_KEY": "",
        "APP_ORIGIN": "https://example.invalid",
    })
    combinations = [
        ["compose.server.yaml"], ["docker-compose.yaml"],
        ["docker-compose.yaml", "docker-compose.burst.yaml"],
        ["docker-compose.yaml", "compose.server.yaml"],
    ]
    for files in combinations:
        for label, expected in (("defaults", defaults), ("overrides", OVERRIDES)):
            command = ["docker", "compose", "--env-file", "/dev/null"]
            for filename in files:
                command.extend(["-f", filename])
            command.extend(["config", "--format", "json"])
            output = subprocess.run(command, cwd=ROOT, env={**env, **(expected if label == "overrides" else {})},
                                    capture_output=True, text=True, check=True).stdout
            config = json.loads(output)
            for service in ("econmind-api", "econmind-worker"):
                actual = config["services"][service]["environment"]
                assert {key for key in actual if key.startswith("NEWS_MAP_")} == expected.keys(), "Unexpected map setting"
                for key, value in expected.items():
                    assert str(actual.get(key)) == value, f"{' + '.join(files)} {label} {service} {key}"
            subprocess.run([sys.executable, str(ROOT / "scripts/check-compose-ports.py")],
                           input=output, text=True, capture_output=True, check=True)
            print(f"{' + '.join(files)} {label}: {len(expected)} settings x API/worker + ports OK")


if __name__ == "__main__":
    main()
