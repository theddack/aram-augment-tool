const S = window.Scoring;
const St = window.Stats;
const state = {
  data: null, champ: null, weights: null, chosen: [], rarity: "all", augQuery: "",
  items: [], kind: "all", itemQuery: "", concept: "augment", level: 18, aram: true,
  offers: [null, null, null], rerolled: [false, false, false], tierMode: "auto",
};
const REROLL_PCT = 0.4; // 같은 등급 안에서 하위 40% 이하면 새로고침 권장
const statOpts = () => ({ aram: state.aram });
const augStats = () => S.augStatsOf(state.chosen, state.data.rules, state.champ);
const fmt = (v, d = 0) => (Math.round(v * 10 ** d) / 10 ** d).toLocaleString("ko-KR", { minimumFractionDigits: d, maximumFractionDigits: d });
const KIND_LABEL = { magic: "마법 피해", physical: "물리 피해", true: "고정 피해", shield: "보호막", heal: "회복" };
const TRIGGER_LABEL = { cast: "스킬마다", onhit: "기본 공격마다", attacks: "기본 공격 N회마다", time: "주기적", once: "1회" };
// 처음 하는 사람도 알 수 있게 쉬운 말로 (정확한 용어는 마우스를 올리면 보임)
const METRIC_LABEL = { combo: "스킬 한 바퀴 피해", dps: "초당 피해", ehp: "버티는 힘", support: "회복·보호막" };
const METRIC_TERM = { combo: "콤보", dps: "DPS", ehp: "유효 체력", support: "회복·보호막" };
// 받침 따라 조사 고르기 (이/가, 을/를, 와/과)
function josa(word, withBatchim, without) {
  const c = String(word).trim().slice(-1).charCodeAt(0);
  if (c < 0xac00 || c > 0xd7a3) return word + without;
  return word + ((c - 0xac00) % 28 ? withBatchim : without);
}
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
  $("#reset-augs").addEventListener("click", () => { state.chosen = []; state.tierMode = "auto"; renderAll(); });
  $("#reset-offers").addEventListener("click", () => { clearOffers(); renderAll(); });
  $("#mini-toggle").addEventListener("click", () => setMini(!document.body.classList.contains("mini")));
  try { if (localStorage.getItem("mini") === "1") setMini(true); } catch (e) { /* 저장소를 못 쓰는 환경 */ }
  document.querySelectorAll("#tier-mode button").forEach((b) => b.addEventListener("click", () => {
    state.tierMode = b.dataset.m;
    renderAugs();
    renderOfferResult();
  }));
  $("#aug-names").innerHTML = d.augments.map((a) => `<option value="${esc(a.name)}">${a.rarityName}</option>`).join("");
  $("#reset-items").addEventListener("click", () => { state.items = []; renderAll(); });
  $("#reset-all").addEventListener("click", () => {
    state.chosen = [];
    state.items = [];
    state.concept = "augment";
    state.tierMode = "auto";
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
    const next = state.data.champions.find((c) => c.id === el.dataset.id);
    if (!state.champ || state.champ.id !== next.id) {
      // 챔피언이 바뀌면 새 게임: 고른 증강·선택지를 비운다 (아이템은 남겨서 비교할 수 있게)
      state.chosen = [];
      state.tierMode = "auto";
      clearOffers();
    }
    state.champ = next;
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
  return `<div><span class="muted small">${c.aramSource === "칼바람 + 증강 모드" ? "칼바람 + 증강 모드 보정" : "칼바람 보정"} (위키)</span> ${chips.join("")}</div>`;
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
  $("#metrics").innerHTML = tile("스킬 한 바퀴 피해 (콤보)", m.combo, base.combo, `방어 적용 전 ${fmt(m.comboRaw)}`)
    + tile("평타 초당 피해 (DPS)", m.autoDps, base.autoDps, `공속 ${fmt(e2.AS, 2)} · 치명 ${fmt(e2.CRIT * 100)}%`)
    + tile("버티는 힘 (유효 체력)", m.ehp, base.ehp, `물리 ${fmt(m.ehpPhys)} · 마법 ${fmt(m.ehpMag)}`)
    + tile("회복·보호막 (스킬 1회)", m.support, base.support, `보호막 ${fmt(m.shield)} · 회복 ${fmt(m.heal)}`);
  const rows = St.ROWS.filter(([k]) => Math.abs(e2[k] || 0) > 1e-6 || ["HP", "AD", "AP", "AS", "ARMOR", "MR", "MS"].includes(k));
  const cell = (v, k, d, pct, sign) => {
    if (Math.abs(v) < 1e-6) return sign ? `<td class="muted">-</td>` : `<td>0</td>`;
    const val = pct ? fmt(v * 100) + "%" : fmt(v, d);
    return `<td class="${sign ? (v > 0 ? "up" : "down") : ""}">${sign && v > 0 ? "+" : ""}${val}</td>`;
  };
  $("#stat-table").innerHTML = `<tr><th>능력치</th><th>기본 (${L}레벨)</th><th>아이템</th><th>증강</th><th>합계</th></tr>`
    + rows.map(([k, name, d, pct]) => `<tr><td>${name}</td>${cell(e0[k] || 0, k, d, pct)}${cell((e1[k] || 0) - (e0[k] || 0), k, d, pct, true)}${cell((e2[k] || 0) - (e1[k] || 0), k, d, pct, true)}<td><b>${pct ? fmt((e2[k] || 0) * 100) + "%" : fmt(e2[k] || 0, d)}</b></td></tr>`).join("");
  const effRows = m.effects.map((e) => `<tr><td>${esc(e.src)}</td><td><span class="chip k-${e.kind}">${KIND_LABEL[e.kind] || e.kind}</span></td>
      <td>${fmt(e.value)}</td><td>${e.hit != null ? fmt(e.hit) : "-"}</td><td class="muted">${TRIGGER_LABEL[e.trigger] || e.trigger}</td></tr>`).join("");
  $("#skills").innerHTML = (effRows ? `<table class="eff-table"><tr><th>패시브·아이템·증강 효과</th><th>종류</th><th>1회</th><th>방어 적용</th><th>발동</th></tr>${effRows}</table>
      <p class="muted small">콤보에는 4초 동안 발동 횟수만큼(쿨다운·기본 공격 횟수 제한) 들어가고, DPS에는 초당 발동 횟수로 들어가요.
      상대 체력 비례 피해는 상대 최대 체력 ${St.TARGET.HP}, 현재 체력 ${St.TARGET.CUR * 100}% 기준이에요.</p>` : "")
    + `<div class="skill"><b>패시브 · ${esc(c.passive.name)}</b><div class="desc">${c.passive.desc}</div></div>`
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

// 연계 티어는 계산이 무거워서 (챔피언·고른 증강·아이템·가중치·보정)이 같으면 재사용
let tierCache = { key: null, value: null };
// 첫 증강은 챔피언 기준(종합), 고른 뒤에는 고른 증강과의 연계 기준. 버튼으로 바꿀 수 있다
const tierMode = () => (state.tierMode === "auto" ? (state.chosen.length ? "synergy" : "total") : state.tierMode);
function getTiers() {
  const key = JSON.stringify([state.champ && state.champ.id, state.chosen.map((a) => a.id), state.items.map((i) => i.id), state.aram, state.weights, tierMode()]);
  if (tierCache.key !== key) {
    const d = state.data;
    tierCache = { key, value: S.augmentTiers(d.augments, d.items, state.weights, state.chosen, state.items, d.rules, state.champ, statOpts(), tierMode()) };
  }
  return tierCache.value;
}
const TIER_ORDER = { S: 0, A: 1, B: 2, C: 3, D: 4 };
// 모토 "어떤 증강도 재밌게": 낮은 평가는 막는 게 아니라 다른 방향(도전)으로 안내. 발동 자체가 안 되는 것만 '효과 없음'
const TIER_LABEL = { S: ["🔥", "강력 추천"], A: ["👍", "추천"], B: ["🙂", "무난"], C: ["🤔", "애매"], D: ["🎲", "도전"] };
const noEffect = (t) => t && t.reasons.includes("조건 없음");

// 플레이 방향: 증강 효과 종류로 판정 → 챔피언 원래 방향과 다르면 '변신'
const STYLE_LABEL = { dps: "원딜형", combo: "폭딜형", ehp: "탱커형", support: "서포터형", fighter: "전사형", mage: "마법사형", melee: "근접" };
const STYLE_TAGS = { dps: ["AS", "CRIT", "ONHIT"], combo: ["AP", "AD", "LETHAL", "MPEN", "ULT", "TRUE"], ehp: ["HP", "ARMOR", "MR"], support: ["HEALSHIELD"] };
function styleOf(tags) {
  const sum = {};
  for (const [k, list] of Object.entries(STYLE_TAGS)) sum[k] = list.reduce((a, t) => a + (tags[t] || 0), 0) / (k === "combo" ? 1.5 : 1);
  // 공격 + 체력을 같이 주면 전사형 (검을 뽑아라 등)
  if (sum.combo + sum.dps >= 0.5 && sum.ehp >= 0.5) return "fighter";
  let best = null, bestV = 0.5;
  for (const [k, v] of Object.entries(sum)) if (v > bestV) { best = k; bestV = v; }
  return best;
}
// 완성 빌드의 방향: 아이템 6개의 효과 종류를 합쳐서 판정 (주문력이면 '마법사형', 체력·방어면 '탱커형' …)
function buildStyle(itemIds) {
  const sum = {};
  for (const id of itemIds) {
    const it = state.data.items.find((i) => i.id === id);
    for (const [t, v] of Object.entries((it && it.tags) || {})) sum[t] = (sum[t] || 0) + v / itemIds.length;
  }
  if ((sum.AP || 0) >= 0.5 && (sum.AP || 0) >= (sum.AD || 0)) return (sum.HP || 0) + (sum.ARMOR || 0) + (sum.MR || 0) >= 0.9 ? "fighter" : "mage";
  return styleOf(sum);
}

function champStyle() {
  if ((state.champ.tags || []).includes("Fighter")) return "fighter";
  const mw = S.metricWeights(state.weights);
  return Object.entries(mw).sort((a, b) => b[1] - a[1])[0][0];
}
function transform(a) {
  const rule = state.data.rules.augments[a.id] || {};
  if (rule.setsFeatures && (rule.setsFeatures.add || []).includes("melee") && !state.champ.melee) return "melee";
  const st = styleOf(rule.tags || a.tags || {});
  return st && st !== champStyle() ? st : null;
}

function tierBadge(t) {
  if (!t) return "";
  const [icon, name] = noEffect(t) ? ["⛔", "효과 없음"] : TIER_LABEL[t.tier];
  const detail = `${t.tier}등급 · ${t.basis === "수치" ? "실제 숫자 기준" : "효과 종류·조건 기준"} · 같은 등급 ${t.rank}위/${t.n}개`
    + (t.interaction != null ? ` · 궁합 ${t.interaction > 0 ? "+" : ""}${fmt(t.interaction * 100)}` : "");
  return `<span class="tier t-${t.tier}" title="${detail}">${icon} ${name}</span>`;
}

function transformChip(a) {
  const tr = transform(a);
  return tr ? `<span class="chip on" title="이 증강을 고르면 ${state.champ.name}의 플레이 방향이 바뀌어요">🔄 ${josa(STYLE_LABEL[tr], "으로", "로")} 변신</span>` : "";
}

// 이유 한 줄: 데이터에서 자동으로 만든 쉬운 문장
function augReason(a, t, sc, g) {
  const feat = state.data.rules.features;
  const names = state.chosen.map((x) => x.name).join(" + ");
  const topPart = (sign) => g && Object.entries(g.parts || {}).filter(([, v]) => sign * v >= 0.01).sort((x, y) => sign * (y[1] - x[1]))[0];
  const partText = (p, word) => p ? `${josa(METRIC_LABEL[p[0]], "이", "가")} ${fmt(Math.abs(p[1]) * 100)}% ${word}` : "";
  if (!t) return "";
  if (t.reasons.includes("조건 없음") && sc && sc.req) {
    const f = sc.req.feature;
    const madeMelee = state.chosen.some((x) => ((state.data.rules.augments[x.id] || {}).setsFeatures || {}).add?.includes("melee"));
    if (f === "ranged") return `원거리 챔피언만 효과가 있어요. ${madeMelee ? "고른 증강 때문에 근접이 됐어요." : `${josa(state.champ.name, "은", "는")} 근접 챔피언이에요.`}`;
    if (f === "melee") return "근접 챔피언만 효과가 있어요.";
    if (f === "eligible") return `${josa(state.champ.name, "은", "는")} 이 증강이 적용되는 스킬이 없어요 (위키 목록 기준).`;
    return `${state.champ.name}에게는 ${josa(feat[f] || f, "이", "가")} 없어서 발동하지 않아요.`;
  }
  const tr = transform(a);
  const trText = tr ? ` 고르면 ${josa(STYLE_LABEL[tr], "으로", "로")} 가볼 수 있어요.` : "";
  if (t.reasons.includes("손해")) return `지금 빌드로는 ${partText(topPart(-1), "줄지만")}, 빌드를 바꾸면 색다르게 쓸 수 있어요.${trText}`;
  if (t.reasons.includes("연계 없음")) return `${josa(names, "과", "와")} 직접 연계는 없어요.${g && g.gain > 0 ? " 그래도 그 자체로는 강해요 (종합 보기에서 확인)." : ""}${trText}`;
  if (t.reasons.includes("충돌")) return `${josa(names, "과", "와")} 잘 안 맞아요. 서로 효과를 깎아먹어요.`;
  if (t.reasons.includes("궁합")) return `${josa(names, "과", "와")} 잘 맞아요. ${partText(topPart(1), "늘어요.")}`;
  if (state.chosen.length && tierMode() === "synergy" && g && t.tier >= "C") {
    return `그 자체로는 ${partText(topPart(1), "늘지만")} ${josa(names, "과", "와")} 연계는 약해요.${trText}`;
  }
  if (g) {
    const p = topPart(1);
    if (t.tier === "D") return `${state.champ.name}에게는 낯선 증강이에요.${trText || " 빌드를 바꿔서 도전해 보세요."}`;
    return p ? partText(p, "늘어요.") + trText : `고르면 수치 변화가 거의 없어요.${trText}`;
  }
  if (sc && sc.generic) return "챔피언과 상관없이 쓸 수 있는 범용 증강이에요 (골드·능력치 등).";
  const rule = (state.data.rules.augments[a.id] || {}).note;
  if (rule) return rule;
  if (sc && sc.parts && sc.parts.length) return `${label(sc.parts[0].tag)} 계열 효과예요.${trText}`;
  return "숫자로 계산할 수 없는 특수 효과예요. 설명을 읽고 재밌어 보이면 도전해 보세요!";
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
      <input type="search" list="aug-names" data-i="${i}" placeholder="${["왼쪽", "가운데", "오른쪽"][i]} 증강 이름" value="${a ? esc(a.name) : ""}" ${full ? "disabled" : ""}>
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
    pushOverlay([]);
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
  const tiers = getTiers();
  // 추천: 연계 티어가 가장 높은 것, 같으면 빌드 가치(없으면 효과 점수)가 큰 것
  const rank = (r) => [TIER_ORDER[(tiers[r.a.id] || {}).tier] ?? 9, -(gains[r.a.id] ? gains[r.a.id].gain : r.sc.score / 100)];
  const best = rows.slice().sort((x, y) => { const a = rank(x), b = rank(y); return a[0] - b[0] || a[1] - b[1]; })[0];
  const rerollable = rows.filter((r) => !state.rerolled[r.i] && r.p && r.p.top >= 1 - REROLL_PCT);
  const worst = rerollable.length ? rerollable.reduce((x, y) => (y.p.top > x.p.top ? y : x)) : null;
  const delta = (v, b) => {
    const dd = b > 0 ? (v - b) / b : 0;
    return Math.abs(dd) < 0.005 ? `<span class="muted">±0%</span>` : `<span class="${dd > 0 ? "up" : "down"}">${dd > 0 ? "+" : ""}${fmt(dd * 100)}%</span>`;
  };
  const nf = nowBal.final;
  out.innerHTML = `<table class="offer-table">
    <tr><th>선택지</th><th>평가</th><th title="빌드 가치">강해지는 정도</th><th>같은 등급 순위</th><th title="콤보">스킬 피해</th><th title="DPS">초당 피해</th><th title="유효 체력">버티는 힘</th><th>이걸 고르면 살 아이템</th><th></th></tr>
    ${rows.map((r) => {
      const f = r.aug.final;
      return `<tr class="${r === best ? "best" : ""}">
        <td><img src="icons/augment/${r.a.id}.png" alt=""> <span class="r${r.a.rarity}">${esc(r.a.name)}</span>
          ${r === best ? `<span class="chip syn">추천</span>` : ""}${r === worst ? `<span class="chip bad">새로고침 권장</span>` : ""}
          ${r.changed >= 3 && !transform(r.a) ? `<span class="chip on">빌드가 바뀌어요</span>` : ""}
          <div class="why">${transformChip(r.a)}${esc(augReason(r.a, tiers[r.a.id], r.sc, gains[r.a.id]))}</div></td>
        <td>${tierBadge(tiers[r.a.id])}</td>
        <td>${gains[r.a.id] ? `<span class="${gains[r.a.id].gain >= 0 ? "up" : "down"}">${gains[r.a.id].gain >= 0 ? "+" : ""}${fmt(gains[r.a.id].gain * 100)}</span>` : `<span class="muted" title="효과 점수 ${r.sc.score.toFixed(2)}">숫자 없음</span>`}</td>
        <td>${r.p ? `${r.p.rank}위 / ${r.p.n}개 (상위 ${fmt(r.p.top * 100)}%)` : `<span class="muted">평가 불가</span>`}</td>
        <td>${fmt(f.combo)} ${delta(f.combo, nf.combo)}</td>
        <td>${fmt(f.dps)} ${delta(f.dps, nf.dps)}</td>
        <td>${fmt(f.ehp)} ${delta(f.ehp, nf.ehp)}</td>
        <td class="mini-build">${r.aug.pick.filter((p) => !p.fixed).map((p) => `<img src="icons/item/${p.item.id}.png" title="${esc(p.item.name)}" alt="">`).join("")}</td>
        <td><button data-pick="${r.i}">이걸로 선택</button></td></tr>`;
    }).join("")}</table>
    <p class="muted small">강해지는 정도(빌드 가치) = 지금 빌드(완성 6칸, 18레벨)에 이 증강을 더했을 때 스킬 피해·초당 피해·버티는 힘·회복/보호막이 오르는 정도(가중합 ×100).
      등급 내 위치는 같은 등급에서 숫자로 평가되는 증강끼리 비교한 순위예요. "숫자 없음"은 무적·CC·재사용 대기시간 반환처럼 고정 수치가 없는 증강이라 직접 판단이 필요해요.
      콤보·DPS·유효 체력 칸은 그 증강에 맞춰 빌드를 다시 짰을 때(몰빵) 지금 대비 변화예요.
      새로고침 권장 = 새로고침을 안 쓴 칸 중 같은 등급 하위 ${REROLL_PCT * 100}% 안에 드는 가장 낮은 칸.
      추천 = 연계 티어가 가장 높은 것(같으면 빌드 가치 순).</p>`;
  pushOverlay(rows.map((r) => ({ r, t: tiers[r.a.id], best: r === best, reroll: r === worst, reason: augReason(r.a, tiers[r.a.id], r.sc, gains[r.a.id]) })));
  out.querySelectorAll("button[data-pick]").forEach((el) => el.addEventListener("click", () => {
    const a = state.offers[Number(el.dataset.pick)];
    if (a && state.chosen.length < 4 && !state.chosen.some((x) => x.id === a.id)) state.chosen.push(a);
    clearOffers();
    renderAll();
  }));
}

// ---- 게임 화면 표시 (윈도우 프로그램): 카드 밑 평가 + 오른쪽 위 상자
let overlayCards = [null, null, null];
function pushOverlay(rows) {
  overlayCards = [null, null, null];
  for (const x of rows || []) {
    const t = x.t;
    const [icon, label] = noEffect(t) ? ["⛔", "효과 없음"] : t ? TIER_LABEL[t.tier] : ["", ""];
    const tag = x.best ? " · 추천" : x.reroll ? " · 새로고침?" : "";
    const tr = transform(x.r.a);
    overlayCards[x.r.i] = {
      name: x.r.a.name, icon, label: label + tag, tier: noEffect(t) ? "X" : t && t.tier,
      // 게임 화면은 짧게: '고르면 ○○형으로 가볼 수 있어요'는 변신 표시와 겹쳐서 뺀다
      reason: (tr ? `🔄 ${josa(STYLE_LABEL[tr], "으로", "로")} 변신 · ` : "") + x.reason.replace(/\s*고르면 .*?가볼 수 있어요\./, ""),
    };
  }
  sendOverlay();
}
function sendOverlay() {
  const api = window.pywebview && window.pywebview.api;
  if (!api || !api.set_overlay || !state.champ) return;
  const hud = [];
  if (state.chosen.length) hud.push("고른 증강: " + state.chosen.map((a) => a.name).join(", "));
  const next = [...document.querySelectorAll("#next-buy .next-row")].map((e) => e.title).slice(0, 3);
  if (next.length) hud.push("다음 구매: " + next.join(" > "));
  api.set_overlay({ cards: overlayCards, hud });
}

function renderAugs() {
  const list = $("#aug-list");
  if (!state.champ) { list.innerHTML = ""; return; }
  const featLabel = state.data.rules.features;
  let ranked = S.rankAugments(state.data.augments, state.weights, state.chosen, state.items, state.champ, state.data.rules);
  const { gains } = S.numericAugmentGains(state.data.augments, state.data.items, state.weights, state.chosen, state.items, state.data.rules, state.champ, statOpts());
  const tiers = getTiers();
  ranked.sort((x, y) => (TIER_ORDER[(tiers[x.aug.id] || {}).tier] ?? 9) - (TIER_ORDER[(tiers[y.aug.id] || {}).tier] ?? 9)
    || ((gains[y.aug.id] || {}).gain ?? y.score / 100) - ((gains[x.aug.id] || {}).gain ?? x.score / 100));
  $("#aug-title").textContent = state.chosen.length ? "다음 증강 추천" : "첫 증강 추천";
  $("#tier-basis").textContent = state.chosen.length
    ? (tierMode() === "synergy" ? `${josa(state.chosen.map((a) => a.name).join(" + "), "과", "와")} 연계되는 순서` : `${state.chosen.map((a) => a.name).join(" + ")} 포함 종합`)
    : `${state.champ.name} 기준`;
  $("#tier-mode").hidden = !state.chosen.length;
  document.querySelectorAll("#tier-mode button").forEach((x) => x.classList.toggle("on", x.dataset.m === tierMode()));
  if (state.rarity !== "all") ranked = ranked.filter((r) => String(r.aug.rarity) === state.rarity);
  if (state.augQuery) ranked = ranked.filter((r) => r.aug.name.includes(state.augQuery) || r.aug.desc.includes(state.augQuery));
  const max = Math.max(...ranked.map((r) => r.score), 0.01);
  list.innerHTML = ranked.map((r) => `
    <div class="aug" data-id="${r.aug.id}">
      <img src="icons/augment/${r.aug.id}.png" alt="" loading="lazy">
      <div>
        <div class="name r${r.aug.rarity}">${tierBadge(tiers[r.aug.id])} ${esc(r.aug.name)} <span class="muted small">${r.aug.rarityName}</span>
          ${gains[r.aug.id] ? `<span class="chip ${gains[r.aug.id].gain >= 0 ? "syn" : "bad"}" title="빌드 가치">강해지는 정도 ${gains[r.aug.id].gain >= 0 ? "+" : ""}${fmt(gains[r.aug.id].gain * 100)}</span>` : ""}</div>
        <div class="why">${transformChip(r.aug)}${esc(augReason(r.aug, tiers[r.aug.id], r, gains[r.aug.id]))}</div>
        <div class="tags">${r.generic ? `<span class="chip">범용 (챔피언 무관)</span>` : ""}${r.unclassified ? `<span class="chip">효과 분류 안 됨</span>` : ""}${r.parts.map((p) =>
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

// 아이템 이유 "검을 뽑아라: 공격력" → "검을 뽑아라와 연계: 공격력"
function itemReason(r) {
  if (!r.reasons || !r.reasons.length) return "";
  const by = {};
  for (const t of r.reasons) {
    const m = t.match(/^(.+?): (.+)$/);
    if (m) (by[m[1]] = by[m[1]] || []).push(m[2]); else (by._ = by._ || []).push(t);
  }
  return Object.entries(by).map(([k, v]) => (k === "_" ? v.join(", ") : `${josa(k, "과", "와")} 연계: ${v.join("·")}`)).join(" / ");
}

function itemRow(r, extra = "") {
  const tip = (r.item.passiveHtml ? r.item.passiveHtml + " " : "") + r.item.desc;
  return `<div class="item ${r.fixed ? "fixed" : ""}" data-id="${r.item.id}" title="${esc(tip.replace(/<br>/g, "\n").replace(/<[^>]+>/g, "").replace(/[ \t]+/g, " "))}">
    <img src="icons/item/${r.item.id}.png" alt="">
    <div><div>${esc(r.item.name)} <span class="gold">${r.item.gold}G</span> <span class="muted small">${KIND_NAME[r.item.kind] || ""}${r.item.limit ? ` · ${LIMIT_NAME[r.item.limit] || r.item.limit} 계열` : ""}</span>${r.fixed ? ` <span class="chip on">고정</span>` : ""}${r.mismatch ? ` <span class="chip">성향 반대</span>` : ""}</div>
    <div>${r.parts.slice(0, 4).map((p) => `<span class="chip">${label(p.tag)} +${p.value.toFixed(2)}</span>`).join("")}</div>
    ${r.synergy ? `<span class="chip syn">🔗 연계 추천</span>` : ""}
    ${r.reasons && r.reasons.length ? `<div class="reason">${esc(itemReason(r))}</div>` : ""}
    ${gainLine(r.gain)}</div>
    <div class="score">${r.score.toFixed(2)}${extra}</div></div>`;
}

function gainLine(g) {
  if (!g) return "";
  const parts = Object.entries(g).filter(([, v]) => Math.abs(v) >= 0.01).sort((a, b) => b[1] - a[1])
    .map(([k, v]) => `<span class="${v > 0 ? "up" : "down"}" title="${METRIC_TERM[k]}">${METRIC_LABEL[k]} ${v > 0 ? "+" : ""}${fmt(v * 100)}%</span>`);
  return parts.length ? `<div class="gain">착용 시 ${parts.join(" · ")}</div>` : `<div class="gain muted">착용 시 수치 변화 없음 (고유 효과만)</div>`;
}

const LIMIT_NAME = { Spellblade: "주문 검", Hydra: "히드라", Fatality: "치명", Lifeline: "생명선", Manaflow: "마나 흐름",
  Immolate: "불사르기", Annul: "무효화", Blight: "역병", Eternity: "영원", Quicksilver: "수은", Stasis: "경직",
  Guardian: "수호자", "Soul Anchor": "영혼 닻", Elixir: "영약" };
function canAdd(item) {
  if (state.items.some((i) => i.name === item.name)) return "이미 고른 아이템이에요.";
  const same = item.limit && state.items.find((i) => i.limit === item.limit);
  if (same) return `${LIMIT_NAME[item.limit] || item.limit} 계열 고유 효과는 하나만 적용돼요. 이미 ${josa(same.name, "을", "를")} 골랐어요.`;
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

function setMini(on) {
  document.body.classList.toggle("mini", on);
  $("#mini-toggle").textContent = on ? "크게 보기" : "작게 보기";
  try { localStorage.setItem("mini", on ? "1" : "0"); } catch (e) { /* 무시 */ }
  if (window.pywebview && window.pywebview.api && window.pywebview.api.set_mini) window.pywebview.api.set_mini(on);
  renderBuild();
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
  // 작게 보기용 한 줄 요약: 컨셉 3개의 빌드 아이콘
  $("#mini-build").innerHTML = `<h2>추천 빌드</h2>` + builds.map((b) => `<div class="mini-row"><b style="min-width:52px">${b.label}</b>
    ${b.pick.map((p) => `<img src="icons/item/${p.item.id}.png" title="${esc(p.item.name)}" alt="">`).join("")}</div>`).join("");
  const f = cur.final;
  $("#build").innerHTML = (f ? `<div class="build-sum">완성 시 (18레벨): 스킬 한 바퀴 피해 <b>${fmt(f.combo)}</b> · 초당 피해 <b>${fmt(f.dps)}</b> · 버티는 힘 <b>${fmt(f.ehp)}</b>${f.support ? ` · 회복·보호막 <b>${fmt(f.support)}</b>` : ""}</div>` : "")
    + cur.pick.map((r) => itemRow(r)).join("");
  $("#build").querySelectorAll(".item:not(.fixed)").forEach((el) =>
    el.addEventListener("click", () => addItem(el.dataset.id)));
}

// 증강을 골랐으면 그 증강과 연계되는 아이템을 위로 (증강 몰빵 기준 점수)
const LINK_TOP = 6;
function itemRanking() {
  const d = state.data;
  const rows = S.rankItems(d.items, state.weights, state.chosen, state.items, d.rules, state.champ, statOpts(), state.chosen.length ? "augment" : "balanced");
  if (state.chosen.length) {
    const syn = S.itemSynergy(d.items, state.weights, state.chosen, state.items, d.rules, state.champ, statOpts());
    const ctx = S.augContext(state.chosen, d.rules);
    const links = rows.filter((r) => r.item.kind !== "special" || ctx.wanted[r.item.name])
      .filter((r) => syn[r.item.id] && syn[r.item.id].delta > 0.1)
      .sort((a, b) => syn[b.item.id].delta - syn[a.item.id].delta).slice(0, LINK_TOP).map((r) => r.item.id);
    for (const r of rows) r.synergy = links.includes(r.item.id) || !!ctx.wanted[r.item.name];
  }
  return rows;
}

function renderNextBuy(rows) {
  const box = $("#next-buy");
  if (!state.champ) { box.innerHTML = ""; return; }
  const hasBoots = state.items.some((i) => i.kind === "boots");
  const ctx = S.augContext(state.chosen, state.data.rules);
  const pick = rows.filter((r) => !canAdd(r.item))
    .filter((r) => r.item.kind === "legendary" || (r.item.kind === "boots" && !hasBoots) || (r.item.kind === "special" && ctx.wanted[r.item.name]))
    .slice(0, 3);
  box.innerHTML = `<h2>다음에 살 아이템 <span class="muted small">${state.chosen.length ? "(고른 증강 연계 우선)" : ""}</span></h2>`
    + (pick.length ? pick.map((r, i) => `<div class="next-row" data-id="${r.item.id}" title="${esc(r.item.name)}">
      <b>${i + 1}</b><img src="icons/item/${r.item.id}.png" alt=""><div><div>${esc(r.item.name)} <span class="gold">${r.item.gold}G</span>${r.synergy ? ` <span class="chip syn">🔗 연계</span>` : ""}</div>
      <div class="small muted">${esc(itemReason(r) || "")}</div>${gainLine(r.gain)}</div></div>`).join("") : `<p class="muted small">아이템 칸이 다 찼어요.</p>`);
  box.querySelectorAll(".next-row").forEach((el) => el.addEventListener("click", () => addItem(el.dataset.id)));
  sendOverlay();
}

function renderItemList() {
  if (!state.champ) { $("#item-list").innerHTML = ""; renderNextBuy([]); return; }
  const q = state.itemQuery;
  const all = itemRanking();
  renderNextBuy(all);
  const rows = all
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

// ---- 상점 추천 등록 (아이템 세트): 챔피언이 정해지면 한 번. 블록 = 기본 / 첫 증강 상위 3개별 / 변칙
let itemSetDone = null;
function itemSetBlocks(champ) {
  const d = state.data;
  const w = S.profile(champ, d.tags);
  const ids = (b) => b.pick.map((p) => p.item.id);
  const [aug, bal, twist] = S.buildConcepts(d.items, w, [], [], d.rules, champ, statOpts());
  const tiers = S.augmentTiers(d.augments, d.items, w, [], [], d.rules, champ, statOpts(), "total");
  // 첫 증강 후보(🔥·👍) 중 빌드가 확실히 달라지는 것만 블록으로: 기존 블록과 아이템이 2개 이상 달라야 넣는다
  const cands = d.augments.filter((a) => tiers[a.id] && ["S", "A"].includes(tiers[a.id].tier) && !(d.rules.augments[a.id] || {}).generic)
    .sort((x, y) => (tiers[y.id].gain ?? -1) - (tiers[x.id].gain ?? -1)).slice(0, 15);
  const blocks = [{ type: `🎯 기본 빌드${buildStyle(ids(bal)) ? ` (${STYLE_LABEL[buildStyle(ids(bal))]})` : ""}`, items: ids(bal) }];
  const differs = (items) => blocks.every((b) => items.filter((i) => !b.items.includes(i)).length >= 2);
  const twistIds = ids(twist);
  for (const a of cands) {
    if (blocks.length >= 4) break;
    const items = ids(S.buildConcepts(d.items, w, [a], [], d.rules, champ, statOpts(), ["augment"])[0]);
    if (!differs(items) || items.filter((i) => !twistIds.includes(i)).length < 2) continue;
    const st = buildStyle(items);
    blocks.push({ type: `🔥 ${a.name} 고르면${st ? ` (${STYLE_LABEL[st]})` : ""}`, items });
  }
  if (differs(twistIds)) blocks.push({ type: `🎲 변칙${buildStyle(twistIds) ? ` (${STYLE_LABEL[buildStyle(twistIds)]})` : ""}`, items: twistIds });
  return blocks;
}
async function applyLcu(api, L) {
  const el = $("#itemset-status");
  if (!$("#itemset-toggle").checked) { el.textContent = ""; return; }
  if (!L || !L.connected) { el.textContent = "· 롤 클라이언트 대기"; return; }
  const champ = state.data.champions.find((c) => Number(c.key) === Number(L.championKey));
  if (!champ) { el.textContent = `· ${L.phase === "ChampSelect" ? "챔피언 선택 대기" : "클라이언트 연결됨"}`; return; }
  if (itemSetDone === champ.id) return;
  itemSetDone = champ.id;
  el.textContent = `· ${champ.name} 상점 추천 만드는 중…`;
  const r = await api.write_item_set(champ.key, `칼바람 증강 계산기 · ${champ.name}`, itemSetBlocks(champ));
  el.textContent = r.ok ? `· ${champ.name} 상점 추천 등록됨 (${r.blocks}개 블록)` : `· 등록 실패: ${r.error}`;
  if (!r.ok) itemSetDone = null;
}

// 게임 연동: 챔피언·레벨·아이템을 게임에 맞춘다 (증강은 직접 고름). 같은 값이면 다시 그리지 않는다
let liveLast = "";
function applyLive(L) {
  const el = $("#live-status");
  if (!L || !L.enabled) { el.textContent = ""; return; }
  if (!L.inGame) { el.textContent = "게임 대기 중"; return; }
  const d = state.data;
  const champ = d.champions.find((c) => c.id === L.champion);
  const items = (L.items || []).map((id) => d.items.find((i) => i.id === id)).filter(Boolean)
    .filter((it, i, arr) => arr.findIndex((x) => x.name === it.name) === i).slice(0, S.SLOTS);
  const level = Math.max(1, Math.min(18, L.level || 1));
  el.textContent = `게임 중 · ${L.championName || L.champion || "?"} ${level}레벨`
    + (L.augmentFields && L.augmentFields.length ? ` · 증강 필드 발견(${L.augmentFields.length})` : "");
  el.title = (L.augmentFields && L.augmentFields.length ? "API 증강 필드: " + L.augmentFields.slice(0, 8).join(", ") + "\n" : "")
    + `원본 저장 위치: ${L.logDir}`;
  const key = JSON.stringify([champ && champ.id, level, items.map((i) => i.id)]);
  if (key === liveLast) return;
  liveLast = key;
  if (champ && (!state.champ || state.champ.id !== champ.id)) {
    state.champ = champ;
    state.weights = S.profile(champ, d.tags);
    state.chosen = [];
    clearOffers();
    renderChamps();
  }
  state.level = level;
  $("#level").value = level;
  $("#level-val").textContent = level;
  state.items = items;
  renderAll();
}

// 데스크톱 프로그램(pywebview)에서만: 항상 위, 데이터 갱신
window.addEventListener("pywebviewready", async () => {
  const api = window.pywebview.api;
  const st = await api.get_state();
  $("#desktop-tools").hidden = false;
  $("#on-top").checked = st.onTop;
  $("#on-top").addEventListener("change", (e) => api.set_on_top(e.target.checked));
  if (st.overlaySupported) {
    $("#overlay-wrap").hidden = false;
    $("#overlay-pos").hidden = false;
    $("#overlay-toggle").checked = st.overlay;
    $("#overlay-toggle").addEventListener("change", (e) => api.set_overlay_enabled(e.target.checked));
    $("#ov-y").value = st.overlayPos.y;
    $("#ov-spread").value = st.overlayPos.spread;
    const pos = () => { api.set_overlay_pos(Number($("#ov-y").value), Number($("#ov-spread").value)); sendOverlay(); };
    $("#ov-y").addEventListener("input", pos);
    $("#ov-spread").addEventListener("input", pos);
    $("#ov-preview").addEventListener("change", (e) => {
      api.set_overlay_preview(e.target.checked);
      // 미리 보기: 선택지가 비어 있으면 예시 카드로 위치를 맞출 수 있게
      if (e.target.checked && !overlayCards.some(Boolean)) {
        overlayCards = ["S", "D", "B"].map((t, i) => ({ name: "예시", icon: TIER_LABEL[t][0], label: TIER_LABEL[t][1] + " (위치 맞추기)", tier: t, reason: ["왼쪽", "가운데", "오른쪽"][i] + " 카드 밑에 오면 맞아요" }));
      }
      if (!e.target.checked) renderOfferResult();
      sendOverlay();
    });
    setInterval(async () => { const o = await api.overlay_status(); $("#overlay-status").textContent = `(${o.status})`; }, 1500);
  }
  $("#itemset-toggle").checked = st.itemSets;
  $("#itemset-toggle").addEventListener("change", (e) => { api.set_item_sets(e.target.checked); itemSetDone = null; });
  setInterval(async () => applyLcu(api, await api.get_lcu()), 3000);
  $("#live-toggle").checked = st.live;
  $("#live-toggle").addEventListener("change", (e) => api.set_live(e.target.checked));
  setInterval(async () => applyLive(await api.get_live()), 3000);
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
