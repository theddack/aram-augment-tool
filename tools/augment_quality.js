// 추천 품질 점검: '증강 몰빵' 빌드가 고른 증강을 실제로 살리는지, '변칙'이 증강에 받쳐질 때만 뒤집히는지.
// 실제 게임 결과가 아니라 계산 결과끼리의 일관성 검사다 (게임 기록이 모이면 그걸로 바꾼다).
//   node tools/augment_quality.js            대표 챔피언 12명 × 증강 전부
//   node tools/augment_quality.js --json     결과를 JSON 으로
// 몰빵 기준:
//   업그레이드 증강(징수의 총 업그레이드 등) = 후보 아이템 중 하나가 빌드에 있어야 함
//   숫자로 계산되는 증강 = 몰빵 빌드에서 증강이 보태는 가치 ≥ 증강 없이 짠 평소(균형) 빌드에서 보태는 가치
//                          (주문력 +80 같은 고정 수치는 주문력 아이템을 사도 안 커진다 → 아이템 종류로 보지 않고 가치로 본다)
//   효과 종류만 있는 증강 = 주된 효과 종류(강도 0.6 이상, 아이템으로 살 수 있는 것)를 가진 아이템이 1개 이상
//   바뀜 = 평소 빌드와 다른 아이템 수
// 변칙 기준:
//   증강 없음      → 받쳐 주는 정도 0 (화면에 '실험 빌드'로 표시)
//   받쳐 주는 증강 → 뒤집힌 쪽 아이템이 2개 이상
const path = require("path");
const WEB = path.join(__dirname, "..", "web");
global.Stats = require(path.join(WEB, "stats.js"));
const S = require(path.join(WEB, "scoring.js"));

const REPS = ["Jinx", "Ezreal", "Lux", "Annie", "Malphite", "Garen", "Zed", "Sona", "Darius", "Yasuo", "Vayne", "Nami"];
const KEY_MIN = 0.6;  // 주된 효과로 볼 강도
const ITEM_MIN = 0.5; // 아이템이 그 효과를 가졌다고 볼 강도

function check(d, champIds = REPS, augIds = null) {
  const champs = champIds.map((id) => d.champions.find((c) => c.id === id)).filter(Boolean);
  const legendary = d.items.filter((i) => i.kind === "legendary");
  // 아이템으로 살 수 있는 효과 종류 (궁극기·고정 피해처럼 아이템이 거의 없는 건 '살림' 판정에서 뺀다)
  const buyable = new Set();
  for (const it of legendary) for (const [t, v] of Object.entries(it.tags || {})) if (v >= ITEM_MIN) buyable.add(t);
  const count = {};
  for (const it of legendary) for (const [t, v] of Object.entries(it.tags || {})) if (v >= ITEM_MIN) count[t] = (count[t] || 0) + 1;
  for (const t of Object.keys(count)) if (count[t] < 3) buyable.delete(t);

  const augs = d.augments.filter((a) => !augIds || augIds.includes(a.id));
  const rows = [];
  const twist = [];
  for (const c of champs) {
    const w = S.profile(c, d.tags);
    const baseItems = S.buildConcepts(d.items, w, [], [], d.rules, c, {}, ["balanced"])[0].pick.map((p) => p.item);
    const base = baseItems.map((i) => i.name);
    const t0 = S.buildConcepts(d.items, w, [], [], d.rules, c, {}, ["twist"])[0];
    twist.push({ champ: c.id, aug: null, s: t0.twist.s, ok: t0.twist.s === 0 });
    for (const a of augs) {
      if (a.category) continue;  // 퀘스트·무작위·소환사 주문: 빌드로 살리는 증강이 아님
      const rule = (d.rules.augments || {})[a.id] || {};
      const unmet = a.eligible ? !a.eligible.includes(c.id) : rule.requires && !((S.withChosenFeatures(c, [a], d.rules).features || []).includes(rule.requires));
      if (unmet) continue;  // 이 챔피언에겐 발동하지 않는 증강
      const conv = rule.convert || {};
      const b = S.buildConcepts(d.items, w, [a], [], d.rules, c, {}, ["augment"])[0];
      const picks = b.pick.map((p) => p.item);
      const tagged = (it) => {
        const tg = { ...(it.tags || {}) };
        for (const [f, to] of Object.entries(conv)) if (tg[f]) tg[to] = Math.max(tg[to] || 0, tg[f]);
        if (tg.VAMP) tg.HEALSHIELD = Math.max(tg.HEALSHIELD || 0, tg.VAMP);  // 흡혈도 회복이다 (회복 증가 증강)
        return tg;
      };
      const changed = picks.filter((it) => !base.includes(it.name)).length;
      const wanted = (rule.items || []).filter((n) => legendary.some((i) => i.name === n));
      let kind, ok, why = "";
      if (wanted.length) {
        kind = "업그레이드";
        ok = picks.some((it) => wanted.includes(it.name));
        if (!ok) why = wanted.join("/");
      } else if (S.hasNumbers(a, d.rules)) {
        kind = "숫자";
        const mine = S.augContribution(picks, w, [], a, d.rules, c);
        const usual = S.augContribution(baseItems, w, [], a, d.rules, c);
        ok = mine >= usual - 0.002;
        if (!ok) why = `몰빵 ${(mine * 100).toFixed(1)} < 평소 ${(usual * 100).toFixed(1)}`;
      } else {
        const tags = rule.tags || a.tags || {};
        const zero = new Set(rule.zero || []);
        const key = Object.entries(tags).filter(([t, v]) => v >= KEY_MIN && !zero.has(t)).map(([t]) => conv[t] || t).filter((t) => buyable.has(t));
        if (!key.length) continue;  // 아이템으로 살릴 효과가 없는 증강 (궁극기·이동 등)
        kind = "효과";
        const miss = key.filter((t) => !picks.some((it) => (tagged(it)[t] || 0) >= ITEM_MIN));
        ok = miss.length < key.length;
        if (!ok) why = miss.map((t) => S.TAG_LABEL[t] || t).join(",");
      }
      rows.push({ champ: c.id, aug: a.id, name: a.name, kind, ok, why, changed });

      const sup = S.twistSupport(w, S.augContext([a], d.rules));
      if (sup.s >= 0.5) {
        const tb = S.buildConcepts(d.items, w, [a], [], d.rules, c, {}, ["twist"])[0];
        const opp = tb.pick.filter((p) => sup.opp.some((t) => {
          return (tagged(p.item)[t] || 0) >= ITEM_MIN;
        })).length;
        twist.push({ champ: c.id, aug: a.id, name: a.name, s: sup.s, opp, ok: opp >= 2 });
      }
    }
  }
  return { rows, twist };
}

