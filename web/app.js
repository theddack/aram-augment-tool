const S = window.Scoring;
const St = window.Stats;
const state = {
  data: null, champ: null, weights: null, chosen: [], rarity: "all", augQuery: "",
  items: [], kind: "all", itemQuery: "", concept: "augment", level: 18, aram: true,
  offers: [null, null, null], rerolled: [false, false, false],
};
const REROLL_PCT = 0.4; // 같은 등급 안에서 하위 40% 이하면 새로고침 권장
const statOpts = () => ({ aram: state.aram });
const augStats = () => S.augStatsOf(state.chosen, state.data.rules, state.champ);
const fmt = (v, d = 0) => (Math.round(v * 10 ** d) / 10 ** d).toLocaleString("ko-KR", { minimumFractionDigits: d, maximumFractionDigits: d });
const KIND_LABEL = { magic: "마법 피해", physical: "물리 피해", true: "고정 피해", shield: "보호막", heal: "회복" };
const METRIC_LABEL = { combo: "콤보", dps: "DPS", ehp: "유효 체력", support: "회복·보호막" };
const KIND_NAME = { legendary: "전설", boots: "신발", special: "증강 보상" };
const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const label = (t) => S.TAG_LABEL[t] || t;

async function init() {
  const res = await fetch("data.json");
  state.data = await res.json();
  const d = state.data;
  $("#meta").textContent = `패치 ${d.version} · 증강 ${d.augments.length}개 (비활성 ${d.disabled.length}개 제외) · 아이템 ${d.items.length}개 (칼바람 증강 모드 상점 기준)`;
  $("#meta").title = `게임 데이터 ${d.dataVersion}\n제외한 비활성 증강: ${d.disabled.join(", ")}`;
  $("#champ-search").addEventListener("input", renderChamps);
  $("#aug-search").addEventListener("input", (e) => { state.augQuery = e.target.value.trim(); renderAugs(); });
  document.querySelectorAll("#rarity-filter button").forEach((b) => b.addEventListener("click", () => {
    state.rarity = b.dataset.r;
    document.querySelectorAll("#rarity-filter button").forEach((x) => x.classList.toggle("on", x === b));
    renderAugs();
  }));
  $("#item-search").addEventListener("input", (e) => { state.itemQuery = e.target.value.trim(); renderItemList(); });
  document.querySelectorAll("#kind-filter button").forEach((b) => b.addEventListener("click", () => {
    state.kind = b.dataset.k;
    document.querySelectorAll("#kind-filter button").forEach((x) => x.classList.toggle("on", x === b));
    renderItemList();
  }));
  $("#reset-augs").addEventListener("click", () => { state.chosen = []; renderAll(); });
  $("#reset-offers").addEventListener("click", () => { clearOffers(); renderAll(); });
  $("#aug-names").innerHTML = d.augments.map((a) => `<option value="${esc(a.name)}">${a.rarityName}</option>`).join("");
  $("#reset-items").addEventListener("click", () => { state.items = []; renderAll(); });
  $("#reset-all").addEventListener("click", () => {
    state.chosen = [];
    state.items = [];
    state.concept = "augment";
    clearOffers();
    if (state.champ) state.weights = S.profile(state.champ, d.tags);
    renderAll();
  });
  $("#level").addEventListener("input", (e) => { state.level = Number(e.target.value); $("#level-val").textContent = state.level; renderStats(); });
  $("#aram-toggle").addEventListener("change", (e) => { state.aram = e.target.checked; renderAll(); });
  $("#stat-panel").querySelector("p").innerHTML = $("#stat-panel").querySelector("p").innerHTML
    .replace("${TARGET_ARMOR}", St.TARGET.ARMOR).replace("${TARGET_MR}", St.TARGET.MR);
  $("#reset-weights").addEventListener("click", () => {
    if (state.champ) { state.weights = S.profile(state.champ, d.tags); renderAll(); }
  });
  renderChamps();
  renderAll();
}

