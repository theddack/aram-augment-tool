// 데이터 출처 페이지: data.json 의 출처·패치 노트·계산 가정값을 그린다 (CSP 때문에 인라인 스크립트 대신 파일)
const safeUrl = (u) => (/^https:\/\//.test(String(u || "")) ? u : "#");  // https 주소만
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
fetch("data.json").then((r) => r.json()).then((d) => {
  document.getElementById("built").textContent = `데이터 만든 시각 ${d.builtAt || "-"} · 패치 ${d.version} · 게임 데이터 ${d.dataVersion}`;
  document.getElementById("sources").insertAdjacentHTML("beforeend", (d.sources || []).map((s) =>
    `<tr><td><a href="${esc(safeUrl(s.url))}" target="_blank" rel="noopener">${esc(s.name)}</a>${s.count > 1 ? ` <span class="muted">(${s.count}개 파일)</span>` : ""}</td>
     <td>${esc(s.license)}</td><td>${esc(s.used)}</td><td class="muted">${esc(s.fetched)}</td></tr>`).join(""));
  document.getElementById("assume").insertAdjacentHTML("beforeend", Stats.ASSUMPTIONS.map((a) =>
    `<tr><td>${a.src === "data" ? "📊" : "⚠️"} ${esc(a.what)}</td><td>${esc(a.why)}</td></tr>`).join(""));
  // 패치 노트 줄마다 게임 데이터 대조 결과
  const checks = {};
  for (const x of [...d.champions, ...d.augments]) if (x.patchCheck) checks[x.name] = x.patchCheck;
  const ICON = { ok: "✅", applied: "📄", stale: "⚠️", differ: "❔", unknown: "" };
  const line = (name, ln, i) => { const c = (checks[name] || [])[i]; return `${c && ICON[c.status] ? ICON[c.status] + " " : ""}${esc(ln)}`; };
  const pn = d.patchNotes;
  document.getElementById("patch").innerHTML = !pn ? `<p class="muted">패치 노트를 가져오지 못했어요.</p>` :
    `<p>패치 <b>${esc(pn.patch)}</b> · <a href="${esc(safeUrl(pn.url))}#patch-aram:-mayhem" target="_blank" rel="noopener">라이엇 공식 패치 노트 원문</a></p>`
    + (pn.intro || []).slice(0, 2).map((x) => `<p class="muted">${esc(x)}</p>`).join("")
    + pn.groups.map((g) => `<h3>${esc(g.title)}</h3><ul>${g.entries.map((e) => `<li>${e.name ? `<b>${esc(e.name)}</b> — ` : ""}${e.lines.map((ln, i) => line(e.name, ln, i)).join(" · ")}</li>`).join("")}</ul>`).join("")
    + `<p class="muted">✅ 게임 데이터에 반영 확인 · 📄 게임 데이터가 아직 이전 값이라 패치 노트 값으로 계산 · ⚠️ 이전 값으로 계산 중(확인 필요) · ❔ 데이터에서 찾지 못함</p>`;
});

// 내 실측 (프로그램에서만): 게임이 끝날 때 저장한 경기 결과의 분당 평균
const MEASURE_NOTE = {
  TOTAL_DAMAGE_TAKEN: (avg) => `초당 ${(avg / 60).toFixed(0)} (게임 전체 평균). 가정값 '받는 피해 초당 600'은 한타 중 집중될 때 기준이라 정의가 달라서, 바로 바꾸지 않고 판 수가 쌓이면 한타 구간 비율과 같이 맞춰 볼 거예요.`,
  TOTAL_DAMAGE_DEALT_TO_CHAMPIONS: () => "추천 빌드의 '초당 피해'와 비교할 실제 값이에요 (게임 전체 평균이라 한타 중보다 낮아요).",
  TOTAL_HEAL: () => "회복·보호막 지표와 비교할 실제 값이에요.",
};
window.addEventListener("pywebviewready", async () => {
  const api = window.pywebview && window.pywebview.api;
  if (!api || !api.get_measurements) return;
  const m = await api.get_measurements();
  const box = document.getElementById("measure");
  if (!m || !m.games) {
    box.innerHTML = `<h3>내 실측</h3><p class="muted">아직 없어요. 게임 연동을 켜고 한 판 끝까지 하면, 롤 클라이언트의 경기 결과(가한 피해·받은 피해·회복·CC)를 이 컴퓨터에 저장해서 여기에 평균을 보여 줘요. 이름은 지우고 저장해요.</p>`;
    return;
  }
  box.innerHTML = `<h3>내 실측 (${m.games}판, 분당 평균)</h3>
    <table><tr><th>항목</th><th>분당</th><th>비교</th></tr>${Object.entries(m.perMinute).map(([k, v]) =>
      `<tr><td>📏 ${esc(v.label)}</td><td>${Number(v.avg).toLocaleString("ko-KR")} <span class="muted">(${v.n}판)</span></td><td class="muted">${esc(MEASURE_NOTE[k] ? MEASURE_NOTE[k](v.avg) : "")}</td></tr>`).join("")}</table>
    <p class="muted" style="font-size:12px">저장 위치: 사용자 폴더의 measure 폴더 (최근 100판). 롤 클라이언트 내부 API 라 형식이 바뀌면 일부 칸이 비어요.</p>`;
});
