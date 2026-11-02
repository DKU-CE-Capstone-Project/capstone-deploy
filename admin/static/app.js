"use strict";
const $ = (id) => document.getElementById(id);
const states = {
  not_configured: "설정 대기",
  authentication_required: "GCP 인증 필요",
  unavailable: "조회 실패",
  no_data: "수집 데이터 없음",
  ok: "수집됨",
};
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
  $("project").textContent = data.project
    ? `${data.project} · VM ${data.instance || "미설정"}`
    : "GCP 프로젝트 설정 대기";
  $("updated").textContent =
    `갱신 ${new Date(data.updated_at).toLocaleString("ko-KR")}`;
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
    const label = `VM ${metricNames[key]} 사용률 ${latest ? latest.value.toFixed(1) + "%" : "확인 불가"}`;
    (key === "disk" ? gauge : $(key + "-trigger")).setAttribute("aria-label", label + (key === "disk" ? "" : ", 최근 30분 그래프 보기"));
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
      ? `${key === "disk" ? `${selected?.device || "디스크"} · 장치 중 최대 · ` : ""}${new Date(latest.time).toLocaleTimeString("ko-KR")}${age > 5 ? " · 지연된 지표" : ""}${metric.complete === false ? " · 일부 장치" : ""}`
      : states[metric?.state] || "설정 대기";
    if (key !== "disk") renderHistory(key, selected?.points || [], data.updated_at);
  }
  const usage = data.usage;
  let warning = "";
  if (usage.state !== "ok")
    warning =
      usage.state === "no_data"
        ? "사용량 이벤트가 아직 없습니다. 계측 배포·Ops Agent 전송 상태와 선택한 기간을 확인해 주세요."
        : `사용량 ${states[usage.state] || "확인 불가"}. 수집 설정과 읽기 권한을 확인해 주세요.`;
  if (usage.complete === false)
    warning =
      "조회 한도에 도달했습니다. 아래 값은 일부 이벤트의 합계입니다. 더 짧은 기간을 선택해 주세요.";
  $("notice").textContent = warning;
  $("notice").hidden = !warning;
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
async function refresh() {
  const button = $("refresh");
  if (button.disabled) return;
  button.disabled = true;
  try {
    const response = await fetch(`/api/overview?hours=${$("period").value}`, {
      cache: "no-store",
    });
    if (!response.ok) throw Error();
    render(await response.json());
  } catch {
    $("notice").hidden = false;
    $("notice").textContent =
      "조회하지 못했습니다. 마지막 표시 값은 이전 조회 결과입니다. 다시 시도해 주세요.";
    $("updated").textContent = "갱신 실패";
  } finally {
    button.disabled = false;
  }
}
$("refresh").addEventListener("click", refresh);
$("period").addEventListener("change", refresh);
for (const key of ["cpu", "memory"]) bindHistory(key);
setInterval(refresh, 60000);
refresh();