function renderChamps() {
  const q = $("#champ-search").value.trim();
  const list = state.data.champions.filter((c) => !q || c.name.includes(q) || c.id.toLowerCase().includes(q.toLowerCase()));
  $("#champ-grid").innerHTML = list.map((c) => `
    <div class="champ ${state.champ && state.champ.id === c.id ? "on" : ""}" data-id="${c.id}">
      <img src="icons/champion/${c.id}.png" alt="" loading="lazy">${esc(c.name)}
    </div>`).join("");
  $("#champ-grid").querySelectorAll(".champ").forEach((el) => el.addEventListener("click", () => {
    state.champ = state.data.champions.find((c) => c.id === el.dataset.id);
    state.weights = S.profile(state.champ, state.data.tags);
    renderChamps();
    renderAll();
  }));
}

function renderAll() {
  renderChampCard();
  renderStats();
  renderOffers();
  renderChosen();
  renderAugs();
  renderChosenItems();
  renderBuild();
  renderItemList();
  renderWeights();
}

function renderChampCard() {
  const c = state.champ;
  const el = $("#champ-card");
  if (!c) return;
  el.classList.remove("empty");
  const top = Object.entries(state.weights).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]).slice(0, 6);
  const featLabel = state.data.rules.features;
  const feats = (c.features || []).filter((f) => f !== "melee" && f !== "ranged");
  const traits = Object.entries(c.traits || {}).sort((a, b) => b[1] - a[1]).slice(0, 5);
  el.innerHTML = `<img src="icons/champion/${c.id}.png" alt="">
    <div><div><b>${esc(c.name)}</b> <span class="muted">${esc(c.title)}</span></div>
    <div class="muted small">${c.tags.join(" · ")} · ${c.melee ? "근접" : "원거리"} · ${esc(c.partype || "자원 없음")}
      ${c.magicShare != null ? ` · 스킬 피해 마법 ${Math.round(c.magicShare * 100)}%` : ""}</div>
    <div><span class="muted small">가중치</span> ${top.map(([t, v]) => `<span class="chip">${label(t)} ${v}</span>`).join("")}</div>
    <div><span class="muted small">스킬 특성</span> ${traits.map(([t, v]) => `<span class="chip">${label(t)} ${v}</span>`).join("")}
      ${feats.map((f) => `<span class="chip on">${esc(featLabel[f] || f)}</span>`).join("")}</div>
    ${aramChips(c)}</div>`;
}

const ARAM_NAME = { dmg_dealt: "가하는 피해", dmg_taken: "받는 피해", healing: "회복", shielding: "보호막",
  ability_haste: "스킬 가속", tenacity: "강인함", total_as: "공격 속도", energyregen_mod: "기력 회복" };
function aramChips(c) {
  if (!c.aram) return `<div><span class="muted small">칼바람 보정 없음</span></div>`;
  const chips = Object.entries(c.aram).map(([k, v]) => {
    const pct = k === "ability_haste" ? `+${v}` : `${Math.round(v * 100)}%`;
    const good = k === "dmg_taken" ? v < 1 : k === "ability_haste" ? v > 0 : v > 1;
    const neutral = k !== "ability_haste" && v === 1;
    return `<span class="chip ${neutral ? "" : good ? "syn" : "bad"}">${ARAM_NAME[k] || k} ${pct}</span>`;
  });
  return `<div><span class="muted small">칼바람 보정 (위키)</span> ${chips.join("")}</div>`;
}

