// 계산 엔진 테스트: 능력치·발동 빈도·기억(memo)·실제 사례로 확인한 값
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("path");
const WEB = path.join(__dirname, "..", "web");
global.Stats = require(path.join(WEB, "stats.js"));
const St = global.Stats;
const S = require(path.join(WEB, "scoring.js"));
const d = require(path.join(WEB, "data.json"));
const champ = (id) => d.champions.find((c) => c.id === id);
const item = (name) => d.items.find((i) => i.name === name);
const aug = (id) => d.augments.find((a) => a.id === id);

test("레벨 성장식: 럭스 18레벨 체력 (손 검산 2263 근처)", () => {
  const t = St.computeStats(champ("Lux"), 18, [], [], { aram: false }).total;
  assert.ok(Math.abs(t.HP - 2263) < 30, `HP ${t.HP}`);
});

test("지속 피해는 지속 시간마다 1번: 징크스 + 리안드리 초당 피해 = 화상 1번 ÷ 3초", () => {
  const m = St.evaluate(champ("Jinx"), 18, [item("리안드리의 고통")], []).m;
  const row = m.effects.find((e) => e.src === "리안드리의 고통");
  const base = St.evaluate(champ("Jinx"), 18, [], []).m;
  const lia = m.augDps - base.augDps;
  assert.ok(Math.abs(lia - row.hit / 3) < 0.5, `리안드리 초당 ${lia} vs ${row.hit / 3}`);
});

test("'스킬 사용 시' 효과는 한 스킬당 최대 2초에 1번 (징크스 Q 0.9초 토글)", () => {
  const c = champ("Jinx");
  assert.ok(c.spells.find((x) => x.slot === "Q").cooldown[0] < 2, "징크스 Q 는 짧은 토글");
  const eff = { kind: "magic", name: "t", base: [100], trigger: { type: "cast" }, src: "시험" };
  const m = St.metrics(c, 18, St.computeStats(c, 18, [], [{ _effects: [eff] }]));
  const row = m.effects.find((e) => e.src === "시험");
  const expected = row.hit * m.spells.reduce((t, sp) => t + (!sp.disabled && sp.cd > 0 ? 1 / Math.max(sp.cd, 2) : 0), 0);
  const base = St.metrics(c, 18, St.computeStats(c, 18, [], [])).augDps;
  assert.ok(Math.abs(m.augDps - base - expected) < 0.01, `${m.augDps - base} vs ${expected}`);
});

test("기준 상대는 공통 가정값 표와 같은 숫자", () => {
  const a = St.ASSUMPTIONS.find((x) => x.what.startsWith("기준 상대"));
  assert.ok(a.what.includes(String(St.DEFAULT_TARGET.HP)) && a.what.includes(String(St.DEFAULT_TARGET.ARMOR)), a.what);
});

test("상대 바꾸기: setTarget 후 기본값 복귀", () => {
  St.setTarget({ ARMOR: 300, MR: 200, HP: 5000 });
  assert.equal(St.TARGET.ARMOR, 300);
  St.setTarget(null);
  assert.deepEqual({ ...St.TARGET }, { ...St.DEFAULT_TARGET });
});

test("계산 기억: 같은 조건이면 같은 결과, 증강이 다르면 새로 계산", () => {
  const c = champ("Jinx"), w = S.profile(c, d.tags);
  const [a1] = S.buildConcepts(d.items, w, [], [], d.rules, c, {}, ["balanced"]);
  const [a2] = S.buildConcepts(d.items, w, [], [], d.rules, c, {}, ["balanced"]);
  assert.equal(a1, a2, "같은 조건 → 기억된 결과");
  const all = S.buildConcepts(d.items, w, [], [], d.rules, c, {});
  assert.equal(all.length, 3);
  assert.equal(all.find((x) => x.key === "balanced"), a1, "3종을 달라고 해도 균형 빌드는 다시 찾지 않는다");
  const [b] = S.buildConcepts(d.items, w, [aug("ARAM_DrawYourSword")], [], d.rules, c, {}, ["balanced"]);
  assert.notEqual(a1, b);
  St.setTarget({ ARMOR: 300, MR: 200, HP: 5000 });
  const [t] = S.buildConcepts(d.items, w, [], [], d.rules, c, {}, ["balanced"]);
  St.setTarget(null);
  assert.notEqual(a1, t, "상대가 바뀌면 다시 계산");
});

