"""Observed-usage estimates at published USD rates, never invoice totals."""

from decimal import Decimal

PRICING_DATE = "2026-11-02"
SOURCES = {
    "gemini": "https://ai.google.dev/gemini-api/docs/pricing",
    "openai": "https://developers.openai.com/api/docs/guides/decisions#pricing-and-availability",
    "anthropic": "https://platform.claude.com/docs/en/about-claude/pricing",
    "diffbot": "https://www.diffbot.com/pricing/",
    "naver": "https://developers.naver.com/notice/article/32530",
}
MILLION = Decimal(1000000)


def count(event, key):
    value = event.get(key)
    return value if type(value) is int and value >= 0 else None


def event_cost(event, diffbot_plan):
    """Return (USD or None, pricing basis); unknown usage stays unpriced."""
    provider = event.get("provider")
    model = event.get("model")
    operation = event.get("operation")
    kind = event.get("kind")
    if provider == "naver" and operation == "news_search":
        return Decimal(0), "NAVER 검색 API 기본 무료 정책"
    if provider == "diffbot":
        if diffbot_plan == "free_academic":
            return Decimal(0), "사용자 확인: 무료·학생 요금제 (계정별 크레딧 한도 별도)"
        return None, "Diffbot 요금제 미설정"
    if provider not in {"gemini", "openai", "anthropic"}:
        return None, "공급자 단가 미등록"
    expected_kind = "http_attempt" if provider == "openai" else "sdk_call"
    if kind != expected_kind:
        return None, "청구 단위와 호출 계층이 달라 미산정"
    inputs = count(event, "input_tokens")
    outputs = count(event, "output_tokens")
    if inputs is None:
        return None, "입력 토큰 미관측"
    for field in (
        "input_tokens",
        "output_tokens",
        "cached_input_tokens",
        "thought_tokens",
        "total_tokens",
        "cache_creation_tokens",
    ):
        if event.get(field) is not None and count(event, field) is None:
            return None, "토큰 형식 오류"
    cached = count(event, "cached_input_tokens") or 0
    if provider != "anthropic" and cached > inputs:
        return None, "캐시 토큰 범위 불일치"
    if provider == "gemini":
        if model == "gemini-embedding-001" and operation in {"embed", "embed_batch"}:
            # embed_batch is synchronous embed_content(list), not the Batch API.
            return Decimal(inputs) * Decimal(
                "0.15"
            ) / MILLION, "임베딩 입력 $0.15 / 1M 토큰 · 동기 요청"
        if model != "gemini-3.5-flash-lite" or operation != "generate":
            return None, "모델·작업 단가 미등록"
        if outputs is None:
            return None, "출력 토큰 미관측"
        tier = event.get("service_tier")
        rates = {"standard": ("0.30", "2.50", "0.03"), "flex": ("0.15", "1.25", "0.02")}
        if tier not in rates:
            return None, "Gemini 처리 티어 미관측"
        input_rate, output_rate, cache_rate = map(Decimal, rates[tier])
        thoughts = count(event, "thought_tokens")
        total = count(event, "total_tokens")
        if total is not None and total < inputs + outputs:
            return None, "전체 토큰 범위 불일치"
        if thoughts is None:
            if total is None:
                return None, "사고 토큰 포함 여부 미관측"
            thoughts = total - inputs - outputs
        amount = (
            Decimal(inputs - cached) * input_rate
            + Decimal(cached) * cache_rate
            + Decimal(outputs + thoughts) * output_rate
        ) / MILLION
        return (
            amount,
            f"{tier} · 입력 ${input_rate} / 출력·사고 ${output_rate} / 캐시 ${cache_rate} · 1M 토큰당",
        )
    if provider == "openai":
        if model != "gpt-6-luna" or operation != "decision":
            return None, "모델·작업 단가 미등록"
        multiplier = 2 if inputs > 272000 else 1
        writes = count(event, "cache_creation_tokens") or 0
        if cached + writes > inputs:
            return None, "캐시 토큰 범위 불일치"
        amount = (
            (
                Decimal(inputs - cached - writes) * Decimal("0.10")
                + Decimal(cached) * Decimal("0.01")
                + Decimal(writes) * Decimal("0.125")
            )
            * multiplier
            / MILLION
        )
        return (
            amount,
            "Decisions · 입력 $0.10 / 1M 토큰 · 출력 무료 · 장문 입력 배율 적용",
        )
    if model != "claude-opus-4-8" or operation != "generate":
        return None, "모델·작업 단가 미등록"
    if outputs is None:
        return None, "출력 토큰 미관측"
    writes = count(event, "cache_creation_tokens") or 0
    amount = (
        Decimal(inputs) * Decimal(5)
        + Decimal(outputs) * Decimal(25)
        + Decimal(cached) * Decimal("0.5")
        + Decimal(writes) * Decimal("6.25")
    ) / MILLION
    return (
        amount,
        "Opus 4.8 · 입력 $5 / 출력 $25 / 캐시 읽기 $0.50 / 5분 쓰기 $6.25 · 1M 토큰당",
    )