function renderStats() {
  const c = state.champ;
  if (!c) { $("#metrics").innerHTML = ""; $("#stat-table").innerHTML = ""; $("#skills").innerHTML = ""; return; }
  const L = state.level, o = statOpts();
  const e0 = St.computeStats(c, L, [], [], o).total;
  const e1 = St.computeStats(c, L, state.items, [], o).total;
  const full = St.evaluate(c, L, state.items, augStats(), o);
  const e2 = full.stats.total, m = full.m;
  const base = St.evaluate(c, L, [], [], o).m;
  const tile = (name, v, b, sub) => {
    const d = b > 0 ? (v - b) / b : 0;
    return `<div class="tile"><div class="muted small">${name}</div><div class="big">${fmt(v)}</div>
      <div class="small ${d > 0.005 ? "up" : d < -0.005 ? "down" : "muted"}">${Math.abs(d) > 0.005 ? (d > 0 ? "+" : "") + fmt(d * 100) + "% (맨몸 대비)" : "맨몸과 같음"}</div>
      ${sub ? `<div class="muted small">${sub}</div>` : ""}</div>`;
  };
  $("#metrics").innerHTML = tile("스킬 콤보 피해", m.combo, base.combo, `방어 적용 전 ${fmt(m.comboRaw)}`)
    + tile("평타 DPS", m.autoDps, base.autoDps, `공속 ${fmt(e2.AS, 2)} · 치명 ${fmt(e2.CRIT * 100)}%`)
    + tile("유효 체력", m.ehp, base.ehp, `물리 ${fmt(m.ehpPhys)} · 마법 ${fmt(m.ehpMag)}`)
    + tile("회복·보호막 (스킬 1회)", m.support, base.support, `보호막 ${fmt(m.shield)} · 회복 ${fmt(m.heal)}`);
  const rows = St.ROWS.filter(([k]) => Math.abs(e2[k] || 0) > 1e-6 || ["HP", "AD", "AP", "AS", "ARMOR", "MR", "MS"].includes(k));
  const cell = (v, k, d, pct, sign) => {
    if (Math.abs(v) < 1e-6) return sign ? `<td class="muted">-</td>` : `<td>0</td>`;
    const val = pct ? fmt(v * 100) + "%" : fmt(v, d);
    return `<td class="${sign ? (v > 0 ? "up" : "down") : ""}">${sign && v > 0 ? "+" : ""}${val}</td>`;
  };
  $("#stat-table").innerHTML = `<tr><th>능력치</th><th>기본 (${L}레벨)</th><th>아이템</th><th>증강</th><th>합계</th></tr>`
    + rows.map(([k, name, d, pct]) => `<tr><td>${name}</td>${cell(e0[k] || 0, k, d, pct)}${cell((e1[k] || 0) - (e0[k] || 0), k, d, pct, true)}${cell((e2[k] || 0) - (e1[k] || 0), k, d, pct, true)}<td><b>${pct ? fmt((e2[k] || 0) * 100) + "%" : fmt(e2[k] || 0, d)}</b></td></tr>`).join("");
  $("#skills").innerHTML = `<div class="skill"><b>패시브 · ${esc(c.passive.name)}</b><div class="desc">${c.passive.desc}</div></div>`
    + c.spells.map((sp, i) => {
      const ms = m.spells[i];
      const vals = ms.values.map((v) => `<span class="chip k-${v.kind}">${KIND_LABEL[v.kind]} ${fmt(v.value)}</span>`).join("");
      return `<div class="skill ${ms.disabled ? "off" : ""}"><b>${sp.slot} · ${esc(sp.name)}</b>
        <span class="muted small">랭크 ${ms.rank}/${sp.ranks} · 재사용 ${fmt(ms.cd, 1)}초</span>
        ${ms.disabled ? `<span class="chip bad">${ms.rank === 0 ? "아직 안 배움" : "궁극기 봉인"}</span>` : ""}
        <div>${vals ? `<span class="muted small">지금 수치</span> ${vals}` : ""}</div>
        <details><summary class="muted small">스킬 설명</summary><div class="desc">${sp.tooltip}</div></details></div>`;
    }).join("");
}

function renderChosen() {
  const slots = [0, 1, 2, 3].map((i) => {
    const a = state.chosen[i];
    if (!a) return `<div class="slot muted">빈 칸</div>`;
    return `<div class="slot" data-i="${i}" title="해제"><img src="icons/augment/${a.id}.png" alt=""><span class="r${a.rarity}">${esc(a.name)}</span></div>`;
  });
  $("#chosen").innerHTML = slots.join("");
  $("#chosen").querySelectorAll(".slot[data-i]").forEach((el) => el.addEventListener("click", () => {
    state.chosen.splice(Number(el.dataset.i), 1);
    renderAll();
  }));
}

function clearOffers() {
  state.offers = [null, null, null];
  state.rerolled = [false, false, false];
}

