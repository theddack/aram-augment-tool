// 기준 상대(stats.js TARGET)의 근거: 전 챔피언의 18레벨 균형 빌드 능력치 중앙값을 계산한다.
//   node tools/target_median.js   → 체력·방어력·마법 저항력 중앙값 (패치 후 다시 돌려서 stats.js 에 반영)
// 중앙값을 쓰는 이유: 평균은 탱커(방어력 300+)에 끌려 올라가서 '흔한 상대'와 멀어진다.
const W = __dirname + "/../web/";
global.Stats = require(W + "stats.js");
const S = require(W + "scoring.js");
const d = require(W + "data.json");
const rows = d.champions.map((c) => {
  const [b] = S.buildConcepts(d.items, S.profile(c, d.tags), [], [], d.rules, c, { aram: true }, ["balanced"]);
  const t = Stats.computeStats(c, 18, b.pick.map((p) => p.item), [], { aram: true }).total;
  return { HP: t.HP, ARMOR: t.ARMOR, MR: t.MR };
});
const median = (k) => { const a = rows.map((r) => r[k]).sort((x, y) => x - y); return Math.round(a[a.length >> 1]); };
console.log(JSON.stringify({ patch: d.version, champions: rows.length, HP: median("HP"), ARMOR: median("ARMOR"), MR: median("MR") }));
