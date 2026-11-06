"""Use VM collectors for telemetry and Cloud Billing for GCP credits."""

import os
from ipaddress import ip_address
from urllib.parse import urlsplit

import requests
from local_source import read
from sources import CloudSource, iso, now


class VmSource(CloudSource):
    def __init__(self):
        super().__init__()
        self.collector = os.environ["VM_COLLECTOR_URL"].rstrip("/")
        self.collector_token = os.environ["VM_COLLECTOR_TOKEN"]
        url = urlsplit(self.collector)
        if (
            url.scheme != "http"
            or not url.hostname
            or not ip_address(url.hostname).is_private
            or url.path
            or url.query
            or url.fragment
            or url.username is not None
            or len(self.collector_token) < 32
        ):
            raise ValueError("Expected a private collector URL and a strong token")

    def collector_data(self, path, params):
        with requests.Session() as session:
            session.trust_env = False
            session.headers["Authorization"] = "Bearer " + self.collector_token
            data = read(session, self.collector + path, params)
        if not isinstance(data, dict):
            raise ValueError("Invalid collector response")
        return data

    def snapshot(self, hours):
        try:
            data = self.collector_data("/api/overview", {"hours": hours})
            metrics, usage = data["metrics"], data["usage"]
        except (requests.RequestException, ValueError, KeyError):
            metrics = {
                name: {"state": "unavailable", "series": []}
                for name in ("cpu", "memory", "disk")
            }
            usage = {"state": "unavailable"}
        return {
            "mode": "vm",
            "project": self.project,
            "instance": self.instance,
            "updated_at": iso(now()),
            "hours": hours,
            "metrics": metrics,
            "usage": usage,
            "connection": self.connection(),
        }

    def external_costs(self, _session, period):
        data = self.collector_data("/api/costs", {"external_period": period["period"]})
        external = data.get("external")
        if not isinstance(external, dict):
            raise ValueError("Invalid collector cost response")
        return external | period

    def cost_snapshot(self, gcp_period="all", external_period="all"):
        return super().cost_snapshot(gcp_period, external_period) | {"mode": "vm"}