function renderOffers() {
  const box = $("#offers");
  const full = state.chosen.length >= 4;
  box.innerHTML = state.offers.map((a, i) => `
    <div class="offer">
      <input type="search" list="aug-names" data-i="${i}" placeholder="${i + 1}번 증강 이름" value="${a ? esc(a.name) : ""}" ${full ? "disabled" : ""}>
      <label class="small"><input type="checkbox" data-r="${i}" ${state.rerolled[i] ? "checked" : ""}> 새로고침 씀</label>
    </div>`).join("");
  box.querySelectorAll("input[type=search]").forEach((el) => el.addEventListener("change", () => {
    const a = state.data.augments.find((x) => x.name === el.value.trim());
    state.offers[Number(el.dataset.i)] = a || null;
    if (!a && el.value.trim()) el.value = "";
    renderOfferResult();
  }));
  box.querySelectorAll("input[type=checkbox]").forEach((el) => el.addEventListener("change", () => {
    state.rerolled[Number(el.dataset.r)] = el.checked;
    renderOfferResult();
  }));
  renderOfferResult();
}

function renderOfferResult() {
  const out = $("#offer-result");
  const offers = state.offers.map((a, i) => (a ? { a, i } : null)).filter(Boolean);
  if (!state.champ || !offers.length) {
    out.innerHTML = state.chosen.length >= 4 ? `<p class="muted small">증강 4개를 다 골랐어요.</p>`
      : `<p class="muted small">게임에 뜬 증강 이름을 입력하면 고를 때마다 빌드·수치가 어떻게 바뀌는지 비교해요.</p>`;
    return;
  }
  const d = state.data, o = statOpts();
  const nowBal = S.buildConcepts(d.items, state.weights, state.chosen, state.items, d.rules, state.champ, o, ["balanced"])[0];
  const ranked = S.rankAugments(d.augments, state.weights, state.chosen, state.items, state.champ, d.rules);
  const { gains } = S.numericAugmentGains(d.augments, d.items, state.weights, state.chosen, state.items, d.rules, state.champ, o);
  // 같은 등급 안에서의 위치: 실제 숫자(빌드 가치 증가)로 평가할 수 있는 증강끼리 비교. 1등 = 상위 1/n
  const pctile = (aug) => {
    if (!gains[aug.id]) return null;
    const pool = d.augments.filter((x) => x.rarity === aug.rarity && gains[x.id]).sort((x, y) => gains[y.id].gain - gains[x.id].gain);
    const idx = pool.findIndex((x) => x.id === aug.id);
    return { top: (idx + 1) / pool.length, n: pool.length, rank: idx + 1 };
  };
  const rows = offers.map(({ a, i }) => {
    const sc = ranked.find((r) => r.aug.id === a.id) || { score: 0 };
    const aug = S.buildConcepts(d.items, state.weights, [...state.chosen, a], state.items, d.rules, state.champ, o, ["augment"])[0];
    const changed = aug.pick.filter((p) => !p.fixed && !nowBal.pick.some((q) => q.item.name === p.item.name)).length;
    return { a, i, sc, p: pctile(a), aug, changed };
  });
  // 추천: 숫자로 평가되는 선택지 중 빌드 가치 증가가 가장 큰 것. 셋 다 숫자가 없으면 효과 점수로
  const numRows = rows.filter((r) => gains[r.a.id]);
  const best = numRows.length ? numRows.reduce((x, y) => (gains[y.a.id].gain > gains[x.a.id].gain ? y : x))
    : rows.reduce((x, y) => (y.sc.score > x.sc.score ? y : x));
  const rerollable = rows.filter((r) => !state.rerolled[r.i] && r.p && r.p.top >= 1 - REROLL_PCT);
  const worst = rerollable.length ? rerollable.reduce((x, y) => (y.p.top > x.p.top ? y : x)) : null;
  const delta = (v, b) => {
    const dd = b > 0 ? (v - b) / b : 0;
    return Math.abs(dd) < 0.005 ? `<span class="muted">±0%</span>` : `<span class="${dd > 0 ? "up" : "down"}">${dd > 0 ? "+" : ""}${fmt(dd * 100)}%</span>`;
  };
  const nf = nowBal.final;
  out.innerHTML = `<table class="offer-table">
    <tr><th>선택지</th><th>빌드 가치</th><th>등급 내 위치 (숫자 기준)</th><th>콤보</th><th>DPS</th><th>유효 체력</th><th>몰빵 빌드</th><th></th></tr>
    ${rows.map((r) => {
      const f = r.aug.final;
      return `<tr class="${r === best ? "best" : ""}">
        <td><img src="icons/augment/${r.a.id}.png" alt=""> <span class="r${r.a.rarity}">${esc(r.a.name)}</span>
          ${r === best ? `<span class="chip syn">추천</span>` : ""}${r === worst ? `<span class="chip bad">새로고침 권장</span>` : ""}
          ${r.changed >= 3 ? `<span class="chip on">빌드 방향 전환</span>` : ""}
          ${r.sc.req && !r.sc.req.met ? `<span class="chip bad">조건 없음</span>` : ""}</td>
        <td>${gains[r.a.id] ? `<span class="${gains[r.a.id].gain >= 0 ? "up" : "down"}">${gains[r.a.id].gain >= 0 ? "+" : ""}${fmt(gains[r.a.id].gain * 100)}</span>` : `<span class="muted" title="효과 점수 ${r.sc.score.toFixed(2)}">숫자 없음</span>`}</td>
        <td>${r.p ? `${r.p.rank}위 / ${r.p.n}개 (상위 ${fmt(r.p.top * 100)}%)` : `<span class="muted">평가 불가</span>`}</td>
        <td>${fmt(f.combo)} ${delta(f.combo, nf.combo)}</td>
        <td>${fmt(f.dps)} ${delta(f.dps, nf.dps)}</td>
        <td>${fmt(f.ehp)} ${delta(f.ehp, nf.ehp)}</td>
        <td class="mini-build">${r.aug.pick.filter((p) => !p.fixed).map((p) => `<img src="icons/item/${p.item.id}.png" title="${esc(p.item.name)}" alt="">`).join("")}</td>
        <td><button data-pick="${r.i}">이걸로 선택</button></td></tr>`;
    }).join("")}</table>
    <p class="muted small">빌드 가치 = 지금 균형 빌드(완성 6칸, 18레벨)에 이 증강을 더했을 때 콤보·DPS·유효 체력·회복/보호막이 오르는 정도(가중합 ×100).
      등급 내 위치는 같은 등급에서 숫자로 평가되는 증강끼리 비교한 순위예요. "숫자 없음"은 무적·CC·재사용 대기시간 반환처럼 고정 수치가 없는 증강이라 직접 판단이 필요해요.
      콤보·DPS·유효 체력 칸은 그 증강에 맞춰 빌드를 다시 짰을 때(몰빵) 지금 대비 변화예요.
      새로고침 권장 = 새로고침을 안 쓴 칸 중 같은 등급 하위 ${REROLL_PCT * 100}% 안에 드는 가장 낮은 칸.</p>`;
  out.querySelectorAll("button[data-pick]").forEach((el) => el.addEventListener("click", () => {
    const a = state.offers[Number(el.dataset.pick)];
    if (a && state.chosen.length < 4 && !state.chosen.some((x) => x.id === a.id)) state.chosen.push(a);
    clearOffers();
    renderAll();
  }));
}