def estimate_external(entries, *, complete=True, diffbot_plan="unknown"):
    providers, seen = {}, set()
    for entry in entries:
        event = entry.get("jsonPayload", {}) if isinstance(entry, dict) else {}
        if (
            not isinstance(event, dict)
            or event.get("event_type") != "econmind_usage"
            or event.get("schema_version") != 1
        ):
            continue
        event_id = event.get("event_id")
        if not isinstance(event_id, str) or event_id in seen:
            continue
        seen.add(event_id)
        if event.get("kind") not in {"sdk_call", "http_attempt", "cache_hit"}:
            continue
        provider = event.get("provider") or "unknown"
        row = providers.setdefault(
            provider,
            {
                "provider": provider,
                "models": set(),
                "basis": set(),
                "amount": Decimal(0),
                "calls": 0,
                "priced_calls": 0,
                "unpriced_calls": 0,
                "cache_hits": 0,
            },
        )
        row["models"].add(event.get("model") or "—")
        if event["kind"] == "cache_hit":
            row["cache_hits"] += 1
            continue
        row["calls"] += 1
        amount, basis = event_cost(event, diffbot_plan)
        row["basis"].add(basis)
        if amount is None:
            row["unpriced_calls"] += 1
        else:
            row["amount"] += amount
            row["priced_calls"] += 1
    rows = []
    for provider, row in sorted(providers.items()):
        rows.append(
            {
                "provider": provider,
                "models": sorted(row["models"]),
                "basis": sorted(row["basis"]),
                "estimated_usd": float(row["amount"])
                if row["priced_calls"] or not row["calls"]
                else None,
                "calls": row["calls"],
                "priced_calls": row["priced_calls"],
                "unpriced_calls": row["unpriced_calls"],
                "cache_hits": row["cache_hits"],
                "source_url": SOURCES.get(provider),
            }
        )
    priced = sum(row["priced_calls"] for row in rows)
    unpriced = sum(row["unpriced_calls"] for row in rows)
    return {
        "state": "ok" if rows else "no_data",
        "currency": "USD",
        "pricing_date": PRICING_DATE,
        "estimated_usd": float(
            sum((row["amount"] for row in providers.values()), Decimal(0))
        )
        if priced or rows and not unpriced
        else None,
        "complete": complete and not unpriced,
        "logs_complete": complete,
        "calls": priced + unpriced,
        "priced_calls": priced,
        "unpriced_calls": unpriced,
        "providers": rows,
        "assumptions": [
            "LLM은 공시 유료 단가 환산이며 무료량·계약 할인·세금·환율은 반영하지 않습니다.",
            "캐시 토큰 미제공 시 일반 입력 단가를 적용합니다. Claude 캐시 쓰기는 현재 코드의 기본 5분 기준입니다.",
            "토큰 미관측 호출은 금액을 만들지 않습니다. SDK 내부 재시도와 계측 이전·누락 사용량은 제외됩니다.",
            "GCP 청구에 포함되는 Gemini 비용과 중복될 수 있으므로 GCP와 API 금액을 합산하지 않습니다.",
        ],
    }
