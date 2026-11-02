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
    const latest = all.filter(Boolean).sort((a, b) => b.value - a.value)[0];
    $(key).textContent = latest ? `${latest.value.toFixed(1)}%` : "—";
    const age = latest ? (Date.now() - Date.parse(latest.time)) / 60000 : 0;
    $(key + "-note").textContent = latest
      ? `${key === "disk" ? "장치 중 최대 · " : ""}${new Date(latest.time).toLocaleTimeString("ko-KR")}${age > 5 ? " · 지연된 지표" : ""}${metric.complete === false ? " · 일부 장치" : ""}`
      : states[metric?.state] || "설정 대기";
    const chart = $(key + "-chart");
    chart.replaceChildren();
    chart.style.display = latest ? "flex" : "none";
    const selected = (metric?.series || []).find(
      (series) => series.points.at(-1) === latest,
    );
    const points = selected?.points || [];
    for (const point of points.slice(-40)) {
      const bar = document.createElement("i");
      bar.style.height = `${Math.max(4, Math.min(100, point.value))}%`;
      bar.title = `${point.value.toFixed(1)}%`;
      chart.append(bar);
    }
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
setInterval(refresh, 60000);
refresh();