function summary({ rows, twist }) {
  const ok = rows.filter((r) => r.ok).length;
  const tok = twist.filter((r) => r.ok).length;
  const avgChanged = rows.reduce((t, r) => t + r.changed, 0) / Math.max(1, rows.length);
  return { mollppang: { n: rows.length, ok, rate: ok / Math.max(1, rows.length), avgChanged }, twist: { n: twist.length, ok: tok, rate: tok / Math.max(1, twist.length) } };
}

if (require.main === module) {
  const d = require(path.join(WEB, "data.json"));
  const res = check(d);
  const sum = summary(res);
  if (process.argv.includes("--json")) { console.log(JSON.stringify({ sum, ...res }, null, 1)); process.exit(0); }
  const pct = (v) => `${(v * 100).toFixed(1)}%`;
  console.log(`증강 몰빵: ${sum.mollppang.ok}/${sum.mollppang.n} 살림 (${pct(sum.mollppang.rate)}) · 균형 대비 평균 ${sum.mollppang.avgChanged.toFixed(1)}개 바뀜`);
  console.log(`변칙: ${sum.twist.ok}/${sum.twist.n} 정상 (${pct(sum.twist.rate)})`);
  const byAug = {};
  for (const r of res.rows.filter((x) => !x.ok)) (byAug[`${r.name} [${r.kind}]`] = byAug[`${r.name} [${r.kind}]`] || []).push(`${r.champ}(${r.why})`);
  const list = Object.entries(byAug).sort((a, b) => b[1].length - a[1].length);
  if (list.length) console.log("\n몰빵 빌드가 못 살린 증강:");
  for (const [n, cs] of list) console.log(`  ${n}: ${cs.join(" ")}`);
  const bad = res.twist.filter((r) => !r.ok);
  if (bad.length) console.log("\n변칙이 이상한 경우:");
  for (const r of bad) console.log(`  ${r.champ} ${r.name || "(증강 없음)"}: 받쳐 주는 정도 ${r.s} · 뒤집힌 쪽 아이템 ${r.opp ?? "-"}개`);
}

module.exports = { check, summary, REPS };
