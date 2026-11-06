"""Read the independent Compose collectors using their private HTTP APIs."""

import os

import requests
from sources import connection_status, iso, now


def read(session, url, params=None):
    response = session.get(url, params=params, timeout=(2, 5), allow_redirects=False)
    response.raise_for_status()
    return response.json()


class LocalSource:
    def __init__(self):
        self.host = os.getenv("MONITOR_HOST_NAME", "Docker 호스트")
        self.status_url = os.getenv("LOCAL_BACKEND_STATUS_URL", "")
        self.status_token = os.getenv("ADMIN_STATUS_TOKEN", "")

    def job_usage(self, job_id):
        with requests.Session() as session:
            session.trust_env = False
            return read(
                session, "http://usage-store:8080/report-job-usage", {"job_id": job_id}
            )

    def snapshot(self, hours):
        with requests.Session() as session:
            session.trust_env = False
            try:
                metrics = read(session, "http://host-metrics:8080/metrics")
            except (requests.RequestException, ValueError):
                metrics = {
                    name: {"state": "unavailable", "series": []}
                    for name in ("cpu", "memory", "disk")
                }
            try:
                usage = read(session, "http://usage-store:8080/usage", {"hours": hours})
            except (requests.RequestException, ValueError):
                usage = {"state": "unavailable"}
            db = connection_status(self.status_url, self.status_token, get=session.get)
        return {
            "mode": "local",
            "instance": self.host,
            "updated_at": iso(now()),
            "hours": hours,
            "metrics": metrics,
            "usage": usage,
            "connection": db,
        }

    def cost_snapshot(self, gcp_period="all", external_period="all"):
        with requests.Session() as session:
            session.trust_env = False
            try:
                external = read(
                    session,
                    "http://usage-store:8080/costs",
                    {"period": external_period},
                )
            except (requests.RequestException, ValueError):
                external = {"state": "unavailable"}
        return {
            "mode": "local",
            "instance": self.host,
            "updated_at": iso(now()),
            "gcp_period": gcp_period,
            "external_period": external_period,
            "gcp": {"state": "not_configured"},
            "external": external,
        }