test("계산 기억: 챔피언·고른 증강·산 아이템·상대 구성·규칙이 바뀌면 새로 계산, 같으면 그대로", () => {
  const c = champ("Jinx"), w = S.profile(c, d.tags);
  const one = (...a) => S.buildConcepts(...a)[0];
  const base = [d.items, w, [aug("ARAM_DrawYourSword")], [d.items.find((i) => i.kind === "legendary")], d.rules, c, { enemyMagic: 0.5 }, ["augment"]];
  const b0 = one(...base);
  assert.equal(one(...base), b0, "같으면 기억된 결과");
  const variants = {
    챔피언: () => { const x = [...base]; x[5] = champ("Lux"); x[1] = S.profile(x[5], d.tags); return x; },
    고른증강: () => { const x = [...base]; x[2] = [aug("ARAM_SpinToWin")]; return x; },
    산아이템: () => { const x = [...base]; x[3] = []; return x; },
    상대구성: () => { const x = [...base]; x[6] = { enemyMagic: 0.8 }; return x; },
    규칙: () => { const x = [...base]; x[4] = JSON.parse(JSON.stringify(d.rules)); return x; },
  };
  for (const [k, make] of Object.entries(variants)) assert.notEqual(one(...make()), b0, k);
  // 증강 점수(수치)도 같은 기준
  const g0 = S.numericAugmentGains(d.augments.slice(0, 20), d.items, w, [], [], d.rules, c, { enemyMagic: 0.5 });
  assert.equal(S.numericAugmentGains(d.augments.slice(0, 20), d.items, w, [], [], d.rules, c, { enemyMagic: 0.5 }), g0);
  assert.notEqual(S.numericAugmentGains(d.augments.slice(0, 20), d.items, w, [], [], d.rules, c, { enemyMagic: 0.9 }), g0, "상대 구성");
  assert.notEqual(S.numericAugmentGains(d.augments.slice(0, 20), d.items, w, [aug("ARAM_DrawYourSword")], [], d.rules, c, { enemyMagic: 0.5 }), g0, "고른 증강");
});

test("계산 기억: 길이가 같아도 다른 후보 목록이면 새로 계산", () => {
  const c = champ("Lux"), w = S.profile(c, d.tags);
  const nums = d.augments.filter((a) => S.hasNumbers(a, d.rules));
  const g1 = S.numericAugmentGains([nums[0]], d.items, w, [], [], d.rules, c);
  const g2 = S.numericAugmentGains([nums[1]], d.items, w, [], [], d.rules, c);
  assert.ok(nums[1].id in g2.gains && !(nums[0].id in g2.gains), "B 를 물으면 B 의 결과 (A 결과가 나오면 안 된다)");
  assert.ok(nums[0].id in g1.gains);
});

test("조건을 못 맞춘 증강은 능력치도 효과도 0 (럭스 + 승리를 위한 회전)", () => {
  const lux = champ("Lux"), spin = aug("ARAM_SpinToWin");
  assert.ok(spin.eligible && !spin.eligible.includes("Lux"));
  const [st] = S.augStatsOf([spin], d.rules, lux);
  assert.deepEqual(st, { _effects: [] });
  const ok = champ(spin.eligible[0]);
  const [st2] = S.augStatsOf([spin], d.rules, ok);
  assert.ok(Object.keys(st2).length >= 1, "맞는 챔피언이면 그대로");
});

