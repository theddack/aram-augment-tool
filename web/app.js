const S = window.Scoring;
const St = window.Stats;
const state = {
  data: null, champ: null, weights: null, chosen: [], rarity: "all", augQuery: "",
  items: [], kind: "all", itemQuery: "", concept: "augment", level: 18, aram: true,
  offers: [null, null, null], rerolled: [false, false, false], tierMode: "auto", target: "",
  gold: null, inventory: [],  // 게임 연동: 지금 골드, 가방 속 아이템 전체(부품 포함)
};
const REROLL_PCT = 0.4; // 같은 등급 안에서 하위 40% 이하면 새로고침 권장
// 받는 피해의 마법 비율은 게임 중 적 구성으로 (모르면 계산 쪽 기본값 반반). 0.1 단위로 맞춰 기억(memo)이 잘 맞게
const statOpts = () => {
  const e = enemyInfo();
  // 적이 전부 마법이어도 0.8 까지만: 끝까지 밀면 다음 구매가 마법 저항 탱템뿐이라 딜템이 사라졌다 (반영 60%)
  return e ? { aram: state.aram, enemyMagic: Math.round((0.5 + (e.magic - 0.5) * 0.6) * 10) / 10 } : { aram: state.aram };
};
const augStats = () => S.augStatsOf(state.chosen, state.data.rules, state.champ);

// 적 구성 (게임 중 데이터): 주 피해 유형 비율, 회복 많은 적, 탱커
function enemyInfo() {
  const list = state.enemies || [];
  if (!list.length) return null;
  const share = { magic: 1, physical: 0, mixed: 0.5 };
  const magic = list.reduce((a, c) => a + (share[c.damageType] ?? 0.5), 0) / list.length;
  // 회복 많은 적: 스킬·패시브 중 회복 효과가 2개 이상 (특성 HEALSHIELD 는 보호막도 섞여 럭스까지 잡혔다)
  const heals = (c) => (c.spells || []).filter((sp) => (sp.effects || []).some((x) => x && x.kind === "heal")).length
    + (((c.passive || {}).effects || []).some((x) => x && x.kind === "heal") ? 1 : 0);
  const healers = list.filter((c) => heals(c) >= 2);
  const tanks = list.filter((c) => (c.tags || []).includes("Tank"));
  return { list, magic, healers, tanks };
}

// 적 구성 조언 (게임 화면 상자·애드온): 받는 피해 쪽 방어, 회복 많으면 치유 감소, 탱커 많으면 관통
function enemyAdvice() {
  const e = enemyInfo();
  if (!e || !state.champ) return [];
  const out = [];
  const pct = Math.round(e.magic * 100);
  out.push(`적 피해: 마법 ${pct}% · 물리 ${100 - pct}%` + (pct >= 65 ? " → 마법 저항 우선" : pct <= 35 ? " → 방어력 우선" : ""));
  const items = state.data.items.filter((i) => i.kind === "legendary");
  const mineType = state.champ.damageType === "magic" ? "magic" : "physical";
  // 내 챔피언이 살릴 수 있는 아이템만 (주 피해 유형, 치명타는 쓰는 챔피언만): 다리우스에게 필멸자의 운명·모렐로를 권하던 것
  const critOk = state.champ.tags.includes("Marksman") || !state.champ.melee || ((state.champ.traits || {}).CRIT || 0) > 0;
  const hasCrit = (i) => (i.tags.CRIT || 0) >= 0.5 || (i.stats && !Array.isArray(i.stats) && "CRIT" in i.stats);
  const fits = (i) => (mineType === "magic" ? (i.tags.AP || 0) >= 0.5 : (i.tags.AD || 0) >= 0.5 && (critOk || !hasCrit(i)));
  if (e.healers.length >= 2) {
    // 치유 감소(고통스러운 상처)가 설명에 있는 아이템 중 내 유형에 맞는 것
    let gw = items.filter((i) => JSON.stringify([i.desc, i.passiveHtml]).includes("고통스러운 상처") && fits(i)).map((i) => i.name);
    // 맞는 완성 아이템이 없으면(치명타를 못 살리는 전사 등) 치유 감소 부품을 (처형인의 대검 · 망각의 구 · 덤불 조끼)
    if (!gw.length) {
      const want = mineType === "magic" ? ["망각의 구"] : ["처형인의 대검"];
      if (state.champ.tags.includes("Tank")) want.push("덤불 조끼");
      gw = Object.values(state.data.parts || {}).filter((pt) => want.includes(pt.name)).map((pt) => `${pt.name}(부품 ${pt.gold}G)`);
    }
    out.push(`적 회복 많음 (${e.healers.map((c) => c.name).join("·")})` + (gw.length ? ` → 치유 감소: ${gw.join(" 또는 ")}` : ""));
  }
  if (e.tanks.length >= 2) {
    const pen = items.filter((i) => fits(i) && (mineType === "magic" ? (i.tags.MPEN || 0) >= 0.5
      : i.stats && !Array.isArray(i.stats) && "ARPEN" in i.stats)).map((i) => i.name).slice(0, 2);
    out.push(`적 탱커 많음 (${e.tanks.map((c) => c.name).join("·")})` + (pen.length ? ` → 관통: ${pen.join(" 또는 ")}` : ""));
  }
  return out;
}
const fmt = (v, d = 0) => (Math.round(v * 10 ** d) / 10 ** d).toLocaleString("ko-KR", { minimumFractionDigits: d, maximumFractionDigits: d });
const KIND_LABEL = { magic: "마법 피해", physical: "물리 피해", true: "고정 피해", shield: "보호막", heal: "회복" };
const TRIGGER_LABEL = { cast: "스킬마다", onhit: "기본 공격마다", attacks: "기본 공격 N회마다", time: "주기적", once: "1회" };
// 처음 하는 사람도 알 수 있게 쉬운 말로 (정확한 용어는 마우스를 올리면 보임)
const METRIC_LABEL = { combo: "스킬 한 바퀴 피해", dps: "초당 피해", ehp: "버티는 힘", support: "회복·보호막", mobility: "기동성", control: "제어력" };
const METRIC_TERM = { combo: "콤보", dps: "DPS", ehp: "유효 체력", support: "회복·보호막", mobility: "평균 이동 속도", control: "콤보당 CC 시간(둔화 30%)" };
// 받침 따라 조사 고르기 (이/가, 을/를, 와/과)
function josa(word, withBatchim, without) {
  const c = String(word).trim().slice(-1).charCodeAt(0);
  if (c < 0xac00 || c > 0xd7a3) return word + without;
  return word + ((c - 0xac00) % 28 ? withBatchim : without);
}
const KIND_NAME = { legendary: "전설", boots: "신발", special: "증강 보상" };
const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);
const label = (t) => esc(S.TAG_LABEL[t] || t);  // 데이터에서 온 태그 이름도 이스케이프
// 아이콘 경로: 데이터를 만들 때 WebP 로 바꿨으면 webp, 아니면 png (프로그램의 '데이터 갱신')
let webpOnly = null;  // PNG 로 받은 데이터에서 WebP 만 남은 아이콘
const icon = (kind, id) => {
  const d = state.data;
  if (d && !webpOnly) webpOnly = new Set(d.iconWebpOnly || []);
  const ext = webpOnly && webpOnly.has(`${kind}/${id}`) ? "webp" : (d && d.iconExt) || "png";
  return `icons/${kind}/${id}.${ext}`;
};

// 근거·패치 펼치기를 눌렀을 때 증강·아이템이 같이 선택되지 않게
document.addEventListener("click", (e) => {
  if (e.target.closest && e.target.closest(".basis, details.patch, .refs a")) e.stopPropagation();
}, true);

