// 데이터 출처 페이지: data.json 의 출처·패치 노트·계산 가정값을 그린다 (CSP 때문에 인라인 스크립트 대신 파일)
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
fetch("data.json").then((r) => r.json()).then((d) => {
  document.getElementById("built").textContent = `데이터 만든 시각 ${d.builtAt || "-"} · 패치 ${d.version} · 게임 데이터 ${d.dataVersion}`;
  document.getElementById("sources").insertAdjacentHTML("beforeend", (d.sources || []).map((s) =>
    `<tr><td><a href="${esc(s.url)}" target="_blank" rel="noopener">${esc(s.name)}</a>${s.count > 1 ? ` <span class="muted">(${s.count}개 파일)</span>` : ""}</td>
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
    `<p>패치 <b>${esc(pn.patch)}</b> · <a href="${esc(pn.url)}#patch-aram:-mayhem" target="_blank" rel="noopener">라이엇 공식 패치 노트 원문</a></p>`
    + (pn.intro || []).slice(0, 2).map((x) => `<p class="muted">${esc(x)}</p>`).join("")
    + pn.groups.map((g) => `<h3>${esc(g.title)}</h3><ul>${g.entries.map((e) => `<li>${e.name ? `<b>${esc(e.name)}</b> — ` : ""}${e.lines.map((ln, i) => line(e.name, ln, i)).join(" · ")}</li>`).join("")}</ul>`).join("")
    + `<p class="muted">✅ 게임 데이터에 반영 확인 · 📄 게임 데이터가 아직 이전 값이라 패치 노트 값으로 계산 · ⚠️ 이전 값으로 계산 중(확인 필요) · ❔ 데이터에서 찾지 못함</p>`;
});