test("빌드: 6칸, 신발 1개, 같은 계열 중복 없음 (전 챔피언)", () => {
  for (const c of d.champions) {
    const [b] = S.buildConcepts(d.items, S.profile(c, d.tags), [], [], d.rules, c, {}, ["balanced"]);
    const items = b.pick.map((p) => p.item);
    assert.equal(items.length, 6, c.id);
    assert.ok(items.filter((i) => i.kind === "boots").length <= 1, c.id);
    const limits = items.map((i) => i.limit).filter(Boolean);
    assert.equal(new Set(limits).size, limits.length, `${c.id} 같은 계열 ${limits}`);
    for (const v of Object.values(b.final)) if (typeof v === "number") assert.ok(Number.isFinite(v), `${c.id} NaN`);
  }
});

test("증강 등급: 전 증강이 등급을 받고, 조건 미충족은 D", () => {
  const c = champ("Lux");
  const tiers = S.augmentTiers(d.augments, d.items, S.profile(c, d.tags), [], [], d.rules, c, {}, "total");
  assert.equal(Object.keys(tiers).length, d.augments.length);
  assert.equal(tiers.ARAM_SpinToWin.tier, "D");
});

test("스킬 레벨: 제이스·니달리·우디르 18레벨 포인트 18개", () => {
  for (const id of ["Jayce", "Nidalee", "Karma", "Elise", "Udyr", "Jinx"]) {
    const c = champ(id);
    const r = St.ranksAt(18, c.spells);
    const free = c.spells[3].ranks === 1 || c.spells[3].ranks === 4 ? 1 : 0;
    assert.equal(r.reduce((a, b) => a + b, 0) - free, 18, `${id} ${r}`);
    r.forEach((x, i) => assert.ok(x <= c.spells[i].ranks, `${id} ${i} ${x}`));
  }
  assert.deepEqual(St.ranksAt(18, champ("Jayce").spells), [6, 6, 6, 1]);
  assert.equal(St.ranksAt(1, champ("Nidalee").spells)[3], 1, "니달리 R 은 1레벨부터");
});

test("최소·최대 피해는 큰 쪽 하나만 (다리우스 R)", () => {
  const m = St.evaluate(champ("Darius"), 18, [], []).m;
  const r = m.spells.find((s) => s.slot === "R");
  assert.equal(r.values.length, 1, r.values.map((v) => v.name).join(","));
});

test("추가 주문력 계수가 계산된다 (잭스 E·R)", () => {
  const j = champ("Jax");
  const a = St.evaluate(j, 18, [], []).m.combo, b = St.evaluate(j, 18, [item("라바돈의 죽음모자")], []).m.combo;
  assert.ok(b > a * 1.2, `${a} → ${b}`);
});

test("이동 속도 감속 구간 (490 초과는 절반만)", () => {
  const t = St.computeStats(champ("Jinx"), 18, [], [{ MS: 400 }]).total;
  assert.ok(t.MS < 600, `MS ${t.MS}`);
});

test("검을 뽑아라(근접 전환)는 추천 빌드에도 반영된다", () => {
  const c = champ("Jinx"), w = S.profile(c, d.tags), dys = aug("ARAM_DrawYourSword");
  const melee = S.withChosenFeatures(c, [dys], d.rules);
  assert.ok(melee.melee && melee.features.includes("melee"));
  const [b] = S.buildConcepts(d.items, w, [dys], [], d.rules, c, {}, ["balanced"]);
  const m = St.evaluate(melee, 18, b.pick.map((p) => p.item), S.augStatsOf([dys], d.rules, c)).m;
  assert.ok(Math.abs(b.final.dps - m.dps) < 0.5, `빌드 ${b.final.dps} vs 근접 ${m.dps}`);
});

test("대체 버전은 하나만: 트페 W 카드 1장, 렝가 강화 Q 제외, 리 신 Q1+Q2", () => {
  const vals = (id, slot) => St.evaluate(champ(id), 18, [], []).m.spells.find((s) => s.slot === slot).values
    .filter((v) => ["magic", "physical", "true"].includes(v.kind)).map((v) => v.name);
  assert.equal(vals("TwistedFate", "W").length, 1);
  assert.ok(!vals("Rengar", "Q").some((n) => /empowered/i.test(n)));
  assert.deepEqual(vals("LeeSin", "Q").sort(), ["initialdamage", "recastdamage"]);
});

