"""Read-only, deduplicated cost history from the durable usage archive."""

import json

import requests
from pricing import COST_FIELDS

MAX_GROUPS = 10000


def query_history(session, *, project, table, location, start, end, exclude_ids):
    # Only named pricing inputs participate in groups. IDs are deduplicated
    # before grouping; current log-tail IDs are excluded to avoid double counts.
    signature = ", ".join(
        f"'{field}', JSON_QUERY(payload, '$.{field}')" for field in COST_FIELDS
    )
    query = f"""
      WITH unique_events AS (
        SELECT payload, JSON_VALUE(payload, '$.event_id') AS event_id
        FROM `{table}`
        WHERE event_time >= @start AND event_time < @end
          AND JSON_VALUE(payload, '$.event_type') = 'econmind_usage'
          AND JSON_VALUE(payload, '$.schema_version') = '1'
          AND JSON_VALUE(payload, '$.kind') IN ('sdk_call','http_attempt','cache_hit')
          AND JSON_VALUE(payload, '$.event_id') IS NOT NULL
        QUALIFY ROW_NUMBER() OVER (
          PARTITION BY JSON_VALUE(payload, '$.event_id') ORDER BY received_at DESC
        ) = 1
      )
      SELECT TO_JSON_STRING(JSON_OBJECT({signature})) AS signature,
             COUNT(*) AS occurrences
      FROM unique_events WHERE event_id NOT IN UNNEST(@exclude_ids)
      GROUP BY signature LIMIT {MAX_GROUPS + 1}
    """
    parameters = [
        {
            "name": name,
            "parameterType": {"type": "TIMESTAMP"},
            "parameterValue": {"value": value},
        }
        for name, value in [("start", start), ("end", end)]
    ] + [
        {
            "name": "exclude_ids",
            "parameterType": {"type": "ARRAY", "arrayType": {"type": "STRING"}},
            "parameterValue": {
                "arrayValues": [{"value": event_id} for event_id in exclude_ids]
            },
        }
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
            return {"state": "query_pending", "entries": [], "complete": False}
        entries = []
        for _ in range(11):
            if data.get("errors"):
                raise ValueError("History query errors")
            fields = [f["name"] for f in data.get("schema", {}).get("fields", [])]
            for row in data.get("rows", []):
                values = dict(
                    zip(fields, [cell.get("v") for cell in row["f"]], strict=True)
                )
                event = json.loads(values["signature"])
                event.update(
                    event_type="econmind_usage",
                    schema_version=1,
                    event_id=f"archive:{len(entries)}",
                )
                entries.append(
                    {"jsonPayload": event, "occurrences": int(values["occurrences"])}
                )
            if len(entries) > MAX_GROUPS:
                return {
                    "state": "ok",
                    "entries": entries[:MAX_GROUPS],
                    "complete": False,
                }
            if not data.get("pageToken"):
                return {"state": "ok", "entries": entries, "complete": True}
            job = data["jobReference"]
            response = session.get(
                f"https://bigquery.googleapis.com/bigquery/v2/projects/{project}/queries/{job['jobId']}",
                params={
                    "location": location,
                    "maxResults": 1000,
                    "pageToken": data["pageToken"],
                },
                timeout=(3, 10),
            )
            response.raise_for_status()
            data = response.json()
        return {"state": "ok", "entries": entries, "complete": False}
    except requests.HTTPError as error:
        status = error.response.status_code if error.response is not None else None
        return {
            "state": {403: "permission_required", 404: "archive_pending"}.get(
                status, "unavailable"
            ),
            "entries": [],
            "complete": False,
        }
    except (requests.RequestException, ValueError, KeyError, TypeError):
        return {"state": "unavailable", "entries": [], "complete": False}