// 받은 데이터의 id 검사: id 는 HTML 속성과 아이콘 파일 경로에 그대로 들어가서, 영문·숫자·밑줄이 아니면 뺀다
// (데이터를 만들 때도 같은 검사를 하지만, 사용자 폴더의 data.json 이 바뀌었을 때를 대비해 화면에서 한 번 더)
function sanitizeData(d) {
  const okId = (x) => /^[A-Za-z0-9_]{1,80}$/.test(String(x));
  d.champions = (d.champions || []).filter((c) => okId(c.id) && /^\d+$/.test(String(c.key)));
  d.augments = (d.augments || []).filter((a) => okId(a.id));
  d.items = (d.items || []).filter((i) => /^\d{1,8}$/.test(String(i.id)));
  return d;
}

async function init() {
  const res = await fetch("data.json");
  state.data = sanitizeData(await res.json());
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
  $("#simple-toggle").addEventListener("click", () => setSimple(!document.body.classList.contains("simple")));
  let simple = true;  // 처음엔 쉽게 보기
  try { simple = localStorage.getItem("simple") !== "0"; } catch (e) { /* 저장소를 못 쓰는 환경 */ }
  setSimple(simple, false);
  try { if (localStorage.getItem("mini") === "1") setMini(true); } catch (e) { /* 저장소를 못 쓰는 환경 */ }
  document.querySelectorAll("#tier-mode button").forEach((b) => b.addEventListener("click", () => {
    state.tierMode = b.dataset.m;
    renderAugs();
    renderOfferResult();
  }));
  $("#aug-names").innerHTML = d.augments.map((a) => `<option value="${esc(a.name)}">${esc(a.rarityName)}</option>`).join("");
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
  $("#aram-toggle").addEventListener("change", (e) => { state.aram = e.target.checked; setTargetChamp(state.target); renderAll(); });
  $("#target-champ").innerHTML += d.champions.map((c) => `<option value="${c.id}">${esc(c.name)}</option>`).join("");
  $("#target-champ").addEventListener("change", (e) => { setTargetChamp(e.target.value); renderAll(); });
  setTargetChamp("");
  $("#stat-panel").querySelector("p").innerHTML = $("#stat-panel").querySelector("p").innerHTML
    .replace("${TARGET_ARMOR}", St.TARGET.ARMOR).replace("${TARGET_MR}", St.TARGET.MR);
  $("#reset-weights").addEventListener("click", () => {
    if (state.champ) { state.weights = S.profile(state.champ, d.tags); renderAll(); }
  });
  renderChamps();
  renderAll();
}

// 기준 상대: 고정값 또는 실제 챔피언의 18레벨 균형 빌드 능력치 (체력·방어력·마법 저항력)
function setTargetChamp(id) {
  const d = state.data;
  const enemy = d.champions.find((c) => c.id === id);
  state.target = enemy ? id : "";
  if (!enemy) {
    St.setTarget(null);
  } else {
    St.setTarget(null);  // 상대의 빌드는 기준 상대로 계산 (앞에 고른 상대에 따라 달라지지 않게)
    const [b] = S.buildConcepts(d.items, S.profile(enemy, d.tags), [], [], d.rules, enemy, statOpts(), ["balanced"]);
    const t = St.computeStats(enemy, 18, b.pick.map((p) => p.item), [], statOpts()).total;
    St.setTarget({ ARMOR: Math.round(t.ARMOR), MR: Math.round(t.MR), HP: Math.round(t.HP) });
  }
  const T = St.TARGET;
  $("#target-info").textContent = `${enemy ? enemy.name + " 18레벨 추천 빌드 기준" : "기준 상대 (전 챔피언 중앙값)"}: 체력 ${fmt(T.HP)} · 방어력 ${fmt(T.ARMOR)} · 마법 저항력 ${fmt(T.MR)}`;
  if ($("#target-champ").value !== state.target) $("#target-champ").value = state.target;
}

function renderChamps() {
  const q = $("#champ-search").value.trim();
  document.body.classList.toggle("searching", !!q);
  const list = state.data.champions.filter((c) => !q || c.name.includes(q) || c.id.toLowerCase().includes(q.toLowerCase()));
  $("#champ-grid").innerHTML = list.map((c) => `
    <div class="champ ${state.champ && state.champ.id === c.id ? "on" : ""}" data-id="${c.id}">
      <img src="${icon("champion", c.id)}" alt="" loading="lazy">${esc(c.name)}
    </div>`).join("");
  $("#champ-grid").querySelectorAll(".champ").forEach((el) => el.addEventListener("click", () => {
    selectChamp(state.data.champions.find((c) => c.id === el.dataset.id));
  }));
}

function selectChamp(next) {
  if (!state.champ || state.champ.id !== next.id) {
    // 챔피언이 바뀌면 새 게임: 고른 증강·선택지를 비운다 (아이템은 남겨서 비교할 수 있게)
    state.chosen = [];
    state.tierMode = "auto";
    state.showAllAugs = false;
    clearOffers();
  }
  state.champ = next;
  state.weights = S.profile(state.champ, state.data.tags);
  renderChamps();
  renderAll();
}

function renderAll() {
  // 쉽게 보기에서 숨긴 칸(능력치 표·아이템 목록·가중치)은 그리지 않는다. '자세히 보기'로 바꿀 때 그린다
  const simple = document.body.classList.contains("simple");
  renderChampCard();
  if (!simple) renderStats();
  renderOffers();
  renderChosen();
  renderAugs();
  renderChosenItems();
  renderBuild();
  if (!simple) {
    renderItemList();  // 안에서 '다음에 살 아이템'·게임 화면 표시도 갱신
    renderWeights();
  } else {
    renderNextBuy(state.champ ? itemRanking() : []);  // 아이템 목록은 숨겨도 '다음에 살 아이템'은 보인다
  }
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
  el.innerHTML = `<img src="${icon("champion", c.id)}" alt="">
    <div><div><b>${esc(c.name)}</b> <span class="muted">${esc(c.title)}</span></div>
    <div class="muted small">${esc(c.tags.join(" · "))} · ${c.melee ? "근접" : "원거리"} · ${esc(c.partype || "자원 없음")}
      ${c.magicShare != null ? ` · 스킬 피해 마법 ${Math.round(c.magicShare * 100)}%` : ""}</div>
    <div><span class="muted small">가중치</span> ${top.map(([t, v]) => `<span class="chip">${label(t)} ${v}</span>`).join("")}</div>
    <div><span class="muted small">스킬 특성</span> ${traits.map(([t, v]) => `<span class="chip">${label(t)} ${v}</span>`).join("")}
      ${feats.map((f) => `<span class="chip on">${esc(featLabel[f] || f)}</span>`).join("")}</div>
    ${aramChips(c)}${patchChip(c.patch, c.patchCheck)}${c.stackBasis ? `<div class="small">중첩형 패시브·스킬은 게임 중반 평균 중첩으로 계산해요 ${basisBlock(c.stackBasis)}</div>` : ""}${refLinks(c.refs)}</div>`;
}

const ARAM_NAME = { dmg_dealt: "가하는 피해", dmg_taken: "받는 피해", healing: "회복", shielding: "보호막",
  ability_haste: "스킬 가속", tenacity: "강인함", total_as: "공격 속도", energyregen_mod: "기력 회복" };
function aramChips(c) {
  if (!c.aram) return `<div><span class="muted small">칼바람 보정 없음</span></div>`;
  const chips = Object.entries(c.aram).map(([k, v]) => {
    const pct = k === "ability_haste" ? `+${Number(v)}` : `${Math.round(Number(v) * 100)}%`;
    const good = k === "dmg_taken" ? v < 1 : k === "ability_haste" ? v > 0 : v > 1;
    const neutral = k !== "ability_haste" && v === 1;
    return `<span class="chip ${neutral ? "" : good ? "syn" : "bad"}">${esc(ARAM_NAME[k] || k)} ${pct}</span>`;
  });
  return `<div><span class="muted small">${c.aramSource === "칼바람 + 증강 모드" ? "칼바람 + 증강 모드 보정" : "칼바람 보정"} (위키)</span> ${chips.join("")}</div>`;
}