test("곱셈 식(닐라 Q)은 치명타 확률이 오르면 피해도 오른다", () => {
  const c = champ("Nilah");
  const q = (items) => St.evaluate(c, 18, items, []).m.spells.find((s) => s.slot === "Q").values[0].value;
  assert.ok(q([item("무한의 대검")]) > q([]));
});

test("중첩형: 나서스 Q 는 평균 중첩만큼, 베이가는 주문력 중첩", () => {
  const nasus = champ("Nasus");
  assert.ok(nasus.stacks > 0 && nasus.stackBasis.some((b) => b.t === "assume"));
  const t = St.computeStats(champ("Veigar"), 18, [], []).total;
  assert.ok(t.AP >= champ("Veigar").stackStats.AP);
});

test("새로 숫자로 계산되는 증강: 흡수(스킬 피해 회복), 죽음의 순환(회복 → 피해)", () => {
  const lux = champ("Lux");
  const heal = (augs) => St.evaluate(lux, 18, [], S.augStatsOf(augs, d.rules, lux)).m.heal;
  assert.ok(heal([aug("ARAM_SustainingStrike")]) > heal([]));
  const sor = champ("Soraka");
  const combo = (augs) => St.evaluate(sor, 18, [], S.augStatsOf(augs, d.rules, sor)).m.combo;
  assert.ok(combo([aug("ARAM_CircleofDeath")]) > combo([]));
});

test("기본 빌드는 라이엇 주 피해 유형을 따른다 (이렐리아 AP·전사 치명타 쏠림 막기)", async () => {
  const { loadPage } = require("./page");
  const p = await loadPage({ simple: true });
  const by = (id) => p.data.champions.find((c) => c.id === id);
  const items = (id) => p.w.itemSetBlocks(by(id))[0].items.map((i) => p.data.items.find((x) => x.id === i));
  const isAP = (it) => (it.tags.AP || 0) >= 0.5 && !((it.tags.AD || 0) >= 0.5);
  const isCrit = (it) => (it.tags.CRIT || 0) >= 0.5 || "CRIT" in (Array.isArray(it.stats) ? {} : it.stats || {});
  assert.equal(by("Irelia").damageType, "physical");
  assert.ok(!items("Irelia").some(isAP), "이렐리아(물리)에 AP 아이템 없음");
  for (const id of ["Azir", "Teemo", "Kayle"]) assert.ok(!items(id).some((it) => (it.tags.AD || 0) >= 0.5 && !(it.tags.AP >= 0.5)), `${id}(마법)에 AD 아이템 없음`);
  for (const id of ["Darius", "Trundle", "Jax"]) assert.ok(!items(id).some(isCrit), `${id}(치명타 못 살리는 근접)에 치명타 없음`);
  assert.ok(items("Jinx").some(isCrit), "원거리 딜러는 치명타 그대로");
  assert.ok(items("Yasuo").some(isCrit), "치명타 특성(야스오)은 그대로");
});

// ---- 추천 품질 (2026-10-15 검토: 변칙은 증강이 받쳐 줄 때만, 몰빵은 증강을 실제로 살리는지)
const build = (cid, augIds, key) => {
  const c = champ(cid);
  return S.buildConcepts(d.items, S.profile(c, d.tags), augIds.map(aug), [], d.rules, c, {}, [key])[0];
};
const hasTag = (b, t) => b.pick.filter((p) => ((p.item.tags || {})[t] || 0) >= 0.5).length;

test("변칙: 증강이 없으면 받쳐 주는 정도 0 (화면에 실험 빌드로 표시)", () => {
  const b = build("Jinx", [], "twist");
  assert.equal(b.twist.s, 0);
  assert.ok(b.twist.opp.includes("AP"), "징크스의 반대쪽은 주문력");
});

