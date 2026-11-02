"""Bounded, project-filtered Cloud Billing export queries."""

import math
from datetime import datetime, timedelta, timezone
from decimal import Decimal, DecimalException

import requests

KST = timezone(timedelta(hours=9))


def query_billing(session, *, project, table, location, timestamp, period_start=None):
    local = timestamp.astimezone(KST)
    start = period_start or local.replace(
        day=1, hour=0, minute=0, second=0, microsecond=0
    )
    base = {
        "period_start": start.isoformat(),
        "period_end": timestamp.isoformat(),
        "month": local.strftime("%Y-%m"),
        "time_zone": "Asia/Seoul",
        "source": "bigquery",
        "report_url": f"https://console.cloud.google.com/billing?project={project}",
    }
    if not table:
        return base | {"state": "not_configured"}
    query = f"""
        SELECT service.description AS service, currency,
          SUM(CAST(cost AS NUMERIC)) AS gross_cost,
          SUM(IFNULL((SELECT SUM(CAST(c.amount AS NUMERIC)) FROM UNNEST(credits) c), 0)) AS credits,
          FORMAT_TIMESTAMP('%Y-%m-%dT%H:%M:%SZ', MAX(usage_end_time), 'UTC') AS data_through,
          FORMAT_TIMESTAMP('%Y-%m-%dT%H:%M:%SZ', MAX(export_time), 'UTC') AS last_export,
          COUNT(*) AS records
        FROM `{table}`
        WHERE project.id = @project
          AND usage_start_time >= @period_start AND usage_start_time < @period_end
        GROUP BY service, currency ORDER BY gross_cost DESC
    """
    parameters = [
        {
            "name": name,
            "parameterType": {"type": kind},
            "parameterValue": {"value": value},
        }
        for name, kind, value in [
            ("project", "STRING", project),
            ("period_start", "TIMESTAMP", start.isoformat()),
            ("period_end", "TIMESTAMP", timestamp.isoformat()),
        ]
    ]
    try:
        response = session.post(
            f"https://bigquery.googleapis.com/bigquery/v2/projects/{project}/queries",
            json={
                "query": query,
                "useLegacySql": False,
                "parameterMode": "NAMED",
                "queryParameters": parameters,
                "location": location,
                "maximumBytesBilled": str(128 * 2**20),
                "maxResults": 1000,
                "timeoutMs": 10000,
            },
            timeout=(3, 15),
        )
        response.raise_for_status()
        data = response.json()
        for _ in range(3):
            if data.get("jobComplete"):
                break
            job = data["jobReference"]
            response = session.get(
                f"https://bigquery.googleapis.com/bigquery/v2/projects/{project}/queries/{job['jobId']}",
                params={"location": location, "maxResults": 1000, "timeoutMs": 1000},
                timeout=(3, 8),
            )
            response.raise_for_status()
            data = response.json()
        if not data.get("jobComplete"):
            return base | {"state": "query_pending"}
        if data.get("errors") or data.get("pageToken"):
            return base | {"state": "unavailable"}
        rows = []
        fields = [field["name"] for field in data.get("schema", {}).get("fields", [])]
        for row in data.get("rows", []):
            values = dict(
                zip(fields, [cell.get("v") for cell in row["f"]], strict=True)
            )
            gross, credits = Decimal(values["gross_cost"]), Decimal(values["credits"])
            if not gross.is_finite() or not credits.is_finite():
                raise ValueError("Non-finite billing amount")
            rows.append(
                {
                    "service": values["service"],
                    "currency": values["currency"],
                    "gross_cost": float(gross),
                    "credits": float(credits),
                    "net_cost": float(gross + credits),
                    "records": int(values["records"]),
                    "data_through": values["data_through"],
                    "last_export": values["last_export"],
                }
            )
        if not rows:
            return base | {"state": "no_data"}
        currencies = {row["currency"] for row in rows}
        if len(currencies) != 1:
            return base | {"state": "mixed_currency", "services": rows}
        gross = sum((Decimal(str(row["gross_cost"])) for row in rows), Decimal(0))
        credits = sum((Decimal(str(row["credits"])) for row in rows), Decimal(0))
        return base | {
            "state": "ok",
            "currency": currencies.pop(),
            "gross_cost": float(gross),
            "credits": float(credits),
            "net_cost": float(gross + credits),
            "services": rows,
            "data_through": max(row["data_through"] for row in rows),
            "last_export": max(row["last_export"] for row in rows),
        }
    except requests.HTTPError as error:
        status = error.response.status_code if error.response is not None else None
        return base | {
            "state": {404: "export_pending", 403: "permission_required"}.get(
                status, "unavailable"
            )
        }
    except (
        requests.RequestException,
        ValueError,
        KeyError,
        TypeError,
        DecimalException,
    ):
        return base | {"state": "unavailable"}


def with_snapshot(result, snapshot, *, project, timestamp):
    """Use an explicitly dated console report while the first export is pending."""
    if result["state"] == "ok" or not snapshot:
        return result
    try:
        if snapshot["project"] != project or snapshot["month"] != timestamp.astimezone(
            KST
        ).strftime("%Y-%m"):
            return result
        month_start = timestamp.astimezone(KST).replace(
            day=1, hour=0, minute=0, second=0, microsecond=0
        )
        if (
            result.get("period_start")
            and datetime.fromisoformat(result["period_start"]) != month_start
        ):
            return result
        for key in ("gross_cost", "credits", "net_cost"):
            if not math.isfinite(snapshot[key]):
                return result
        if not math.isclose(
            snapshot["gross_cost"] + snapshot["credits"],
            snapshot["net_cost"],
            abs_tol=0.000001,
        ):
            return result
        return (
            snapshot
            | {
                key: result[key]
                for key in ("period_start", "period_end")
                if key in result
            }
            | {
                "state": "snapshot",
                "source": "console_snapshot",
                "automatic_state": result["state"],
                "report_url": result["report_url"],
            }
        )
    except (KeyError, TypeError, ValueError):
        return result