function renderStats() {
  const c = state.champ && S.withChosenFeatures(state.champ, state.chosen, state.data.rules);  // 검을 뽑아라 → 근접
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
    + tile("회복·보호막 (스킬 1회)", m.support, base.support, `보호막 ${fmt(m.shield)} · 회복 ${fmt(m.heal)}`)
    + tile("기동성 (평균 이동 속도)", m.mobility, base.mobility, `스킬 가속 기본 ${fmt(m.haste.basic)} · 궁 ${fmt(m.haste.ult)}`)
    + tile("제어력 (콤보당 CC 초)", m.control, base.control, "기절·속박 등 그대로, 둔화는 30%");
  const rows = St.ROWS.filter(([k]) => Math.abs(e2[k] || 0) > 1e-6 || ["HP", "AD", "AP", "AS", "ARMOR", "MR", "MS"].includes(k));
  const cell = (v, k, d, pct, sign) => {
    if (Math.abs(v) < 1e-6) return sign ? `<td class="muted">-</td>` : `<td>0</td>`;
    const val = pct ? fmt(v * 100) + "%" : fmt(v, d);
    return `<td class="${sign ? (v > 0 ? "up" : "down") : ""}">${sign && v > 0 ? "+" : ""}${val}</td>`;
  };
  $("#stat-table").innerHTML = `<tr><th>능력치</th><th>기본 (${L}레벨)</th><th>아이템</th><th>증강</th><th>합계</th></tr>`
    + rows.map(([k, name, d, pct]) => `<tr><td>${name}</td>${cell(e0[k] || 0, k, d, pct)}${cell((e1[k] || 0) - (e0[k] || 0), k, d, pct, true)}${cell((e2[k] || 0) - (e1[k] || 0), k, d, pct, true)}<td><b>${pct ? fmt((e2[k] || 0) * 100) + "%" : fmt(e2[k] || 0, d)}</b></td></tr>`).join("");
  const effRows = m.effects.map((e) => `<tr><td>${esc(e.src)}</td><td><span class="chip k-${esc(e.kind)}">${esc(KIND_LABEL[e.kind] || e.kind)}</span></td>
      <td>${fmt(e.value)}</td><td>${e.hit != null ? fmt(e.hit) : "-"}</td><td class="muted">${esc(TRIGGER_LABEL[e.trigger] || e.trigger)}</td></tr>`).join("");
  $("#skills").innerHTML = (effRows ? `<table class="eff-table"><tr><th>패시브·아이템·증강 효과</th><th>종류</th><th>1회</th><th>방어 적용</th><th>발동</th></tr>${effRows}</table>
      <p class="muted small">콤보에는 4초 동안 발동 횟수만큼(쿨다운·기본 공격 횟수 제한) 들어가고, DPS에는 초당 발동 횟수로 들어가요.
      상대 체력 비례 피해는 상대 최대 체력 ${St.TARGET.HP}, 현재 체력 ${St.TARGET.CUR * 100}% 기준이에요.</p>` : "")
    + `<div class="skill"><b>패시브 · ${esc(c.passive.name)}</b><div class="desc">${c.passive.desc}</div></div>`
    + c.spells.map((sp, i) => {
      const ms = m.spells[i];
      const vals = ms.values.map((v) => `<span class="chip k-${esc(v.kind)}">${esc(KIND_LABEL[v.kind] || v.kind)} ${fmt(v.value)}</span>`).join("");
      return `<div class="skill ${ms.disabled ? "off" : ""}"><b>${esc(sp.slot)} · ${esc(sp.name)}</b>
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
    return `<div class="slot" data-i="${i}" title="해제"><img src="${icon("augment", a.id)}" alt=""><span class="r${a.rarity}">${esc(a.name)}</span></div>`;
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
  const key = JSON.stringify([state.champ && state.champ.id, state.chosen.map((a) => a.id), state.items.map((i) => i.id), state.aram, state.weights, tierMode(), state.target, statOpts()]);
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

// 출처 링크 (클릭해도 증강·아이템이 선택되지 않게)
function refLinks(refs) {
  if (!refs || !refs.length) return "";
  const safe = refs.filter((r) => /^https:\/\//.test(r.url || ""));  // https 주소만 (javascript: 같은 주소 차단)
  if (!safe.length) return "";
  return `<div class="refs">🔗 ${safe.map((r) => `<a href="${esc(r.url)}" target="_blank" rel="noopener"${r.external ? ' class="ext"' : ""}>${esc(r.label)}</a>`).join(" · ")}</div>`;
}
const majorMinor = (v) => String(v || "").split(".").slice(0, 2).join(".");
// 새 증강(이번 패치에 처음 본 것)·자동 추정 규칙 표시
function newChips(a) {
  const r = state.data.rules.augments[a.id] || {};
  return (a.firstSeen && majorMinor(a.firstSeen) === majorMinor(state.data.version) ? `<span class="chip on" title="패치 ${esc(a.firstSeen)}에 처음 나온 증강">✨ 새 증강</span>` : "")
    + (r.auto ? `<span class="chip" title="사람이 확인하기 전, 설명 문장에서 자동으로 읽은 능력치예요">🤖 자동 추정</span>` : "");
}

// 이번 패치 변경 + 줄마다 게임 데이터 대조 결과 (계산에 어떤 값을 썼는지 근거)
const PATCH_STATUS = {
  ok: ["✅", "게임 데이터에 반영된 것을 확인했어요"],
  applied: ["📄", "게임 데이터가 아직 이전 값이라, 이 줄은 패치 노트 값으로 계산해요"],
  stale: ["⚠️", "게임 데이터가 아직 이전 값이에요. 바꿀 자리를 확실히 못 찾아서 이전 값으로 계산해요"],
  differ: ["❔", "이 수치를 데이터에서 찾지 못해 대조하지 못했어요"],
  unknown: ["", "숫자 비교가 없는 변경이에요 (삭제·신규·설명 변경)"],
};
// 숫자로 비교하지 않는 증강은 왜 그런지 종류로 보여 준다 (숫자로 계산되는 증강에는 붙이지 않는다)
const CATEGORY = {
  quest: ["📜 퀘스트", "조건을 채우면 보상을 받는 증강이라, 보상 전까지는 효과가 없어요. 숫자로 비교하지 않아요."],
  random: ["🎰 무작위", "결과가 무작위라 미리 숫자로 비교할 수 없어요."],
  spell: ["✨ 소환사 주문", "소환사 주문을 바꾸거나 늘리는 증강이라 피해·체력 같은 숫자로 비교하지 않아요."],
};
function categoryChip(r) {
  const a = r.aug;
  if (S.hasNumbers(a, state.data.rules)) return "";
  const c = CATEGORY[a.category];
  if (c) return `<span class="chip" title="${esc(c[1])}">${c[0]}</span>`;
  return r.unclassified ? `<span class="chip" title="설명에서 효과 종류를 정하지 못했어요. 숫자 없이 기본 점수로 비교해요.">효과 분류 안 됨</span>` : "";
}

function patchChip(lines, checks) {
  if (!lines || !lines.length) return "";
  const rows = lines.map((ln, i) => {
    const c = (checks || [])[i] || {};
    const [icon, why] = PATCH_STATUS[c.status] || ["", ""];
    const tip = why + (c.source ? ` · 비교한 곳: ${c.source}` : "") + (c.note ? ` · ${c.note}` : "");
    return `<li title="${esc(tip)}">${icon ? icon + " " : ""}${esc(ln)}${c.status === "applied" ? ` <span class="chip on">패치 노트 값으로 계산</span>` : ""}</li>`;
  }).join("");
  return `<details class="patch"><summary>🆕 이번 패치 변경 ${lines.length}줄</summary><ul>${rows}</ul>
    <div class="muted small">✅ 게임 데이터 반영 확인 · 📄 패치 노트 값으로 임시 계산 · ⚠️ 이전 값으로 계산 중 · ❔ 대조 못 함 (마우스를 올리면 이유)</div></details>`;
}

function transformChip(a) {
  const tr = transform(a);
  return tr ? `<span class="chip on" title="이 증강을 고르면 ${esc(state.champ.name)}의 플레이 방향이 바뀌어요">🔄 ${josa(STYLE_LABEL[tr], "으로", "로")} 변신</span>` : "";
}

// 이유 한 줄: 데이터에서 자동으로 만든 쉬운 문장
function augReason(a, t, sc, g) {
  const feat = state.data.rules.features;
  const names = state.chosen.map((x) => x.name).join(" + ");
  const topPart = (sign) => g && Object.entries(g.parts || {}).filter(([, v]) => sign * v >= 0.01).sort((x, y) => sign * (y[1] - x[1]))[0];
  // 실제 숫자로: "회복·보호막이 112 → 407로 늘어요" (18레벨·아이템 6칸 기준). 퍼센트는 원래 값이 작으면 과장돼 보여서 뺀다
  const partText = (p, word) => {
    if (!p) return "";
    const ab = g && g.abs && g.abs[p[0]];
    if (ab) {
      const d = p[0] === "control" ? 1 : 0;
      const unit = p[0] === "control" ? "초" : "";
      // 숫자 뒤 조사(로/으로)는 읽는 법에 따라 달라서 괄호로 뺀다: "회복·보호막이 늘어요 (558 → 2,330)."
      const range = `(${fmt(ab[0], d)}${unit} → ${fmt(ab[1], d)}${unit})`;
      return word.endsWith(".") ? `${josa(METRIC_LABEL[p[0]], "이", "가")} ${word.slice(0, -1)} ${range}.` : `${josa(METRIC_LABEL[p[0]], "이", "가")} ${range} ${word}`;
    }
    return `${josa(METRIC_LABEL[p[0]], "이", "가")} ${fmt(Math.abs(p[1]) * 100)}% ${word}`;
  };
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
  if (state.chosen.length && tierMode() === "synergy" && g && TIER_ORDER[t.tier] >= TIER_ORDER.C) {
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
  state.offersClosed = false;
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
    state.offersClosed = false;  // 직접 입력하면 게임 화면에도 다시 표시
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
    if (!$("#ov-preview") || !$("#ov-preview").checked) pushOverlay([]);  // 위치 맞추기 미리 보기 중이면 예시 카드를 지우지 않는다
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
        <td><img src="${icon("augment", r.a.id)}" alt=""> <span class="r${r.a.rarity}">${esc(r.a.name)}</span>
          ${newChips(r.a)}${r === best ? `<span class="chip syn">추천</span>` : ""}${r === worst ? `<span class="chip bad">새로고침 권장</span>` : ""}
          ${r.changed >= 3 && !transform(r.a) ? `<span class="chip on">빌드가 바뀌어요</span>` : ""}
          <div class="why">${transformChip(r.a)}${esc(augReason(r.a, tiers[r.a.id], r.sc, gains[r.a.id]))}</div></td>
        <td>${tierBadge(tiers[r.a.id])}</td>
        <td>${gains[r.a.id] ? `<span class="${gains[r.a.id].gain >= 0 ? "up" : "down"}">${gains[r.a.id].gain >= 0 ? "+" : ""}${fmt(gains[r.a.id].gain * 100)}</span>` : `<span class="muted" title="효과 점수 ${r.sc.score.toFixed(2)}">숫자 없음</span>`}</td>
        <td>${r.p ? `${r.p.rank}위 / ${r.p.n}개 (상위 ${fmt(r.p.top * 100)}%)` : `<span class="muted">평가 불가</span>`}</td>
        <td>${fmt(f.combo)} ${delta(f.combo, nf.combo)}</td>
        <td>${fmt(f.dps)} ${delta(f.dps, nf.dps)}</td>
        <td>${fmt(f.ehp)} ${delta(f.ehp, nf.ehp)}</td>
        <td class="mini-build">${r.aug.pick.filter((p) => !p.fixed).map((p) => `<img src="${icon("item", p.item.id)}" title="${esc(p.item.name)}" alt="">`).join("")}</td>
        <td><button data-pick="${r.i}">이걸로 선택</button></td></tr>`;
    }).join("")}</table>
    <p class="muted small">강해지는 정도(빌드 가치) = 지금 빌드(완성 6칸, 18레벨)에 이 증강을 더했을 때 스킬 피해·초당 피해·버티는 힘·회복/보호막이 오르는 정도(가중합 ×100).
      등급 내 위치는 같은 등급에서 숫자로 평가되는 증강끼리 비교한 순위예요. "숫자 없음"은 소환사 주문 변경·증강 변환·퀘스트처럼 고정 수치가 없는 증강이라 직접 판단이 필요해요.
      콤보·DPS·유효 체력 칸은 그 증강에 맞춰 빌드를 다시 짰을 때(몰빵) 지금 대비 변화예요.
      새로고침 권장 = 새로고침을 안 쓴 칸 중 같은 등급 하위 ${REROLL_PCT * 100}% 안에 드는 가장 낮은 칸.
      추천 = 연계 티어가 가장 높은 것(같으면 빌드 가치 순).</p>`;
  // 게임의 선택 화면이 이미 닫혔으면(고른 증강을 못 정해 선택지만 남긴 경우) 게임 화면 카드 표시는 지운다 (2026-10-14 지적: 팁이 안 없어짐)
  if (state.offersClosed) pushOverlay([]);
  else pushOverlay(rows.map((r) => ({ r, t: tiers[r.a.id], best: r === best, reroll: r === worst, reason: augReason(r.a, tiers[r.a.id], r.sc, gains[r.a.id]) })));
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
    const tag = x.best ? " · 1순위" : x.reroll ? " · 새로고침?" : "";
    const tr = transform(x.r.a);
    overlayCards[x.r.i] = {
      name: x.r.a.name, icon, label: label + tag, tier: noEffect(t) ? "X" : t && t.tier,
      // 게임 화면은 짧게: '고르면 ○○형으로 가볼 수 있어요'는 변신 표시와 겹쳐서 뺀다
      reason: (tr ? `🔄 ${josa(STYLE_LABEL[tr], "으로", "로")} 변신 · ` : "") + x.reason.replace(/\s*고르면 .*?가볼 수 있어요\./, ""),
    };
  }
  sendOverlay();
}
// 산 아이템 평가: 이 챔피언(+고른 증강)에 잘 맞는지. 그 아이템을 빼고 다시 순위를 매겨서 몇 등인지로 판단
let verdictCache = { key: null, value: [] };
function itemVerdicts() {
  if (!state.champ || !state.items.length) return [];
  const key = JSON.stringify([state.champ.id, state.chosen.map((a) => a.id), state.items.map((i) => i.id), state.weights, state.target, statOpts()]);
  if (verdictCache.key === key) return verdictCache.value;
  const d = state.data;
  const concept = state.chosen.length ? "augment" : "balanced";
  const value = state.items.map((it) => {
    const others = state.items.filter((x) => x.id !== it.id);
    const rows = S.rankItems(d.items, state.weights, state.chosen, others, d.rules, state.champ, statOpts(), concept)
      .filter((r) => r.item.kind === it.kind);
    const idx = rows.findIndex((r) => r.item.id === it.id);
    const row = rows[idx];
    const best = rows.find((r) => !others.some((o) => o.id === r.item.id));
    if (row && row.offType) return { item: it, mark: "⚠️", text: `${state.champ.damageType === "magic" ? "마법" : "물리"} 챔피언에겐 반대 유형이에요` + (best ? ` (추천: ${best.item.name})` : "") };
    if (idx >= 0 && idx < 5) return { item: it, mark: "✅", text: state.chosen.length ? "고른 증강과 잘 맞아요" : "잘 맞아요" };
    if (idx >= 0 && idx < 15) return { item: it, mark: "👍", text: "괜찮아요" };
    return { item: it, mark: "🤔", text: best ? `더 잘 맞는 것: ${best.item.name}` : "다른 선택이 나아요" };
  });
  verdictCache = { key, value };
  return value;
}

function sendOverlay() {
  const api = window.pywebview && window.pywebview.api;
  if (!api || !api.set_overlay || !state.champ) return;
  const hud = [];
  if (state.chosen.length) hud.push("고른 증강: " + state.chosen.map((a) => a.name).join(", "));
  const v = itemVerdicts();
  if (v.length) hud.push("산 아이템: " + v.map((x) => `${x.item.name} ${x.mark}`).join(" · "));
  hud.push(...enemyAdvice());
  const next = [...document.querySelectorAll("#next-buy .next-row")].map((e) => e.title).slice(0, 3);
  if (next.length) hud.push("다음 구매: " + next.join(" > "));
  if (state.buyNowText) hud.push("💰 " + state.buyNowText);
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
  // 쉽게 보기: 위 15개만 (검색 중이거나 '전체 보기'를 누르면 다)
  const SIMPLE_N = 15;
  const total = ranked.length;
  if (document.body.classList.contains("simple") && !state.showAllAugs && !state.augQuery) ranked = ranked.slice(0, SIMPLE_N);
  list.innerHTML = ranked.map((r) => `
    <div class="aug" data-id="${r.aug.id}">
      <img src="${icon("augment", r.aug.id)}" alt="" loading="lazy">
      <div>
        <div class="name r${r.aug.rarity}">${tierBadge(tiers[r.aug.id])} ${esc(r.aug.name)} <span class="muted small">${esc(r.aug.rarityName)}</span>${newChips(r.aug)}
          ${gains[r.aug.id] ? `<span class="chip adv ${gains[r.aug.id].gain >= 0 ? "syn" : "bad"}" title="빌드 가치">강해지는 정도 ${gains[r.aug.id].gain >= 0 ? "+" : ""}${fmt(gains[r.aug.id].gain * 100)}</span>` : ""}</div>
        <div class="why">${transformChip(r.aug)}${esc(augReason(r.aug, tiers[r.aug.id], r, gains[r.aug.id]))}</div>
        ${basisBlock([...(((state.data.rules.augments[r.aug.id] || {}).basisView) || []), ...effectBasis(r.aug.effects)])}
        ${patchChip(r.aug.patch, r.aug.patchCheck)}${refLinks(r.aug.refs)}
        <div class="tags">${r.generic ? `<span class="chip">범용 (챔피언 무관)</span>` : ""}${categoryChip(r)}${r.parts.map((p) =>
          `<span class="chip ${p.synergy ? "syn" : ""}">${label(p.tag)} +${p.value.toFixed(2)}${p.synergy ? " 시너지" : ""}</span>`).join("")}${r.req
          ? `<span class="chip ${r.req.met ? "syn" : "bad"}">조건: ${esc(featLabel[r.req.feature] || r.req.feature)} ${r.req.met ? "충족" : "없음"}</span>` : ""}${r.itemMatch ? `<span class="chip syn">고른 아이템과 연계</span>` : ""}</div>
        ${r.note ? `<div class="note">${esc(r.note)}</div>` : ""}
        <div class="desc">${r.aug.desc}</div>
      </div>
      <div class="score">${r.score.toFixed(2)}<div class="bar"><i style="width:${(r.score / max) * 100}%"></i></div></div>
    </div>`).join("") + (ranked.length < total ? `<button class="more-augs reset">나머지 ${total - ranked.length}개 증강도 보기</button>` : "")
    || `<p class="muted">조건에 맞는 증강이 없어요.</p>`;
  const more = list.querySelector(".more-augs");
  if (more) more.addEventListener("click", () => { state.showAllAugs = true; renderAugs(); });
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
    <img src="${icon("item", r.item.id)}" alt="">
    <div><div>${esc(r.item.name)} <span class="gold">${fmt(Number(r.item.gold) || 0)}G</span> <span class="muted small">${KIND_NAME[r.item.kind] || ""}${r.item.limit ? ` · ${esc(LIMIT_NAME[r.item.limit] || r.item.limit)} 계열` : ""}</span>${r.fixed ? ` <span class="chip on">고정</span>` : ""}${r.mismatch ? ` <span class="chip">성향 반대</span>` : ""}</div>
    <div>${r.parts.slice(0, 4).map((p) => `<span class="chip">${label(p.tag)} +${p.value.toFixed(2)}</span>`).join("")}</div>
    ${r.synergy ? `<span class="chip syn">🔗 연계 추천</span>` : ""}
    ${r.reasons && r.reasons.length ? `<div class="reason">${esc(itemReason(r))}</div>` : ""}
    ${gainLine(r.gain)}${basisBlock([...(r.item.basisView || []), ...effectBasis(r.item.effects)])}${r.fixed ? "" : refLinks((r.item.refs || []).filter((x) => x.label === "위키"))}</div>
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

function ownedParts() {
  const parts = (state.inventory || []).map((id) => (state.data.parts || {})[id]).filter(Boolean);
  if (!parts.length) return "";
  return `<div class="owned-parts small muted">게임에서 산 부품: ${parts.map((p) => `<b>${esc(p.name)}</b>`).join(" · ")}</div>`;
}

function renderChosenItems() {
  const slots = Array.from({ length: S.SLOTS }, (_, i) => state.items[i]);
  $("#chosen-items").innerHTML = slots.map((it) => it
    ? `<div class="islot" data-id="${it.id}" title="${esc(it.name)} (해제)"><img src="${icon("item", it.id)}" alt=""></div>`
    : `<div class="islot muted">빈 칸</div>`).join("")
    + itemVerdicts().map((v) => `<div class="verdict small">${v.mark} <b>${esc(v.item.name)}</b> ${esc(v.text)}</div>`).join("")
    // 게임에서 산 부품도 보이게: 완성 아이템만 칸에 들어가서, 초반엔 산 게 하나도 안 보였다 (2026-10-14 지적)
    + ownedParts();
  $("#chosen-items").querySelectorAll(".islot[data-id]").forEach((el) => el.addEventListener("click", () => {
    state.items = state.items.filter((i) => i.id !== el.dataset.id);
    renderAll();
  }));
}

// 쉽게 보기: 추천·이유·살 아이템만. 자세히 보기: 능력치 표·점수·태그·가중치까지
function setSimple(on, rerender = true) {
  document.body.classList.toggle("simple", on);
  $("#simple-toggle").textContent = on ? "자세히 보기" : "쉽게 보기";
  try { localStorage.setItem("simple", on ? "1" : "0"); } catch (e) { /* 무시 */ }
  if (rerender && state.data) renderAll();
}

// 근거: 숫자마다 어디서 왔는지 (게임 데이터 / 설명 / 계산 / 가정+이유)
const BASIS_ICON = { game: ["📊", "게임 데이터"], desc: ["📖", "설명 수치"], calc: ["🧮", "계산"], assume: ["⚠️", "가정"],
  auto: ["🤖", "자동 추정"], wiki: ["📚", "위키 대조"], missing: ["❔", "확인 필요"], trigger: ["⏱️", "발동 빈도"] };
function basisBlock(list, open = false) {
  if (!list || !list.length) return "";
  const hasAssume = list.some((b) => b.t === "assume" || b.t === "auto" || b.t === "missing");
  const rows = list.map((b) => {
    const [icon, name] = BASIS_ICON[b.t] || ["•", ""];
    return `<li>${icon} <b>${name}</b> ${esc(b.text)}${b.why ? `<div class="bwhy">이유: ${esc(b.why)}${b.noData ? " · 실측 아님 (게임 기록이 모이면 바꿀 예정)" : ""}</div>` : ""}</li>`;
  }).join("");
  return `<details class="basis"${open ? " open" : ""}><summary>📐 근거 보기${hasAssume ? " · ⚠️ 가정 포함" : ""}</summary><ul>${rows}</ul></details>`;
}
// 게임 데이터에서 뽑은 효과(피해·보호막·회복)의 근거: 발동 빈도를 어떤 문구로 정했는지
const TRIGGER_WHY = { cast: "스킬을 쓸 때마다", onhit: "기본 공격마다", attacks: "기본 공격 N번마다", time: "일정 시간마다", once: "한 번 (처치·조건부)" };
function effectBasis(effects) {
  return (effects || []).map((e) => {
    const tr = e.trigger || { type: "once" };
    const freq = tr.type === "attacks" ? `기본 공격 ${tr.n}번마다` : tr.type === "time" ? `${tr.sec}초마다` : TRIGGER_WHY[tr.type];
    return { t: tr.assume ? "assume" : "trigger", text: `${KIND_LABEL[e.kind] || e.kind}: ${freq}`
      + (tr.from ? ` — 설명의 '${tr.from}'` : " — 발동 문구가 없어 한 번으로 셈"), why: tr.assume || "" };
  });
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
  // 작게 보기(3종을 한 줄씩)일 때만 3종을 다 찾는다. 보통은 지금 고른 탭 하나만 (다른 탭은 누를 때)
  const mini = document.body.classList.contains("mini");
  const key = S.CONCEPTS.some((c) => c.key === state.concept) ? state.concept : S.CONCEPTS[0].key;
  const builds = S.buildConcepts(state.data.items, state.weights, state.chosen, state.items, state.data.rules, state.champ, statOpts(), mini ? null : [key]);
  const cur = builds.find((b) => b.key === key) || builds[0];
  $("#concept-tabs").innerHTML = S.CONCEPTS.map((b) =>
    `<button data-k="${b.key}" class="${b.key === cur.key ? "on" : ""}">${b.label}</button>`).join("");
  $("#concept-tabs").querySelectorAll("button").forEach((el) => el.addEventListener("click", () => {
    state.concept = el.dataset.k;
    renderBuild();
  }));
  $("#concept-desc").textContent = cur.desc + (state.chosen.length ? "" : " — 증강을 고르면 컨셉마다 빌드가 달라져요.");
  // 작게 보기용 한 줄 요약: 컨셉 3개의 빌드 아이콘
  $("#mini-build").innerHTML = !mini ? "" : `<h2>추천 빌드</h2>` + builds.map((b) => `<div class="mini-row"><b style="min-width:52px">${b.label}</b>
    ${b.pick.map((p) => `<img src="${icon("item", p.item.id)}" title="${esc(p.item.name)}" alt="">`).join("")}</div>`).join("");
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

// ---- 지금 골드로 살 것: 1순위 아이템을 완성하려면 남은 금액, 지금 살 수 있는 가장 비싼 부품
const partOf = (id) => (state.data.parts || {})[id] || state.data.items.find((i) => i.id === id);
const partGold = (id) => { const p = partOf(id); return p ? (p.gold || 0) : 0; };
const partFrom = (id) => { const p = partOf(id); return (p && p.from) || []; };
// 가진 부품(owned: id → 개수)을 써서 id 를 완성하는 데 드는 남은 금액
function remainingCost(id, owned) {
  if (owned[id] > 0) { owned[id]--; return 0; }
  const from = partFrom(id);
  if (!from.length) return partGold(id);
  const combine = partGold(id) - from.reduce((t, c) => t + partGold(c), 0);
  return combine + from.reduce((t, c) => t + remainingCost(c, owned), 0);
}
function buyNow(item, gold, inventory) {
  const count = () => inventory.reduce((m, id) => ((m[id] = (m[id] || 0) + 1), m), {});
  const left = remainingCost(item.id, count());
  if (gold >= left) return { done: true, left };
  // 부품 후보: 트리 안의 부품 중 아직 없는 것 (가진 부품을 반영한 실제 가격)
  const cands = [];
  const walk = (id) => { for (const c of partFrom(id)) { cands.push(c); walk(c); } };
  walk(item.id);
  let best = null;
  for (const c of new Set(cands)) {
    if (inventory.includes(c)) continue;
    const cost = remainingCost(c, count());
    if (cost > 0 && cost <= gold && (!best || cost > best.cost)) best = { id: c, name: partOf(c).name, cost };
  }
  return { done: false, left, part: best };
}
function renderBuyNow() {
  const el = $("#buy-now");
  if (!el) return;
  const top = state.nextTop;
  if (state.gold == null || !top) { el.innerHTML = ""; return; }
  const g = Math.floor(state.gold);
  const b = buyNow(top, g, state.inventory);
  el.innerHTML = `💰 지금 골드 <b>${fmt(g)}</b> · ${esc(top.name)} 완성까지 <b>${fmt(b.left)}G</b><br>`
    + (b.done ? `→ <b>지금 ${esc(top.name)} 완성할 수 있어요</b>`
      : b.part ? `→ 지금 <img src="${icon("item", b.part.id)}" alt=""> <b>${esc(b.part.name)}</b> (${fmt(b.part.cost)}G)부터 사세요`
        : `→ 아직 살 수 있는 부품이 없어요 (가장 싼 부품보다 골드가 적어요)`);
  state.buyNowText = b.done ? `지금 ${top.name} 완성 가능` : b.part ? `지금 ${b.part.name} (${b.part.cost}G)` : "";
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
      <b>${i + 1}</b><img src="${icon("item", r.item.id)}" alt=""><div><div>${esc(r.item.name)} <span class="gold">${fmt(Number(r.item.gold) || 0)}G</span>${r.synergy ? ` <span class="chip syn">🔗 연계</span>` : ""}</div>
      <div class="small muted">${esc(itemReason(r) || "")}</div>${gainLine(r.gain)}</div></div>`).join("") : `<p class="muted small">아이템 칸이 다 찼어요.</p>`);
  const adv = enemyAdvice();
  if (adv.length) box.insertAdjacentHTML("beforeend", `<div class="enemy-advice small">${adv.map((a) => `<div>🛡️ ${esc(a)}</div>`).join("")}</div>`);
  box.insertAdjacentHTML("beforeend", `<div id="buy-now" class="small"></div>`);
  state.nextTop = pick.length ? pick[0].item : null;
  renderBuyNow();
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
    <input type="range" min="0" max="1.5" step="0.05" value="${Number(state.weights[t]) || 0}" data-t="${esc(t)}">
    <span>${state.weights[t].toFixed(2)}</span>`).join("");
  $("#weights").querySelectorAll("input").forEach((el) => el.addEventListener("input", () => {
    state.weights[el.dataset.t] = Number(el.value);
    el.nextElementSibling.textContent = Number(el.value).toFixed(2);
    renderChampCard();
    renderAugs();
    renderBuild();
    renderItemList();
    renderOfferResult();  // 선택지 비교·게임 화면 표시도 새 가중치로
  }));
}

// ---- 상점 추천 등록 (아이템 세트): 챔피언이 정해지면 한 번. 블록 = 기본 / 첫 증강 상위 3개별 / 변칙
let itemSetDone = null;
function itemSetBlocks(champ) {
  // 상점 추천은 기본 빌드 한 줄만: 증강별 후보를 여러 줄 넣으면 뭘 사야 할지 모른다 (2026-10-14 지적).
  // 상점은 게임 로딩 때 한 번만 읽혀서 증강을 고른 뒤 바뀌지 않는다 → 고른 증강 기준은 게임 화면 오른쪽 위 '다음 구매'가 맡는다
  const d = state.data;
  const w = S.profile(champ, d.tags);
  const bal = S.buildConcepts(d.items, w, [], [], d.rules, champ, statOpts(), ["balanced"])[0];
  const items = bal.pick.map((p) => p.item.id);
  const st = buildStyle(items);
  return [{ type: `기본 빌드${st ? ` (${STYLE_LABEL[st]})` : ""} · 왼쪽부터 사세요`, items }];
}
let lcuChamp = null;
async function applyLcu(api, L) {
  const el = $("#itemset-status");
  const champ = L && L.connected ? state.data.champions.find((c) => Number(c.key) === Number(L.championKey)) : null;
  // 챔피언 선택 중: 화면도 내 챔피언으로 (대기석에서 바꾸면 따라간다). 게임 중엔 게임 연동이 맞춘다.
  // 롤에서 챔피언이 바뀔 때만 바꾼다 (그 사이 다른 챔피언을 눌러 보는 건 덮어쓰지 않게)
  if (champ && L.phase === "ChampSelect" && lcuChamp !== champ.id) {
    lcuChamp = champ.id;
    if (!state.champ || state.champ.id !== champ.id) selectChamp(champ);
  }
  if (!$("#itemset-toggle").checked) { el.textContent = ""; return; }
  if (!L || !L.connected) { el.textContent = "· 롤 클라이언트 대기"; return; }
  if (!champ) { el.textContent = `· ${L.phase === "ChampSelect" ? "챔피언 선택 대기" : "클라이언트 연결됨"}`; return; }
  if (itemSetDone === champ.id) return;
  itemSetDone = champ.id;
  el.textContent = `· ${champ.name} 상점 추천 만드는 중…`;
  try {
    // 상점 추천은 화면에서 고른 상대와 무관하게 기준 상대로 만든다
    const prev = state.target;
    if (prev) St.setTarget(null);
    let blocks;
    try { blocks = itemSetBlocks(champ); } finally { if (prev) setTargetChamp(prev); }
    const r = await api.write_item_set(champ.key, `칼바람 증강 Addon · ${champ.name}`, blocks);
    el.textContent = r && r.ok ? `· ${champ.name} 상점 추천 등록됨 (${r.blocks}개 블록)` : `· 등록 실패: ${(r && r.error) || "알 수 없음"}`;
    if (!r || !r.ok) itemSetDone = null;
  } catch (e) {
    el.textContent = `· 등록 실패: ${String(e && e.message || e).slice(0, 80)}`;
    itemSetDone = null;  // 다음 확인 때 다시 시도
  }
}

// 증강 인식 (윈도우): 게임의 증강 선택 화면에서 읽은 선택지를 채우고, 화면이 닫히면 고른 증강을 넣는다
let augSeq = 0, augPickSeq = 0;
function applyAug(A) {
  if (!A || !state.champ) return;
  const byName = (n) => (n ? state.data.augments.find((a) => a.name === n) || null : null);
  // 게임 증강 칸 아이콘으로 읽은 고른 증강이 정답 (클릭으로 짐작한 건 틀릴 수 있었다: 2026-10-14 나피리 판)
  // 클릭으로 잡은 것은 지우지 않고 합친다 (v0.1.15: 증강 칸 오판이 맞게 잡은 광신자·조준경 부착을 덮어썼다)
  const hud = (A.hudPicked || []).map((id) => state.data.augments.find((a) => a.id === id)).filter(Boolean);
  const add = hud.filter((a) => !state.chosen.some((c) => c.id === a.id));
  if (add.length && state.chosen.length < 4) {
    state.chosen = [...state.chosen, ...add].slice(0, 4);
    clearOffers();
    renderAll();
  }
  if (A.offers && A.seq !== augSeq) {
    augSeq = A.seq;
    if (state.chosen.length < 4) {
      state.offers = A.offers.map(byName);
      state.offersClosed = false;
      renderOffers();
    }
  }
  if (A.pickSeq && A.pickSeq !== augPickSeq) {
    augPickSeq = A.pickSeq;
    const a = byName(A.picked);
    if (a) {
      // 증강 칸 그림으로 확인했더니 클릭으로 짐작한 것과 달랐다 → 짐작한 것을 빼고 바꾼다
      const wrong = A.by === "hud" && byName(A.replaces);
      if (wrong) state.chosen = state.chosen.filter((x) => x.id !== wrong.id);
      if (state.chosen.length < 4 && !state.chosen.some((x) => x.id === a.id)) state.chosen.push(a);
      clearOffers();
      renderAll();
    } else if (A.pickedFrom) {
      // 어느 카드였는지 못 정함 → 애드온엔 선택지를 남겨 '이걸로 선택'을 누르게 하고, 게임 화면 카드 표시는 지운다
      state.offers = A.pickedFrom.map(byName);
      state.offersClosed = true;
      renderOffers();
      $("#offer-result").insertAdjacentHTML("afterbegin", `<p class="warn small">방금 고른 증강을 화면에서 확인하지 못했어요. 고른 것의 <b>이걸로 선택</b>을 눌러 주세요.</p>`);
    }
  }
}

// 게임 연동: 챔피언·레벨·아이템을 게임에 맞춘다 (증강은 직접 고름). 같은 값이면 다시 그리지 않는다
let liveLast = "";
let liveEnded = false;
let liveShop = false, goldDrawnAt = 0;
function applyLive(L) {
  const el = $("#live-status");
  // 게임 중엔 챔피언 목록(아이콘 173개)을 접는다: 챔피언은 게임에서 정해지고, 화면 엔진 메모리를 덜 쓴다. 검색하면 다시 보인다
  document.body.classList.toggle("ingame", !!(L && L.enabled && L.inGame));
  if (!L || !L.enabled) { el.textContent = ""; return; }
  if (!L.inGame) {
    el.textContent = "게임 대기 중";
    if (state.gold != null) { state.gold = null; state.inventory = []; state.enemies = []; renderBuyNow(); }
    if (liveLast) { liveLast = ""; liveEnded = true; }  // 게임이 끝남 → 다음 게임은 새로 시작
    return;
  }
  if (liveEnded) {  // 같은 챔피언으로 다음 게임을 해도 고른 증강·선택지는 비운다
    liveEnded = false;
    state.chosen = [];
    state.tierMode = "auto";
    clearOffers();
  }
  const d = state.data;
  // 게임 내부 이름과 Data Dragon id 의 대소문자가 다른 챔피언이 있다 (FiddleSticks ↔ Fiddlesticks)
  const champ = L.champion ? d.champions.find((c) => c.id.toLowerCase() === String(L.champion).toLowerCase()) : null;
  const items = (L.items || []).map((id) => d.items.find((i) => i.id === id)).filter(Boolean)
    .filter((it, i, arr) => arr.findIndex((x) => x.name === it.name) === i).slice(0, S.SLOTS);
  const level = Math.max(1, Math.min(18, L.level || 1));
  const statusText = `게임 중 · ${L.championName || L.champion || "?"} ${level}레벨`
    + (L.augmentFields && L.augmentFields.length ? ` · 증강 필드 발견(${L.augmentFields.length})` : "")
    // 게임 화면 위 표시가 안 보일 때 이유를 바로 보이게 (윈도우)
    + (L.overlayStatus ? ` · 게임 화면 표시: ${L.overlayStatus}` : "")
    // 증강 인식이 안 되면 이유가 바로 보이게 (v0.1.12 는 기록 파일을 열어야만 알 수 있었다)
    + (L.aug && L.aug.status ? ` · 증강 인식: ${L.aug.status}` : "");
  if (el.textContent !== statusText) el.textContent = statusText;  // 같은 글이면 안 건드린다 (1초마다 다시 그리지 않게)
  el.title = (L.augmentFields && L.augmentFields.length ? "API 증강 필드: " + L.augmentFields.slice(0, 8).join(", ") + "\n" : "")
    + `원본 저장 위치: ${L.logDir}`;
  // 골드·부품은 3초마다 바뀌어도 순위는 그대로라 '지금 살 것'만 다시 그린다.
  // 골드는 칼바람에서 1초마다 오른다 → 상점이 열려 있을 때(게임 화면 상자에 보일 때)·부품이 바뀌었을 때만 바로,
  // 그 밖엔 10초에 한 번만 (게임 중 CPU 를 아낀다)
  const shopOpen = !!(L.aug && L.aug.shop);
  const invChanged = JSON.stringify(state.inventory) !== JSON.stringify((L.items || []).map(String));
  const goldChanged = invChanged || (state.gold !== (L.gold ?? null)
    && (shopOpen || shopOpen !== liveShop || Date.now() - goldDrawnAt > 10000));
  liveShop = shopOpen;
  if (goldChanged) goldDrawnAt = Date.now();
  state.gold = L.gold ?? null;
  state.inventory = (L.items || []).map(String);
  const enemies = (L.enemies || []).map((id) => d.champions.find((c) => c.id.toLowerCase() === String(id).toLowerCase())).filter(Boolean);
  const enemyKey = enemies.map((c) => c.id).sort().join(",");
  if (enemyKey !== (state.enemies || []).map((c) => c.id).sort().join(",")) state.enemies = enemies;
  const key = JSON.stringify([champ && champ.id, level, items.map((i) => i.id), enemyKey]);
  if (key === liveLast) {
    if (goldChanged) { renderBuyNow(); renderChosenItems(); sendOverlay(); }
    return;
  }
  const sameBuild = liveLast && JSON.stringify(JSON.parse(liveLast).filter((_, i) => i !== 1)) === JSON.stringify(JSON.parse(key).filter((_, i) => i !== 1));
  liveLast = key;
  // 레벨만 바뀌었으면 능력치 표만 다시: 증강·아이템 순위는 18레벨 완성 빌드 기준이라 레벨과 무관하다
  if (sameBuild && state.champ && champ && state.champ.id === champ.id) {
    state.level = level;
    $("#level").value = level;
    $("#level-val").textContent = level;
    if (!document.body.classList.contains("simple")) renderStats();
    return;
  }
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

// 데스크톱 프로그램(pywebview)에서만: 데이터 갱신 등
window.addEventListener("pywebviewready", async () => {
  const api = window.pywebview.api;
  const st = await api.get_state();
  $("#desktop-tools").hidden = false;
  // 새 프로그램 버전 알림 (받기는 공식 릴리스 페이지에서 직접. 프로그램이 스스로 바꾸지 않는다)
  api.check_app_update().then((u) => {
    if (!u || !u.newer || !/^https:\/\/github\.com\/theddack\/kalbaram-addon\//.test(u.url || "")) return;
    const bar = document.createElement("div");
    bar.id = "app-update";
    bar.innerHTML = `<span>🔔 새 버전 <b>v${esc(u.latest)}</b>이 나왔어요 <span class="muted">(지금 v${esc(u.current)})</span></span>
      <a href="${esc(u.url)}" target="_blank" rel="noopener">받으러 가기</a>
      <a class="sub" href="https://theddack.github.io/kalbaram-addon/verify.html" target="_blank" rel="noopener">받은 파일 확인</a>
      <span class="tip">압축을 풀기 전에 '받은 파일 확인'으로 공식 파일인지 확인하세요. 새 폴더로 바꿀 땐 예전 폴더 안의 data 폴더를 옮기면 설정·기록이 그대로예요.</span>`;
    document.body.prepend(bar);
  }).catch(() => {});
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
    // 위치 맞추기 칸을 펼쳤을 때만 상태를 묻는다
    setInterval(async () => { if (!$("#overlay-pos").open) return; const o = await api.overlay_status(); $("#overlay-status").textContent = `(${o.status})`; }, 1500);
  }
  $("#itemset-toggle").checked = st.itemSets;
  $("#itemset-toggle").addEventListener("change", (e) => { api.set_item_sets(e.target.checked); itemSetDone = null; });
  // 롤 클라이언트: 게임 중엔 챔피언 선택·상점 추천 등록이 끝났으니 15초마다만 (게임 중 CPU)
  let lcuAt = 0;
  setInterval(async () => {
    if (document.body.classList.contains("ingame") && Date.now() - lcuAt < 15000) return;
    lcuAt = Date.now();
    applyLcu(api, await api.get_lcu());
  }, 3000);
  // 모드는 하나 (저사양 PC 도): 계산 기억은 작게, 챔피언 목록은 검색할 때만. 쉽게/자세히 보기는 고를 수 있다
  document.body.classList.add("light");
  S.setMemoMax(8);
  $("#live-toggle").checked = st.live;
  $("#live-toggle").addEventListener("change", (e) => api.set_live(e.target.checked));
  // 증강 선택 화면이 떠 있거나 상점이 열려 있으면 1초마다, 그 밖엔 3초마다 (게임 중 CPU).
  // 선택 화면이 새로 뜨거나 고르거나 상점을 열면 파이썬이 바로 깨운다 (livePoke) → 카드 평가가 늦지 않다.
  // 화면 → 파이썬 호출이 잦을수록 창 스레드와 파이썬이 서로 기다리다 굳을 위험이 커서 필요할 때만 자주 부른다
  let liveTimer = null, liveBusy = false;
  const livePoll = async () => {
    clearTimeout(liveTimer);
    if (liveBusy) return;
    liveBusy = true;
    let L = null;
    try { L = await api.get_live(); applyLive(L); applyAug(L && L.aug); } catch (e) { /* 다음에 다시 */ }
    liveBusy = false;
    const busy = L && L.inGame && L.aug && (L.aug.offers || L.aug.shop);
    liveTimer = setTimeout(livePoll, busy ? 1000 : 3000);
  };
  window.livePoke = livePoll;
  livePoll();
  // 데이터 갱신: 브라우저 확인창(제목이 '127.0.0.1:…의 메시지')은 헷갈려서 버튼 옆 안내 문구로 묻는다 (2026-10-14 지적)
  const btn = $("#update-data");
  const note = document.createElement("span");
  note.id = "update-note";
  note.className = "small muted";
  btn.after(note);
  const patch = (v) => (String(v || "").match(/^\d+\.\d+/) || [v])[0];
  const reset = () => { btn.disabled = false; btn.textContent = "데이터 갱신"; };
  let again = 0;  // '이미 최신' 안내 뒤 10초 안에 한 번 더 누르면 다시 받는다
  btn.title = "게임 패치 데이터(챔피언·아이템·증강 수치)를 다시 받아요. 프로그램 업데이트는 위 '새 버전' 알림으로 해요.";
  btn.addEventListener("click", async () => {
    btn.disabled = true;
    if (Date.now() > again) {
      btn.textContent = "확인 중…";
      const v = await api.check_update();
      if (v.error) { note.textContent = "최신 패치 확인 실패 (인터넷 연결 확인)"; note.title = v.error; reset(); return; }
      if (!v.outdated) {
        note.textContent = `이미 최신이에요 (패치 ${patch(v.current)}) · 그래도 다시 받으려면 한 번 더 누르세요`;
        again = Date.now() + 10000;
        setTimeout(() => { if (Date.now() > again) note.textContent = ""; }, 10500);
        reset();
        return;
      }
    }
    again = 0;
    note.textContent = "";
    btn.textContent = "받는 중…";
    const r = await api.update_data();
    if (!r.ok) { note.textContent = "갱신 실패 (마우스를 올리면 이유)"; note.title = r.log; reset(); return; }
    // 진행 상황: 지금 단계와 걸린 시간 (보통 2~5분)
    const timer = setInterval(async () => {
      const u = await api.update_status();
      const t = `${Math.floor(u.seconds / 60)}분 ${u.seconds % 60}초`;
      btn.textContent = `받는 중… ${u.step || ""} · ${t}`;
      note.textContent = "다 받으면 화면이 새로 고쳐져요. 그동안 다른 기능은 그대로 써도 돼요.";
      if (u.running) return;
      clearInterval(timer);
      if (u.ok) location.reload();
      else { note.textContent = "갱신 실패 (마우스를 올리면 이유)"; note.title = u.log; reset(); }
    }, 1000);
  });
});

init();