test("변칙: 주문력 계수 보호막(전술적 대비)이면 징크스가 주문력 아이템을 산다", () => {
  const b = build("Jinx", ["ARAM_BigBrain"], "twist");
  assert.ok(b.twist.s > 0 && b.twist.by.includes("전술적 대비"), JSON.stringify(b.twist));
  assert.ok(hasTag(b, "AP") >= 2, b.pick.map((p) => p.item.name).join(","));
});

test("변칙: 공격력·주문력을 둘 다 키우는 증강(적응형 계수)이나 주문력 고정 수치는 근거가 아니다", () => {
  const sup = (cid, ids) => S.twistSupport(S.profile(champ(cid), d.tags), S.augContext(ids.map(aug), d.rules)).s;
  const id = (name) => d.augments.find((a) => a.name === name).id;
  assert.equal(sup("Zed", [id("불여우")]), 0, "불여우: 공격력·주문력 계수 둘 다");
  assert.equal(sup("Jinx", [id("사악한 정신")]), 0, "사악한 정신: 주문력 +80 고정");
  assert.equal(sup("Jinx", ["ARAM_ADAPt"]), 1, "공격력→주문력 환산은 확실한 근거");
});

test("몰빵: 아이템 업그레이드 증강은 그 아이템을 빌드에 넣는다 (징크스 + 징수의 총 업그레이드)", () => {
  const id = d.augments.find((a) => a.name === "징수의 총 업그레이드").id;
  for (const key of ["augment", "balanced"]) {
    const b = build("Jinx", [id], key);
    assert.ok(b.pick.some((p) => p.item.name === "징수의 총"), `${key}: ${b.pick.map((p) => p.item.name).join(",")}`);
  }
});

test("몰빵: 적 방어력을 깎는 증강(부식)에 방어력 아이템을 사지 않는다", () => {
  const id = d.augments.find((a) => a.name === "부식").id;
  const b = build("Jinx", [id], "augment");
  assert.equal(hasTag(b, "ARMOR") - b.pick.filter((p) => p.item.kind === "boots" && ((p.item.tags || {}).ARMOR || 0) >= 0.5).length, 0,
    b.pick.map((p) => p.item.name).join(","));
});

test("추천 품질 점검: 몰빵 빌드가 증강을 살리는 비율 95% 이상, 변칙 정상 90% 이상 (표본)", () => {
  const Q = require(path.join(__dirname, "..", "tools", "augment_quality.js"));
  const ids = d.augments.filter((a, i) => i % 5 === 0).map((a) => a.id);
  const sum = Q.summary(Q.check(d, ["Jinx", "Malphite"], ids));
  assert.ok(sum.mollppang.n > 30 && sum.mollppang.rate >= 0.95, JSON.stringify(sum));
  assert.ok(sum.twist.rate >= 0.9, JSON.stringify(sum));
});

// ---- 연계 상태 (등급과 따로): 강한 연계·일반·없음·충돌·불확실
const tiersOf = (cid, chosenIds) => {
  const c = champ(cid);
  return S.augmentTiers(d.augments, d.items, S.profile(c, d.tags), chosenIds.map(aug), [], d.rules, c, {}, "synergy");
};
const byName = (name) => d.augments.find((a) => a.name === name);

test("연계: 고른 증강이 없으면 연계 표시 없음", () => {
  const t = tiersOf("Lux", []);
  assert.ok(Object.values(t).every((x) => x.link === null));
});

test("연계: 궁극기 봉인(기본으로 돌아가기) + 궁극기 증강 = 효과 충돌, 이유 문장, 등급 한 단계 아래", () => {
  const t = tiersOf("Lux", ["ARAM_BacktoBasics"]);
  const ult = Object.entries(t).filter(([, x]) => x.link && x.link.kind === "conflict");
  assert.ok(ult.length > 0);
  assert.ok(ult.every(([, x]) => /궁극기/.test(x.link.why)), ult.map(([, x]) => x.link.why).join("\n"));
});

