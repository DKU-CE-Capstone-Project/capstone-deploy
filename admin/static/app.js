"use strict";
const $ = (id) => document.getElementById(id);
const states = {
  not_configured: "설정 대기",
  authentication_required: "GCP 인증 필요",
  unavailable: "조회 실패",
  no_data: "수집 데이터 없음",
  ok: "수집됨",
};
const pages = {
  overview: { title: "운영 현황", eyebrow: "OVERVIEW", description: "VM 리소스와 DB 연결 상태를 확인합니다." },
  "external-api": { title: "외부 API 사용량", eyebrow: "EXTERNAL API", description: "공급자·모델·작업 단계별 API 호출과 토큰을 확인합니다.", usage: true },
  "service-api": { title: "서비스 API 요청", eyebrow: "SERVICE API", description: "서비스 API 경로별 요청 수와 오류 응답을 확인합니다.", usage: true },
  cost: { title: "비용", eyebrow: "COST", description: "측정 시작부터의 누적 비용과 기간별 사용액을 확인합니다." },
};
let currentTab;
let hasData = false;
let usageWarning = "";
let fetchWarning = "";
let costFetchWarning = "";
let lastOverview;
let lastCosts;
let localMode = false;
const costPeriods = { gcp: "all", external: "all" };
const costPeriodLabels = { all: "누적", day: "하루", week: "7일", month: "한 달" };
function costQuery() {
  return `gcp_period=${costPeriods.gcp}&external_period=${costPeriods.external}`;
}
function selectCostPeriod(component, period) {
  costPeriods[component] = period;
  for (const name of Object.keys(costPeriodLabels)) {
    const button = $(`${component}-period-${name}`);
    button.setAttribute("aria-pressed", String(name === period));
    button.className = name === period ? "selected" : "";
  }
  $("updated").textContent = "기간 조회 중…";
  refresh();
}
function renderMeta() {
  const data = currentTab === "cost" ? lastCosts : lastOverview;
  const source = data || lastOverview || lastCosts;
  localMode = source?.mode === "local";
  $("project").textContent = localMode
    ? `Docker Compose · ${source.instance}`
    : (data?.project
      ? `${data.project}${currentTab === "cost" ? " · 프로젝트 크레딧·API 비용" : ` · VM ${data.instance || "미설정"}`}`
      : "프로젝트 확인 중");
  $("updated").textContent = data ? `갱신 ${new Date(data.updated_at).toLocaleString("ko-KR")}` : "조회 중…";
  $("monitoring-source").textContent = source?.mode === "vm"
    ? "VM 수집기 · Cloud Billing"
    : localMode ? "호스트 지표 · 앱 사용량" : "Cloud Monitoring · Cloud Logging";
  $("gcp-summary").hidden = $("gcp-details").hidden = localMode;
  $("external-cost-source").textContent = localMode ? "공시 단가 · USD · 로컬 수집 이벤트 기준" : "공시 단가 · USD · 관측된 프로젝트 사용량 기준";
  if (currentTab === "overview") $("page-description").textContent = localMode ? "Docker 호스트 리소스와 DB 연결 상태를 확인합니다." : pages.overview.description;
}
function renderNotice() {
  const message = currentTab === "cost" ? costFetchWarning : fetchWarning || usageWarning;
  $("notice").textContent = message;
  $("notice").hidden = !message || (!fetchWarning && currentTab === "overview");
}
function showTab(name, updateHash = false) {
  const key = Object.hasOwn(pages, name) ? name : "overview";
  const changed = currentTab !== key;
  currentTab = key;
  const page = pages[key];
  for (const name of Object.keys(pages)) {
    const selected = name === key;
    $("view-" + name).hidden = !selected;
    const tab = $("tab-" + name);
    tab.className = `nav${selected ? " active" : ""}`;
    tab.setAttribute("aria-selected", String(selected));
    tab.setAttribute("tabindex", selected ? "0" : "-1");
  }
  $("page-title").textContent = page.title;
  $("page-description").textContent = page.description;
  $("page-eyebrow").textContent = `ECONMIND / ${page.eyebrow}`;
  document.title = `EconMind · ${page.title}`;
  $("usage-period").hidden = !page.usage;
  for (const id of ["data-controls", "data-meta", "data-footer"]) $(id).hidden = false;
  $("data-footer").textContent = key === "cost" ? "비용은 5분 캐시 · 60초마다 갱신 확인" : "60초마다 자동 갱신";
  if (key === "overview") $("data-footer").textContent = "DB 표시는 백엔드에서 MongoDB까지의 연결 결과입니다. 호스트 전원 상태를 의미하지 않습니다. · 60초마다 자동 갱신";
  if (updateHash && location.hash !== "#" + key) location.hash = "#" + key;
  if (changed) {
    for (const metric of ["cpu", "memory"]) historyOpen(metric, false);
    window.scrollTo(0, 0);
  }
  renderNotice();
  renderMeta();
  if (changed || !(key === "cost" ? lastCosts : hasData)) refresh();
}
function cell(row, text, detail) {
  const td = document.createElement("td");
  td.textContent = text;
  if (detail) {
    const small = document.createElement("small");
    small.textContent = detail;
    td.append(small);
  }
  row.append(td);
}
function tokens(row, key) {
  const n = row.token_observations[key];
  const total = row.sdk_calls + row.http_attempts;
  if (key === "input_tokens" && row.token_observations.counted_input_tokens) {
    const counted = row.token_observations.counted_input_tokens;
    const parts = [];
    if (n) parts.push(`${row.token_sums[key].toLocaleString()} (응답)`);
    parts.push(`${row.token_sums.counted_input_tokens.toLocaleString()} (계수)`);
    return `${parts.join(" + ")}${n + counted < total ? " (일부)" : ""}`;
  }
  return n
    ? `${row.token_sums[key].toLocaleString()}${n < total ? " (일부)" : ""}`
    : "미제공";
}
const metricNames = { cpu: "CPU", memory: "메모리", disk: "디스크" };
const minute = 60000;
function clock(time, seconds = false) {
  return new Date(time).toLocaleTimeString("ko-KR", {
    hour: "2-digit",
    minute: "2-digit",
    ...(seconds ? { second: "2-digit" } : {}),
    hour12: false,
  });
}
function historyWindow(points, updatedAt) {
  const parsedEnd = Date.parse(updatedAt);
  const end = Number.isFinite(parsedEnd) ? parsedEnd : Date.now();
  const start = end - 30 * minute;
  const buckets = Array(30).fill(null);
  for (const point of points) {
    const time = Date.parse(point.time);
    if (!Number.isFinite(point.value) || !Number.isFinite(time) || time < start || time > end) continue;
    const index = Math.min(29, Math.floor((time - start) / minute));
    // Keep the latest actual observation in each minute; missing minutes stay empty.
    if (!buckets[index] || time >= Date.parse(buckets[index].time)) buckets[index] = point;
  }
  return { start, end, buckets };
}
function renderHistory(key, points, updatedAt) {
  const { start, end, buckets } = historyWindow(points, updatedAt);
  const chart = $(key + "-chart");
  chart.replaceChildren();
  chart.setAttribute("aria-label", `${metricNames[key]} 사용률, ${clock(start)}부터 ${clock(end)}까지, 세로축 0에서 100%, 한 칸 1분`);
  for (const [index, point] of buckets.entries()) {
    const slot = document.createElement("span");
    slot.className = "history-slot";
    slot.title = point
      ? `${clock(point.time, true)} · ${point.value.toFixed(1)}%`
      : `${clock(start + index * minute)}–${clock(start + (index + 1) * minute)} · 수집값 없음`;
    if (point) {
      const bar = document.createElement("i");
      bar.className = "history-bar";
      bar.style.height = `${Math.max(0, Math.min(100, point.value))}%`;
      slot.append(bar);
    }
    chart.append(slot);
  }
  $(key + "-start").textContent = `${clock(start)} · 30분 전`;
  $(key + "-end").textContent = `${clock(end)} · 현재`;
  $(key + "-history-note").textContent = buckets.some(Boolean)
    ? "1칸 1분 · 수집되지 않은 구간은 공백 · 막대에 올리면 시각과 사용률 표시"
    : "최근 30분 수집 데이터가 없습니다.";
}
function historyOpen(key, open) {
  for (const name of ["cpu", "memory"]) {
    if (name !== key && !open) continue;
    const visible = name === key && open;
    $(name + "-history").hidden = !visible;
    $(name + "-trigger").setAttribute("aria-expanded", String(visible));
    $(name + "-card").style.zIndex = visible ? "5" : "";
  }
}
function bindHistory(key) {
  const card = $(key + "-card");
  const trigger = $(key + "-trigger");
  card.addEventListener("pointerenter", () => historyOpen(key, true));
  card.addEventListener("pointerleave", () => {
    if (!card.contains(document.activeElement)) historyOpen(key, false);
  });
  card.addEventListener("focusin", () => historyOpen(key, true));
  card.addEventListener("focusout", (event) => {
    if (!card.contains(event.relatedTarget)) historyOpen(key, false);
  });
  trigger.addEventListener("click", () => historyOpen(key, true));
  card.addEventListener("keydown", (event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      trigger.focus();
      historyOpen(key, false);
    }
  });
  $(key + "-history-close").addEventListener("click", () => {
    trigger.focus();
    historyOpen(key, false);
  });
  document.addEventListener("pointerdown", (event) => {
    if (!card.contains(event.target)) historyOpen(key, false);
  });
}
function render(data) {
  hasData = true;
  fetchWarning = "";
  lastOverview = data;
  renderMeta();
  const db = data.connection;
  const names = {
    connected: "연결됨",
    unreachable: "연결 불가",
    unknown: "확인 불가",
    disabled: "비활성",
    not_configured: "설정 대기",
  };
  $("db").textContent = names[db.state] || "확인 불가";
  $("db").className = `metric ${db.state}`;
  $("db-note").textContent = db.checked_at
    ? `확인 ${new Date(db.checked_at).toLocaleTimeString("ko-KR")}`
    : "연결 확인 경로와 인증을 설정해 주세요.";
  for (const key of ["cpu", "memory", "disk"]) {
    const metric = data.metrics[key];
    const all = (metric?.series || []).map((s) => s.points.at(-1));
    const latest = all.filter((point) => point && Number.isFinite(point.value)).sort((a, b) => b.value - a.value)[0];
    const selected = (metric?.series || []).find(
      (series) => series.points.at(-1) === latest,
    );
    $(key).textContent = latest ? `${latest.value.toFixed(1)}%` : "—";
    const gauge = $(key + "-gauge");
    gauge.style.setProperty("--usage", latest ? Math.max(0, Math.min(100, latest.value)) : 0);
    gauge.className = `gauge${latest ? "" : " is-empty"}`;
    const name = `${localMode ? "호스트" : "VM"} ${metricNames[key]}`;
    $(key + "-label").textContent = name;
    if (key !== "cpu") $(key + "-source").textContent = data.mode === "vm" || localMode ? "LINUX" : "OPS AGENT";
    const label = `${name} 사용률 ${latest ? latest.value.toFixed(1) + "%" : "확인 불가"}`;
    (key === "disk" ? gauge : $(key + "-trigger")).setAttribute("aria-label", label + (key === "disk" ? "" : ", 최근 30분 그래프 보기"));
    if (key !== "disk") $(key + "-history").setAttribute("aria-label", `${name} 최근 30분 사용률`);
    if (key !== "cpu") {
      const used = latest?.used_bytes;
      const total = latest?.total_bytes;
      const available = Number.isFinite(used) && Number.isFinite(total) && used >= 0 && total > 0 && used <= total;
      const capacity = $(key + "-capacity");
      capacity.textContent = available
        ? `${(used / 2 ** 30).toFixed(2)} GiB / ${(total / 2 ** 30).toFixed(2)} GiB`
        : "— GiB / — GiB";
      capacity.setAttribute("aria-label", available ? `사용량 / 전체 용량: ${capacity.textContent}` : "용량 확인 불가");
    }
    const age = latest ? (Date.now() - Date.parse(latest.time)) / 60000 : 0;
    $(key + "-note").textContent = latest
      ? `${key === "disk" ? `${selected?.device || "디스크"} · ${localMode || data.mode === "vm" ? "" : "장치 중 최대 · "}` : ""}${new Date(latest.time).toLocaleTimeString("ko-KR")}${age > 5 ? " · 지연된 지표" : ""}${metric.complete === false ? " · 일부 장치" : ""}`
      : states[metric?.state] || "설정 대기";
    if (key !== "disk") renderHistory(key, selected?.points || [], data.updated_at);
  }
  const usage = data.usage;
  let warning = "";
  if (usage.state !== "ok")
    warning =
      usage.state === "no_data"
        ? `사용량 이벤트가 아직 없습니다. ${localMode || data.mode === "vm" ? "앱 로그 전달 설정과" : "계측 배포·Ops Agent 전송 상태와"} 선택한 기간을 확인해 주세요.`
        : `사용량 ${states[usage.state] || "확인 불가"}. 수집 설정과 읽기 권한을 확인해 주세요.`;
  if (usage.complete === false)
    warning =
      "조회 한도에 도달했습니다. 아래 값은 일부 이벤트의 합계입니다. 더 짧은 기간을 선택해 주세요.";
  usageWarning = warning;
  renderNotice();
  $("usage-state").textContent =
    (states[usage.state] || "확인 불가") +
    (usage.complete === false ? " · 일부" : "");
  $("usage").replaceChildren();
  for (const row of usage.providers || []) {
    const tr = document.createElement("tr");
    cell(tr, row.provider, row.model);
    cell(tr, row.operation, `${row.stage} · ${row.service_tier}`);
    cell(
      tr,
      row.sdk_calls && !row.http_attempts
        ? "미관측"
        : row.http_attempts.toLocaleString(),
    );
    cell(tr, row.sdk_calls.toLocaleString());
    cell(tr, row.cache_hits.toLocaleString());
    cell(tr, `${row.errors} / ${row.cancelled}`);
    cell(tr, tokens(row, "input_tokens"));
    cell(tr, tokens(row, "output_tokens"));
    $("usage").append(tr);
  }
  $("usage-empty").hidden = !!usage.providers?.length;
  $("routes").replaceChildren();
  for (const row of usage.routes || []) {
    const tr = document.createElement("tr");
    cell(tr, row.method);
    cell(tr, row.route);
    cell(tr, row.requests.toLocaleString());
    cell(tr, row.errors.toLocaleString());
    $("routes").append(tr);
  }
  $("routes-empty").hidden = !!usage.routes?.length;
}
function money(value, currency = "USD") {
  if (!Number.isFinite(value)) return "—";
  if (currency === "USD" && value > 0 && value < 0.000001) return "< US$0.000001";
  return new Intl.NumberFormat("ko-KR", {style: "currency", currency, minimumFractionDigits: currency === "KRW" ? 0 : 2, maximumFractionDigits: currency === "USD" ? 6 : 2}).format(value);
}
function stamp(value) {
  return value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString("ko-KR", {timeZone: "Asia/Seoul"}) : "미제공";
}
function costRange(data) {
  return `${stamp(data.period_start)} ~ ${stamp(data.period_end)} (KST)`;
}
function creditsUsed(value, currency) {
  return money(Number.isFinite(value) ? value === 0 ? 0 : -value : undefined, currency);
}
function observedTokenTotal(rows, key) {
  const observed = rows.filter((row) => row.token_observations[key]);
  return observed.length ? observed.reduce((total, row) => total + row.token_sums[key], 0) : null;
}
async function lookupReportJob(event) {
  event.preventDefault();
  const input = $("report-job-id");
  const button = $("report-job-submit");
  if (button.disabled) return;
  const jobId = input.value.trim();
  const status = $("report-job-status");
  const results = $("report-job-results");
  const rows = $("report-job-rows");
  status.hidden = false;
  results.hidden = true;
  rows.replaceChildren();
  input.disabled = button.disabled = true;
  status.textContent = "작업 사용량 조회 중…";
  try {
    const response = await fetch(`/api/report-job-usage?job_id=${encodeURIComponent(jobId)}`, {cache: "no-store"});
    if (response.status === 422) {
      status.textContent = "리포트 작업 ID 형식을 확인해 주세요.";
      return;
    }
    if (!response.ok) throw Error();
    const data = await response.json();
    if (data.state === "not_configured") {
      status.textContent = "SQLite 수집기가 연결된 관리자에서 작업별 사용량을 조회할 수 있습니다.";
      return;
    }
    if (data.state === "no_data") {
      status.textContent = "이 작업 ID로 수집된 사용량이 없습니다.";
      return;
    }
    for (const row of data.rows) {
      const tr = document.createElement("tr");
      cell(tr, row.stage, row.operation);
      cell(tr, row.provider, row.model);
      cell(tr, (row.sdk_calls + row.http_attempts).toLocaleString(), row.cache_hits ? `캐시 재사용 ${row.cache_hits}회` : "");
      cell(tr, tokens(row, "input_tokens"));
      cell(tr, tokens(row, "output_tokens"));
      cell(tr, Number.isFinite(row.estimated_usd) ? money(row.estimated_usd) : "미산정",
        row.unpriced_calls ? `미산정 ${row.unpriced_calls}회` : "");
      rows.append(tr);
    }
    const amount = Number.isFinite(data.estimated_usd) ? money(data.estimated_usd) : "미산정";
    const missing = data.unpriced_calls ? ` · 미산정 ${data.unpriced_calls}회` : "";
    const capped = data.logs_complete ? "" : " · 조회 한도 도달, 일부 이벤트";
    status.textContent = `${data.job_id} · 예상 비용 ${amount} · ${data.priced_calls} / ${data.calls}회 산정${missing}${capped}`;
    const inputTokens = observedTokenTotal(data.rows, "input_tokens")?.toLocaleString() ?? "미제공";
    const outputTokens = observedTokenTotal(data.rows, "output_tokens")?.toLocaleString() ?? "미제공";
    const countedTokens = observedTokenTotal(data.rows, "counted_input_tokens");
    const counted = countedTokens === null ? "" : ` · 별도 계수 입력 ${countedTokens.toLocaleString()}`;
    $("report-job-totals").textContent = `관측 토큰 합계 · 입력 ${inputTokens} · 출력 ${outputTokens}${counted}`;
    results.hidden = false;
  } catch {
    status.textContent = "리포트 작업 사용량을 조회하지 못했습니다. 다시 시도해 주세요.";
  } finally {
    input.disabled = button.disabled = false;
  }
}
function renderCosts(data) {
  lastCosts = data;
  costFetchWarning = "";
  renderMeta();
  renderNotice();
  const gcp = data.gcp || {};
  const external = data.external || {};
  const billingStates = {ok: "BigQuery", snapshot: "보고서 기준", export_pending: "첫 내보내기 대기", no_data: "내역 수집 대기", query_pending: "집계 중", permission_required: "조회 권한 필요", not_configured: "연결 대기", authentication_required: "GCP 인증 필요", unavailable: "조회 실패", mixed_currency: "통화별 확인 필요"};
  $("gcp-cost-state").textContent = billingStates[gcp.state] || "확인 불가";
  $("gcp-credits-used").textContent = creditsUsed(gcp.credits, gcp.currency || "KRW");
  $("gcp-selected-period").textContent = `${gcp.period_label || costPeriodLabels[data.gcp_period] || "누적"} · 프로젝트에 적용된 크레딧`;
  $("gcp-measurement-start").textContent = `측정 시작 ${stamp(gcp.measurement_start)} (KST)`;
  $("gcp-cost-period").textContent = costRange(gcp);
  $("gcp-report-link").href = gcp.report_url || "https://console.cloud.google.com/billing";
  $("gcp-cost-rows").replaceChildren();
  for (const row of gcp.services || []) {
    const tr = document.createElement("tr");
    cell(tr, row.service);
    cell(tr, creditsUsed(row.credits, row.currency));
    $("gcp-cost-rows").append(tr);
  }
  $("gcp-cost-empty").hidden = !!gcp.services?.length;
  $("gcp-cost-empty").textContent = billingStates[gcp.state] || "크레딧 데이터가 없습니다.";
  $("gcp-cost-note").textContent = gcp.state === "snapshot"
    ? `Cloud Billing 보고서 CSV · 확인 ${stamp(gcp.observed_at)} · 원 단위 반올림 표시값입니다. 자동 연결: ${billingStates[gcp.automatic_state] || "수집 대기"}. 내보내기가 도착하면 BigQuery 데이터로 자동 전환합니다.`
    : gcp.state === "ok"
      ? `BigQuery · 한국 시간 사용월 기준 · 사용량 반영 ${stamp(gcp.data_through)} · 내보내기 ${stamp(gcp.last_export)}. 수집 지연·소급 반영으로 금액이 변경될 수 있습니다. 계정 공통 요금·세금은 제외합니다.`
      : "첫 Billing 내보내기는 지연될 수 있습니다. 데이터가 없거나 조회하지 못한 경우 0원으로 표시하지 않습니다.";
  $("external-cost-period").textContent = external.period_label || costPeriodLabels[data.external_period] || "누적";
  $("external-measurement-start").textContent = `측정 시작 ${stamp(external.measurement_start)} (KST)`;
  $("external-cost-range").textContent = costRange(external);
  $("external-cost-total").textContent = money(external.estimated_usd);
  $("external-cost-coverage").textContent = external.state === "ok"
    ? `${external.priced_calls} / ${external.calls}회 산정${external.counted_calls ? ` · 입력 계수 ${external.counted_calls}회` : ""}${external.unpriced_calls ? ` · 미산정 ${external.unpriced_calls}회` : ""}${external.logs_complete === false ? " · 기록 일부" : ""}`
    : states[external.state] || "확인 불가";
  $("external-cost-state").textContent = external.state === "ok" ? external.complete ? "예상액" : "일부 산정" : states[external.state] || "확인 불가";
  $("external-cost-rows").replaceChildren();
  for (const row of external.providers || []) {
    const tr = document.createElement("tr");
    cell(tr, row.provider, row.models.join(", "));
    cell(tr, row.calls.toLocaleString(), row.cache_hits ? `앱 캐시 ${row.cache_hits}회 별도` : "");
    cell(tr, money(row.estimated_usd));
    cell(tr, `${row.priced_calls} / ${row.calls}회`, [row.counted_calls ? `입력 계수 ${row.counted_calls}회` : "", row.unpriced_calls ? `미산정 ${row.unpriced_calls}회` : ""].filter(Boolean).join(" · "));
    const td = document.createElement("td");
    td.textContent = row.basis.join(" · ") || "앱 캐시 재사용: 외부 호출 없음";
    if (row.source_url?.startsWith("https://")) {
      const link = document.createElement("a");
      link.className = "source-link";
      link.href = row.source_url;
      link.target = "_blank";
      link.rel = "noopener noreferrer";
      link.textContent = " 공식 요금 ↗";
      td.append(link);
    }
    tr.append(td);
    $("external-cost-rows").append(tr);
  }
  $("external-cost-empty").hidden = !!external.providers?.length;
  $("external-cost-empty").textContent = external.state === "no_data" ? "관측된 API 사용량이 없습니다." : states[external.state] || "확인 불가";
  const archiveStates = {ok: "보관 중", archive_pending: "첫 저장 대기", permission_required: "조회 권한 필요", unavailable: "조회 실패", query_pending: "집계 중"};
  $("external-cost-note").textContent = `단가 확인 ${external.pricing_date || "—"} · 누적 기록 ${archiveStates[external.archive_state] || "확인 대기"}. ${(external.assumptions || []).join(" ")}`;
}
async function refresh() {
  const button = $("refresh");
  if (button.disabled) return;
  const isCost = currentTab === "cost";
  const hours = $("period").value;
  const selectedCosts = costQuery();
  button.disabled = true;
  try {
    const response = await fetch(isCost ? `/api/costs?${selectedCosts}` : `/api/overview?hours=${hours}`, {
      cache: "no-store",
    });
    if (!response.ok) throw Error();
    const data = await response.json();
    if (isCost) { if (selectedCosts === costQuery()) renderCosts(data); } else render(data);
  } catch {
    const message = "조회하지 못했습니다. 마지막 표시 값은 이전 조회 결과입니다. 다시 시도해 주세요.";
    if (isCost) costFetchWarning = message; else fetchWarning = message;
    renderNotice();
    $("updated").textContent = "갱신 실패";
  } finally {
    button.disabled = false;
    if ((currentTab === "cost") !== isCost || (isCost ? costQuery() !== selectedCosts : $("period").value !== hours)) refresh();
  }
}
$("refresh").addEventListener("click", refresh);
$("period").addEventListener("change", refresh);
$("report-job-form").addEventListener("submit", lookupReportJob);
for (const component of ["gcp", "external"]) {
  for (const period of Object.keys(costPeriodLabels)) {
    $(`${component}-period-${period}`).addEventListener("click", () => selectCostPeriod(component, period));
  }
}
for (const key of ["cpu", "memory"]) bindHistory(key);
const pageKeys = Object.keys(pages);
for (const [index, key] of pageKeys.entries()) {
  $("tab-" + key).addEventListener("click", () => showTab(key, true));
  $("tab-" + key).addEventListener("keydown", (event) => {
    let next;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") next = (index + 1) % pageKeys.length;
    if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = (index + pageKeys.length - 1) % pageKeys.length;
    if (event.key === "Home") next = 0;
    if (event.key === "End") next = pageKeys.length - 1;
    if (next === undefined) return;
    event.preventDefault();
    showTab(pageKeys[next], true);
    $("tab-" + pageKeys[next]).focus();
  });
}
window.addEventListener("hashchange", () => showTab(location.hash.slice(1)));
setInterval(refresh, 60000);
showTab(location.hash.slice(1));