function renderAugs() {
  const list = $("#aug-list");
  if (!state.champ) { list.innerHTML = ""; return; }
  const featLabel = state.data.rules.features;
  let ranked = S.rankAugments(state.data.augments, state.weights, state.chosen, state.items, state.champ, state.data.rules);
  const { gains } = S.numericAugmentGains(state.data.augments, state.data.items, state.weights, state.chosen, state.items, state.data.rules, state.champ, statOpts());
  if (state.rarity !== "all") ranked = ranked.filter((r) => String(r.aug.rarity) === state.rarity);
  if (state.augQuery) ranked = ranked.filter((r) => r.aug.name.includes(state.augQuery) || r.aug.desc.includes(state.augQuery));
  const max = Math.max(...ranked.map((r) => r.score), 0.01);
  list.innerHTML = ranked.map((r) => `
    <div class="aug" data-id="${r.aug.id}">
      <img src="icons/augment/${r.aug.id}.png" alt="" loading="lazy">
      <div>
        <div class="name r${r.aug.rarity}">${esc(r.aug.name)} <span class="muted small">${r.aug.rarityName}</span>
          ${gains[r.aug.id] ? `<span class="chip ${gains[r.aug.id].gain >= 0 ? "syn" : "bad"}">빌드 가치 ${gains[r.aug.id].gain >= 0 ? "+" : ""}${fmt(gains[r.aug.id].gain * 100)}</span>` : `<span class="chip">숫자 없음</span>`}</div>
        <div>${r.generic ? `<span class="chip">범용 (챔피언 무관)</span>` : ""}${r.unclassified ? `<span class="chip">효과 분류 안 됨</span>` : ""}${r.parts.map((p) =>
          `<span class="chip ${p.synergy ? "syn" : ""}">${label(p.tag)} +${p.value.toFixed(2)}${p.synergy ? " 시너지" : ""}</span>`).join("")}${r.req
          ? `<span class="chip ${r.req.met ? "syn" : "bad"}">조건: ${esc(featLabel[r.req.feature] || r.req.feature)} ${r.req.met ? "충족" : "없음"}</span>` : ""}${r.itemMatch ? `<span class="chip syn">고른 아이템과 연계</span>` : ""}</div>
        ${r.note ? `<div class="note">${esc(r.note)}</div>` : ""}
        <div class="desc">${r.aug.desc}</div>
      </div>
      <div class="score">${r.score.toFixed(2)}<div class="bar"><i style="width:${(r.score / max) * 100}%"></i></div></div>
    </div>`).join("") || `<p class="muted">조건에 맞는 증강이 없어요.</p>`;
  list.querySelectorAll(".aug").forEach((el) => el.addEventListener("click", () => {
    if (state.chosen.length >= 4) return;
    state.chosen.push(state.data.augments.find((a) => a.id === el.dataset.id));
    renderAll();
  }));
}