test("연계: 반대 방향 환산(적응형 능력치 ↔ 마법사 (물리))은 충돌", () => {
  assert.match(S.ruleConflict(aug("ARAM_escAPADe"), [aug("ARAM_ADAPt")], d.rules), /환산/);
  assert.equal(S.ruleConflict(byName("부식"), [aug("ARAM_ADAPt")], d.rules), null, "상관없는 증강은 충돌 아님");
});

test("연계: 숫자상 손해만으로는 충돌이라 하지 않는다 (규칙으로 아는 충돌만)", () => {
  const t = tiersOf("Lux", ["ARAM_BacktoBasics"]);
  for (const x of Object.values(t)) if (x.link && x.link.kind === "conflict") assert.ok(x.link.why, "충돌이면 이유가 있다");
  assert.ok(Object.values(t).some((x) => x.link && x.link.kind === "none" && x.interaction < 0), "음수 궁합은 '직접 연계 없음'");
});

test("연계: 숫자로 계산 못 하는 쪽이 있으면 판정 불확실, 계산되면 강한/일반/없음", () => {
  const t = tiersOf("Sona", [byName("흡수").id]);
  const kinds = new Set(Object.values(t).map((x) => x.link && x.link.kind));
  for (const k of ["strong", "none", "unsure"]) assert.ok(kinds.has(k), `${k} 없음: ${[...kinds]}`);
  for (const [id, x] of Object.entries(t)) {
    if (!x.link) continue;
    if (!S.hasNumbers(aug(id), d.rules)) assert.ok(["unsure", "conflict", "strong", "some"].includes(x.link.kind), `${aug(id).name}: 숫자 없는 증강을 '연계 없음'이라 단정`);
    if (x.link.kind === "strong") assert.ok(x.interaction >= S.SYNERGY_MARK);
  }
});

test("연계: 두 번째·세 번째 증강까지 누적 (고른 증강 2개 기준 궁합)", () => {
  const one = tiersOf("Sona", [byName("흡수").id]);
  const two = tiersOf("Sona", [byName("흡수").id, "ARAM_BigBrain"]);
  const id = byName("응급처치 키트").id;
  assert.notEqual(one[id].interaction, two[id].interaction);
});

test("치명적 리듬 베인: 무한의 대검이 불경한 히드라보다 위 (치명타 공속 중첩 · 쪼개기는 주변 적에게만)", () => {
  const c = d.champions.find((x) => x.id === "Vayne");
  const w = S.profile(c, d.tags);
  const rh = d.augments.find((a) => a.id === "CriticalRhythm");
  const owned = ["6672", "3006", "3036", "3153"].map((id) => d.items.find((i) => i.id === id));
  const rows = S.rankItems(d.items, w, [rh], owned, d.rules, c, { aram: true }, "augment");
  const at = (n) => rows.findIndex((r) => r.item.name === n);
  assert.ok(at("무한의 대검") < at("불경한 히드라"), `무한의 대검 ${at("무한의 대검") + 1}위 · 불경한 히드라 ${at("불경한 히드라") + 1}위`);
  // 치명타가 오를수록 공격 속도도 오른다
  const aug = S.augStatsOf([rh], d.rules, c);
  const as = (items) => Stats.computeStats(c, 18, items, aug, { aram: true }).total.AS;
  assert.ok(as([...owned, d.items.find((i) => i.id === "3031")]) > as(owned));
});

test("치명적 리듬 중첩 = 치명타 확률 × 초당 공격 × 유지 6초 (최대 10, 모두 게임 데이터 값)", () => {
  const c = d.champions.find((x) => x.id === "Vayne");
  const rh = d.augments.find((a) => a.id === "CriticalRhythm");
  const r = d.rules.augments.CriticalRhythm.statsResolved;
  assert.deepEqual([+r.critStackAS.toFixed(2), r.critStackMax, r.critStackSec], [0.06, 10, 6]);
  const aug = S.augStatsOf([rh], d.rules, c);
  // 아이템 없음: 치명타 25%(증강)만 → 6초에 터지는 치명타 수 = 0.25 × 공격 속도 × 6
  const t0 = Stats.computeStats(c, 18, [], aug, { aram: true }).total;
  assert.ok(Math.abs(t0.critStacks - 0.25 * t0.AS * 6) < 0.01, `중첩 ${t0.critStacks} · 공속 ${t0.AS}`);
  assert.ok(t0.critStacks < 2.5, "예전 가정(치명타 확률 × 10 = 2.5)보다 적다");
  // 치명타 100%면 최대 10
  const full = ["3031", "6672", "3046", "3036"].map((id) => d.items.find((i) => i.id === id)).filter(Boolean);
  const t1 = Stats.computeStats(c, 18, full, aug, { aram: true }).total;
  assert.equal(t1.CRIT, 1);
  assert.equal(t1.critStacks, 10);
});

