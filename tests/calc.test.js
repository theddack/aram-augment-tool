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
