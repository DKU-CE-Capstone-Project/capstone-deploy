#!/usr/bin/env python3
"""Inspect/apply/rollback v2 MongoDB metadata on an explicitly isolated local DB.

No document is changed or deleted. Unknown historical result shapes must be
investigated separately; this tool never invents provenance or generation keys.
Requires pymongo (available in the backend project virtualenv).
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from bson import json_util
from pymongo import MongoClient

ROOT = Path(__file__).resolve().parents[1]
SPEC = json.loads((ROOT / "scripts/lib/newsmap-v2-schema.json").read_text())
COLLECTIONS = ("news", "news_bodies", "reports", "strategies")


def new_validators(database):
    info = database.list_collections(filter={"name": "news"}).next()
    validator = info.get("options", {}).get("validator", {})
    if "$jsonSchema" not in validator or validator["$jsonSchema"].get("bsonType") != "object":
        raise ValueError("Unknown news validator: initialize known v1 schema before migration")
    validator["$jsonSchema"].setdefault("properties", {})["body_ref"] = SPEC["body_ref"]
    return {"news": validator, **SPEC["validators"]}


def same_index(actual, expected):
    if dict(actual["key"]) != expected["key"]:
        return False
    options = expected["options"]
    return (bool(actual.get("unique", False)) == bool(options.get("unique", False))
            and bool(actual.get("sparse", False)) == bool(options.get("sparse", False))
            and actual.get("partialFilterExpression") == options.get("partialFilterExpression")
            and "expireAfterSeconds" not in actual)


def inspect(database):
    validators = new_validators(database)
    existing = set(database.list_collection_names())
    blockers = []
    counts = {}
    for name, validator in validators.items():
        total = database[name].count_documents({})
        invalid = database[name].count_documents({"$nor": [validator]}) if name in existing else 0
        counts[name] = {"total": total, "incompatible": invalid}
        if invalid:
            blockers.append(f"{name}: {invalid} incompatible documents")
    create_indexes = []
    for spec in SPEC["indexes"]:
        name = spec["collection"]
        actual = list(database[name].list_indexes()) if name in existing else []
        equivalent = next((i for i in actual if same_index(i, spec)), None)
        collision = next((i for i in actual if i["name"] == spec["options"]["name"] and not same_index(i, spec)), None)
        key_conflict = next((i for i in actual if dict(i["key"]) == spec["key"] and not same_index(i, spec)), None)
        if collision or key_conflict:
            blockers.append(f"{name}: incompatible existing index { (collision or key_conflict)['name'] }")
        if spec["options"].get("unique"):
            field = next(iter(spec["key"]))
            query = dict(spec["options"].get("partialFilterExpression", {}))
            if spec["options"].get("sparse"):
                query[field] = {"$exists": True}
            duplicates = list(database[name].aggregate([
                {"$match": query}, {"$group": {"_id": f"${field}", "count": {"$sum": 1}}},
                {"$match": {"count": {"$gt": 1}}}, {"$count": "groups"},
            ]))
            if duplicates:
                blockers.append(f"{name}: {duplicates[0]['groups']} duplicate groups for {field}")
        if not equivalent:
            create_indexes.append(spec)
    return {"database": database.name, "counts": counts, "blockers": blockers,
            "indexes_to_create": create_indexes, "validators": validators,
            "data_changes": 0, "legacy_promotions": 0}


def write_snapshot(path, database, plan):
    existing = set(database.list_collection_names())
    metadata = {}
    for name in COLLECTIONS:
        info = next(database.list_collections(filter={"name": name}), None)
        metadata[name] = {"exists": name in existing, "options": info.get("options", {}) if info else {},
                          "indexes": list(database[name].list_indexes()) if info else [],
                          "count": database[name].count_documents({})}
    snapshot = {"schema_version": 1, "database": database.name,
                "created_at": datetime.now(timezone.utc), "collections": metadata,
                "added_indexes": plan["indexes_to_create"], "data_changes": 0}
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, "w") as file:
        file.write(json_util.dumps(snapshot, indent=2))
    return snapshot


def apply(database, snapshot_path):
    plan = inspect(database)
    if plan["blockers"]:
        raise ValueError("Migration blocked: " + "; ".join(plan["blockers"]))
    write_snapshot(snapshot_path, database, plan)
    existing = set(database.list_collection_names())
    # Validators first. A concurrent incompatible writer makes the apply fail.
    # Stop all API/worker writes during any real metadata migration.
    for name, validator in plan["validators"].items():
        options = {"validator": validator, "validationLevel": "strict", "validationAction": "error"}
        if name in existing:
            database.command({"collMod": name, **options})
        else:
            database.create_collection(name, **options)
    for spec in plan["indexes_to_create"]:
        database[spec["collection"]].create_index(list(spec["key"].items()), **spec["options"])
    final = inspect(database)
    if final["blockers"] or final["indexes_to_create"]:
        raise ValueError("Post-apply verification failed; retain snapshot and use rollback")
    return final


def rollback(database, snapshot_path):
    snapshot = json_util.loads(snapshot_path.read_text())
    if snapshot["database"] != database.name:
        raise ValueError("Snapshot belongs to a different database")
    # Preserve every document and collection, including any newly created body DB.
    # Remove only indexes introduced by this apply; restore previous validators.
    for spec in snapshot["added_indexes"]:
        indexes = list(database[spec["collection"]].list_indexes())
        entry = next((i for i in indexes if i["name"] == spec["options"]["name"]), None)
        if entry:
            if not same_index(entry, spec):
                raise ValueError("Index changed since migration; rollback refused")
            database[spec["collection"]].drop_index(entry["name"])
    for name, old in snapshot["collections"].items():
        if name not in database.list_collection_names():
            continue
        options = old["options"]
        database.command({"collMod": name, "validator": options.get("validator", {}),
                          "validationLevel": options.get("validationLevel", "strict"),
                          "validationAction": options.get("validationAction", "error")})
    return {"database": database.name, "rolled_back_metadata": True, "data_changes": 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("dry-run", "apply", "rollback"), nargs="?", default="dry-run")
    parser.add_argument("--uri-env", default="MONGODB_URI", help="Environment variable; URI is never printed")
    parser.add_argument("--database", default="econmind_v2_local_test")
    parser.add_argument("--snapshot", type=Path, help="Mandatory metadata rollback snapshot for apply/rollback")
    args = parser.parse_args()
    uri = os.environ.get(args.uri_env, "")
    host = urlsplit(uri).hostname
    if host not in {"localhost", "127.0.0.1", "::1"} or uri.startswith("mongodb+srv:"):
        parser.error("This local migration only accepts an explicit loopback MongoDB URI")
    if not (args.database.endswith("_local_test") or args.database.startswith("test_")):
        parser.error("Use a separate *_local_test or test_* database")
    if args.action != "dry-run" and not args.snapshot:
        parser.error("--snapshot is required for apply and rollback")
    client = MongoClient(uri, serverSelectionTimeoutMS=5000, connectTimeoutMS=5000)
    try:
        client.admin.command("ping")
        database = client[args.database]
        result = (inspect(database) if args.action == "dry-run" else
                  apply(database, args.snapshot) if args.action == "apply" else
                  rollback(database, args.snapshot))
        # Logs carry only counts, collection/index names, and metadata (no documents/URI).
        result.pop("validators", None)
        print(json.dumps(result, indent=2))
        if result.get("blockers"):
            raise SystemExit(2)
    except Exception as exc:
        print(f"Migration stopped: {type(exc).__name__}")
        # Drivers can embed credentials and document contents in exception messages.
        raise SystemExit(1) from None
    finally:
        client.close()


if __name__ == "__main__":
    main()
