"""Fixed measurement baselines and explicit Korean-time cost windows."""

from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
LABELS = {"all": "누적", "day": "하루", "week": "7일", "month": "한 달"}


def parse_start(value):
    if not value:
        return None
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Measurement start must include a time zone")
    return result


def window(period, start, end):
    if period not in LABELS:
        raise ValueError("Invalid cost period")
    local = end.astimezone(KST)
    requested = {
        "all": start,
        "day": end - timedelta(days=1),
        "week": end - timedelta(days=7),
        "month": local.replace(day=1, hour=0, minute=0, second=0, microsecond=0),
    }[period]
    return {
        "period": period,
        "period_label": LABELS[period],
        "measurement_start": start.isoformat(),
        "period_start": max(start, requested).isoformat(),
        "period_end": end.isoformat(),
        "time_zone": "Asia/Seoul",
    }