function itemRow(r, extra = "") {
  return `<div class="item ${r.fixed ? "fixed" : ""}" data-id="${r.item.id}" title="${esc(r.item.desc.replace(/<[^>]+>/g, " "))}">
    <img src="icons/item/${r.item.id}.png" alt="">
    <div><div>${esc(r.item.name)} <span class="gold">${r.item.gold}G</span> <span class="muted small">${KIND_NAME[r.item.kind] || ""}</span>${r.fixed ? ` <span class="chip on">고정</span>` : ""}${r.mismatch ? ` <span class="chip">성향 반대</span>` : ""}</div>
    <div>${r.parts.slice(0, 4).map((p) => `<span class="chip">${label(p.tag)} +${p.value.toFixed(2)}</span>`).join("")}</div>
    ${r.reasons && r.reasons.length ? `<div class="reason">${r.reasons.map(esc).join(" · ")}</div>` : ""}
    ${gainLine(r.gain)}</div>
    <div class="score">${r.score.toFixed(2)}${extra}</div></div>`;
}

function gainLine(g) {
  if (!g) return "";
  const parts = Object.entries(g).filter(([, v]) => Math.abs(v) >= 0.01).sort((a, b) => b[1] - a[1])
    .map(([k, v]) => `<span class="${v > 0 ? "up" : "down"}">${METRIC_LABEL[k]} ${v > 0 ? "+" : ""}${fmt(v * 100)}%</span>`);
  return parts.length ? `<div class="gain">착용 시 ${parts.join(" · ")}</div>` : `<div class="gain muted">착용 시 수치 변화 없음 (고유 효과만)</div>`;
}

function canAdd(item) {
  if (state.items.some((i) => i.name === item.name)) return "이미 고른 아이템이에요.";
  if (item.kind === "boots" && state.items.some((i) => i.kind === "boots")) return "신발은 1개만 고를 수 있어요.";
  if (state.items.length >= S.SLOTS) return "아이템 칸(6칸)이 다 찼어요.";
  return "";
}

function addItem(id) {
  const item = state.data.items.find((i) => i.id === id);
  const why = canAdd(item);
  if (why) { alert(why); return; }
  state.items.push(item);
  renderAll();
}

function renderChosenItems() {
  const slots = Array.from({ length: S.SLOTS }, (_, i) => state.items[i]);
  $("#chosen-items").innerHTML = slots.map((it) => it
    ? `<div class="islot" data-id="${it.id}" title="${esc(it.name)} (해제)"><img src="icons/item/${it.id}.png" alt=""></div>`
    : `<div class="islot muted">빈 칸</div>`).join("");
  $("#chosen-items").querySelectorAll(".islot[data-id]").forEach((el) => el.addEventListener("click", () => {
    state.items = state.items.filter((i) => i.id !== el.dataset.id);
    renderAll();
  }));
}

