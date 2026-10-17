// 실제 경기 기록 회귀 테스트: 계산 능력치가 게임 실제 값에서 더 멀어지면 실패 (기준 = game.json gaps, tests/replay.js)
const test = require("node:test");
const assert = require("node:assert/strict");
const { replay, games } = require("./replay.js");

test("실제 경기 기록: 계산 능력치가 기준보다 실제에서 멀어지지 않는다", () => {
  assert.ok(games().length >= 1, "기록이 있다");
  for (const name of games()) {
    const { game, rows } = replay(name);
    assert.ok(rows.length >= 3, `${name}: 능력치가 찍힌 기록 ${rows.length}장`);
    for (const r of rows) {
      const base = (game.gaps || {})[r.file] || {};
      for (const [k, [calc, real]] of Object.entries(r.stats)) {
        const gap = Math.abs(calc - real);
        assert.ok(gap <= (base[k] ?? 0) + 0.05, `${name} ${r.t}초 ${k}: 계산 ${calc} · 실제 ${real} (기준 차이 ${base[k] ?? 0})`);
      }
    }
  }
});

test("베인 판: 부품이 없을 때 공격력·방어력·마법 저항력·치명타는 실제와 같다 (레벨당 공격력 = 위키 값)", () => {
  const { rows } = replay("vayne_20261015");
  const r = rows.find((x) => x.t === 600);
  for (const k of ["AD", "ARMOR", "MR", "CRIT"]) assert.ok(Math.abs(r.stats[k][0] - r.stats[k][1]) < 0.05, `${k} ${r.stats[k]}`);
});

for (const [name, why] of [["kaisa_20261017", "부품 공격력 20~35 가 빠져 낮던 것"], ["samira_20261017", "징수의 총 업그레이드 250골드를 공격력으로 환산해 두 번 세던 것"]])
test(`${name}: 모든 기록에서 능력치가 실제와 같다 (${why})`, () => {
  const { rows } = replay(name);
  assert.ok(rows.some((r) => r.parts > 0), "부품을 든 기록이 있다");
  for (const r of rows) for (const [k, [calc, real]] of Object.entries(r.stats)) assert.ok(Math.abs(calc - real) < 0.05, `${r.t}초 ${k}: 계산 ${calc} · 실제 ${real}`);
});