test("효과 종류만 있는 증강은 몰빵 빌드에 그 효과 아이템을 하나는 넣는다 · 아이템으로 안 커지는 증강(lift {})은 안 넣는다", () => {
  const build = (cid, aid) => {
    const c = d.champions.find((x) => x.id === cid);
    const a = d.augments.find((x) => x.id === aid);
    return S.buildConcepts(d.items, S.profile(c, d.tags), [a], [], d.rules, c, {}, ["augment"])[0].pick.map((p) => p.item);
  };
  for (const cid of ["Ezreal", "Vayne"]) {
    const picks = build(cid, "Ability_SelfAOE_DoubleCast");
    assert.ok(picks.some((i) => (i.tags.HASTE || 0) >= 0.5), `${cid} 메아리 시전: ${picks.map((i) => i.name).join(", ")}`);
  }
  // 아이템 가속 = 사용 효과 아이템 (빌드할 때 게임 데이터 설명에서 채움)
  const act = d.rules.augments.ARAM_ApexInventor.items;
  assert.ok(act.length >= 5 && act.includes("요우무의 유령검"));
  assert.ok(build("Ezreal", "ARAM_ApexInventor").some((i) => act.includes(i.name)));
  // 눈덩이 가속·티아라 매혹은 아이템으로 안 커진다 → 빌드가 따라가지 않는다
  const ctx = S.augContext([d.augments.find((x) => x.id === "ARAM_Snowday"), d.augments.find((x) => x.id === "PromQueen")], d.rules);
  assert.deepEqual(ctx.lift, {});
});

test("적응형은 추가 공격력과 주문력 중 큰 쪽으로 (2026-10-17 트리스타나 판: 치명타 125% → 초과 25% = 적응형 18.75 → 공격력 +11.25, 실제 334.05)", () => {
  const its = ["루난의 허리케인", "크라켄 학살자", "광전사의 군화", "윤 탈 야생화살", "도미닉 경의 인사", "무한의 대검"].map(item);
  const augs = [aug("ARAM_Vulnerability")];
  const t = St.computeStats(champ("Tristana"), 18, its, S.augStatsOf(augs, d.rules, champ("Tristana")), { aram: true }).total;
  assert.equal(t.AP, 0);
  assert.ok(Math.abs(t.AD - 334.05) < 0.05, `AD ${t.AD}`);
});

test("그레이브즈 공격 속도는 재장전 속도 상한(0.651~0.891, 레벨 비례)에서 막힌다 (2026-10-17 판: 계산 1.78 · 실제 0.89)", () => {
  const its = ["크라켄 학살자", "루난의 허리케인", "윤 탈 야생화살", "광전사의 군화"].map(item);
  assert.ok(Math.abs(St.computeStats(champ("Graves"), 18, its, [], { aram: true }).total.AS - 0.891) < 0.001);
  assert.ok(St.computeStats(champ("Graves"), 1, its, [], { aram: true }).total.AS <= 0.651 + 1e-9);
  assert.ok(St.computeStats(champ("Jinx"), 18, its, [], { aram: true }).total.AS > 1.5, "다른 챔피언은 그대로");
  // 탄환 세례: 방향마다 8초 대기시간은 스킬 가속과 무관 → 가속 아이템을 끌어오지 않는다
  assert.ok(!("HASTE" in d.rules.augments.ARAM_FanTheHammer.tags));
});