function renderBuild() {
  if (!state.champ) { $("#build").innerHTML = ""; $("#concept-tabs").innerHTML = ""; $("#concept-desc").textContent = ""; return; }
  const builds = S.buildConcepts(state.data.items, state.weights, state.chosen, state.items, state.data.rules, state.champ, statOpts());
  const cur = builds.find((b) => b.key === state.concept) || builds[0];
  $("#concept-tabs").innerHTML = builds.map((b) =>
    `<button data-k="${b.key}" class="${b.key === cur.key ? "on" : ""}">${b.label}</button>`).join("");
  $("#concept-tabs").querySelectorAll("button").forEach((el) => el.addEventListener("click", () => {
    state.concept = el.dataset.k;
    renderBuild();
  }));
  $("#concept-desc").textContent = cur.desc + (state.chosen.length ? "" : " — 증강을 고르면 컨셉마다 빌드가 달라져요.");
  const f = cur.final;
  $("#build").innerHTML = (f ? `<div class="build-sum">완성 시 (18레벨): 콤보 <b>${fmt(f.combo)}</b> · DPS <b>${fmt(f.dps)}</b> · 유효 체력 <b>${fmt(f.ehp)}</b>${f.support ? ` · 회복·보호막 <b>${fmt(f.support)}</b>` : ""}</div>` : "")
    + cur.pick.map((r) => itemRow(r)).join("");
  $("#build").querySelectorAll(".item:not(.fixed)").forEach((el) =>
    el.addEventListener("click", () => addItem(el.dataset.id)));
}

function renderItemList() {
  if (!state.champ) { $("#item-list").innerHTML = ""; return; }
  const q = state.itemQuery;
  const rows = S.rankItems(state.data.items, state.weights, state.chosen, state.items, state.data.rules, state.champ, statOpts())
    .filter((r) => state.kind === "all" || r.item.kind === state.kind)
    .filter((r) => !q || r.item.name.includes(q));
  $("#item-list").innerHTML = rows.map((r) => itemRow(r, canAdd(r.item) ? `<div class="muted small">불가</div>` : "")).join("")
    || `<p class="muted">조건에 맞는 아이템이 없어요.</p>`;
  $("#item-list").querySelectorAll(".item").forEach((el) => el.addEventListener("click", () => addItem(el.dataset.id)));
}

function renderWeights() {
  if (!state.champ) { $("#weights").innerHTML = ""; return; }
  $("#weights").innerHTML = state.data.tags.map((t) => `
    <label>${label(t)}</label>
    <input type="range" min="0" max="1.5" step="0.05" value="${state.weights[t]}" data-t="${t}">
    <span>${state.weights[t].toFixed(2)}</span>`).join("");
  $("#weights").querySelectorAll("input").forEach((el) => el.addEventListener("input", () => {
    state.weights[el.dataset.t] = Number(el.value);
    el.nextElementSibling.textContent = Number(el.value).toFixed(2);
    renderChampCard();
    renderAugs();
    renderBuild();
    renderItemList();
  }));
}

// 데스크톱 프로그램(pywebview)에서만: 항상 위, 데이터 갱신
window.addEventListener("pywebviewready", async () => {
  const api = window.pywebview.api;
  const st = await api.get_state();
  $("#desktop-tools").hidden = false;
  $("#on-top").checked = st.onTop;
  $("#on-top").addEventListener("change", (e) => api.set_on_top(e.target.checked));
  const btn = $("#update-data");
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    btn.textContent = "확인 중…";
    const v = await api.check_update();
    if (v.error) { alert("최신 버전 확인 실패: " + v.error); btn.disabled = false; btn.textContent = "데이터 갱신"; return; }
    if (!v.outdated && !confirm(`이미 최신 데이터예요 (${v.current}).\n그래도 다시 받을까요?`)) { btn.disabled = false; btn.textContent = "데이터 갱신"; return; }
    btn.textContent = "받는 중… (몇 분)";
    const r = await api.update_data();
    if (!r.ok) { alert("갱신 실패\n" + r.log); btn.disabled = false; btn.textContent = "데이터 갱신"; }
  });
});

init();
