"""Real MongoDB 8 validators/indexes and no-data-change metadata migration.

Run against compose.local-test.yaml; never use deployment credentials.
"""
from copy import deepcopy
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
import uuid

from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError, OperationFailure

ROOT = Path(__file__).resolve().parents[1]
module = importlib.util.spec_from_file_location("migration", ROOT / "scripts/migrate-newsmap-v2.py")
migration = importlib.util.module_from_spec(module)
module.loader.exec_module(migration)


def uid():
    return str(uuid.uuid4())


def report(key="a" * 64, reusable=True):
    now = datetime.now(timezone.utc)
    model = {"provider": "fixture", "name": "deterministic", "config_hash": "b" * 64}
    return {"schema_version": 2, "report_id": uid(), "title": "Fixture", "event_summary": "summary",
            "event_analysis": "analysis", "market_impact": "impact", "stock_impacts": [], "risk_factors": [],
            "requested_news_ids": ["fixture00001"], "used_evidence": [{"news_id": "fixture00001", "title": "Title",
                "source_name": "Source", "source_url": "https://example.invalid/fixture", "published_at": None,
                "body_id": uid(), "content_hash": "c" * 64, "input_char_count": 4, "truncated": False, "input_excerpt": "body"}],
            "failed_evidence": [], "coverage": {"requested_count": 1, "used_count": 1, "failed_count": 0},
            "rag_evidence": [], "verification": {"status": "unavailable", "notes": []}, "generation_key": key,
            "reuse_eligible": reusable, "generation": {"language": "ko", "report_type": "investment", "model": model,
                "prompt_version": "fixture", "preprocessing_version": "article-body-v1", "rag_version": "fixture", "input_budget_version": "fixture"},
            "created_at": now, "updated_at": now}


def legacy_report():
    return {"report_id": uid(), "title": "Legacy", "summary": "summary", "event_analysis": "analysis",
            "market_impact": "impact", "related_stocks": [], "evidence_news": [{"news_id": "fixture00001", "title": "Title"}],
            "risk_factors": [], "created_at": "2026-10-04T00:00:00Z"}


def legacy_strategy():
    return {"strategy_id": uid(), "expected_return": 1.2, "risk": "moderate", "period": "short",
            "strategy_summary": "summary", "strategy_items": [], "created_at": "2026-10-04T00:00:00Z"}


class MigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = MongoClient("mongodb://127.0.0.1:27218", serverSelectionTimeoutMS=5000)
        cls.client.admin.command("ping")

    @classmethod
    def tearDownClass(cls):
        cls.client.close()

    def setUp(self):
        self.db = self.client["test_newsmap_" + uuid.uuid4().hex]
        # Known v1 baseline, no new compatibility validator initially.
        self.db.create_collection("news", validator={"$jsonSchema": {"bsonType": "object", "required": ["title", "url", "source"], "properties": {"title": {"bsonType": "string"}, "url": {"bsonType": "string"}, "source": {"bsonType": "object"}}}})
        self.db.create_collection("reports", validator={}, validationAction="warn")
        self.db.create_collection("strategies", validator={}, validationAction="warn")
        self.temp = tempfile.TemporaryDirectory()
        self.snapshot = Path(self.temp.name) / "rollback.json"

    def tearDown(self):
        self.client.drop_database(self.db.name)  # isolated random test DB only
        self.temp.cleanup()

    def apply(self):
        migration.apply(self.db, self.snapshot)

    def rejected(self, collection, doc):
        with self.assertRaises(OperationFailure) as error:
            self.db[collection].insert_one(doc)
        self.assertEqual(error.exception.code, 121)

    def test_schema_sources_match(self):
        actual = subprocess.check_output(["node", "-e", "console.log(JSON.stringify(require('./scripts/lib/newsmap-v2-schema.js')))"], cwd=ROOT)
        self.assertEqual(json.loads(actual), migration.SPEC)

    def test_legacy_writes_and_new_v2_strict_validation(self):
        legacy = legacy_report()
        self.db.reports.insert_one(deepcopy(legacy))
        self.db.strategies.insert_one(legacy_strategy())
        self.apply()
        self.assertEqual(self.db.reports.find_one({"report_id": legacy["report_id"]})["created_at"], legacy["created_at"])
        self.db.reports.insert_one(legacy_report())
        self.db.strategies.insert_one(legacy_strategy())
        good = report()
        self.db.reports.insert_one(deepcopy(good))
        for mutation in (lambda d: d.pop("event_summary"), lambda d: d.update(schema_version=3),
                         lambda d: d.update(created_at="2026-10-04T00:00:00Z"),
                         lambda d: d.update(requested_news_ids=["n"] * 11),
                         lambda d: d["used_evidence"][0].update(published_at="2026-10-04"),
                         lambda d: d["verification"].update(status="made-up")):
            broken = deepcopy(good); broken["report_id"] = uid(); mutation(broken)
            self.rejected("reports", broken)
        self.rejected("reports", {"title": "not a legacy report"})
        self.rejected("strategies", {"strategy_id": uid(), "schema_version": 2, "strategy_summary": "old prototype"})
        self.rejected("reports", {**legacy_report(), "schema_version": 2})

    def test_ten_article_boundary_and_strategy_result_contract(self):
        self.apply()
        good = report()
        good["requested_news_ids"] = [f"fixture{i:05d}" for i in range(10)]
        evidence = good["used_evidence"][0]
        good["used_evidence"] = [{**deepcopy(evidence), "news_id": nid, "body_id": uid()} for nid in good["requested_news_ids"]]
        good["coverage"] = {"requested_count": 10, "used_count": 10, "failed_count": 0}
        self.db.reports.insert_one(good)
        strategy = {"schema_version": 2, "strategy_id": uid(), "report_id": good["report_id"],
            "report_generation_key": good["generation_key"], "risk_level": "medium", "period": "short",
            "strategy_summary": "summary", "risk_warning": "risk", "strategy_items": [{"ticker": None, "stock_name": "Name", "action": "watch", "reason": "reason"}],
            "generation_key": "e" * 64, "generation": {"model": good["generation"]["model"], "prompt_version": "strategy-v2"},
            "created_at": good["created_at"], "updated_at": good["updated_at"]}
        self.db.strategies.insert_one(deepcopy(strategy))
        for mutation in (lambda d: d.pop("report_id"), lambda d: d.update(risk_level="invented"),
                         lambda d: d["strategy_items"][0].update(action="up"),
                         lambda d: d.update(created_at="2026-10-04T00:00:00Z")):
            broken = deepcopy(strategy); broken["strategy_id"] = uid(); mutation(broken)
            self.rejected("strategies", broken)
        with self.assertRaises(DuplicateKeyError):
            self.db.strategies.insert_one({**strategy, "strategy_id": uid()})

    def test_partial_unique_cache_index_and_no_ttl(self):
        self.apply()
        self.db.reports.insert_one(report())
        with self.assertRaises(DuplicateKeyError):
            self.db.reports.insert_one(report())
        self.db.reports.insert_one(report(reusable=False))
        self.db.reports.insert_one(report(reusable=False))
        self.db.reports.insert_one(legacy_report())
        for name in ("news_bodies", "reports", "strategies"):
            self.assertFalse(any("expireAfterSeconds" in i for i in self.db[name].list_indexes()))
        self.assertEqual(self.db.reports.count_documents({"reuse_eligible": True}), 1)

    def test_body_ref_and_body_bson_constraints(self):
        self.apply()
        now = datetime.now(timezone.utc)
        body = {"schema_version": 2, "body_id": uid(), "news_id": "fixture00001", "source_url": "https://example.invalid/body",
                "content": "body", "content_hash": "d" * 64, "extractor_provider": "diffbot", "extractor_version": "article-body-v1", "extracted_at": now, "created_at": now}
        self.db.news_bodies.insert_one(deepcopy(body))
        self.rejected("news_bodies", {**body, "body_id": uid(), "content": ""})
        self.rejected("news_bodies", {**body, "body_id": uid(), "content_hash": "x" * 64})
        self.rejected("news_bodies", {**body, "body_id": uid(), "extracted_at": "2026-10-04"})
        self.rejected("news", {"title": "Fixture", "url": "https://example.invalid", "source": {"name": "Source"}, "body_ref": {"body_id": "invented"}})
        self.db.news.insert_one({"title": "Fixture", "url": "https://example.invalid", "source": {"name": "Source"},
            "body_ref": {k: body[k] for k in ("body_id", "content_hash", "source_url", "extractor_version", "extracted_at")}})
        with self.assertRaises(DuplicateKeyError):
            self.db.news_bodies.insert_one(deepcopy(body))

    def test_incompatible_old_v2_stops_before_metadata_mutation(self):
        self.db.reports.insert_one({"schema_version": 2, "report_id": uid(), "summary": "old incompatible format"})
        plan = migration.inspect(self.db)
        self.assertTrue(plan["blockers"])
        with self.assertRaises(ValueError):
            self.apply()
        self.assertFalse(self.snapshot.exists())
        self.assertEqual(self.db.command({"listCollections": 1, "filter": {"name": "reports"}})["cursor"]["firstBatch"][0]["options"]["validationAction"], "warn")

    def test_duplicate_and_index_option_conflict_stop_apply(self):
        self.db.reports.insert_many([report(), report()])
        self.assertTrue(any("duplicate groups" in v for v in migration.inspect(self.db)["blockers"]))
        with self.assertRaises(ValueError):
            self.apply()
        self.db.reports.delete_many({})
        self.db.reports.create_index("generation_key", name="uniq_reports_v2_generation")
        self.assertTrue(any("incompatible existing index" in v for v in migration.inspect(self.db)["blockers"]))
        with self.assertRaises(ValueError):
            self.apply()

    def test_rollback_restores_metadata_and_preserves_legacy_and_body(self):
        old = legacy_report()
        self.db.reports.insert_one(old)
        self.apply()
        self.assertEqual(self.snapshot.stat().st_mode & 0o777, 0o600)
        new = report()
        self.db.reports.insert_one(new)
        counts = {n: self.db[n].count_documents({}) for n in migration.COLLECTIONS}
        migration.rollback(self.db, self.snapshot)
        self.assertEqual(counts, {n: self.db[n].count_documents({}) for n in migration.COLLECTIONS})
        info = next(self.db.list_collections(filter={"name": "reports"}))
        self.assertEqual(info["options"]["validationAction"], "warn")
        self.assertEqual(info["options"].get("validator", {}), {})
        self.assertFalse(any(i["name"] == "uniq_reports_v2_generation" for i in self.db.reports.list_indexes()))


if __name__ == "__main__":
    unittest.main()
