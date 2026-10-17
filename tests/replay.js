// 실제 경기 기록 다시 돌리기: 기록한 게임 연동 데이터(5분마다 한 장)를 계산에 넣어 게임 실제 능력치와 비교한다.
//   node tests/replay.js                차이를 표로 보기
//   node tests/replay.js --baseline     지금 차이를 기준(game.json gaps)으로 저장 (계산을 고친 뒤 좋아졌을 때만)
// 기록은 tests/data/games/<판>/ : live_<초>s.json (소환사 이름은 '플레이어0~9'로 가린 것) + game.json (고른 증강·시각)
const fs = require("fs");
const path = require("path");
const WEB = path.join(__dirname, "..", "web");
global.Stats = global.Stats || require(path.join(WEB, "stats.js"));
const S = require(path.join(WEB, "scoring.js"));
const d = require(path.join(WEB, "data.json"));
const GAMES = path.join(__dirname, "data", "games");
// 계산 키 → 게임 연동 championStats 키
const LIVE = { HP: "maxHealth", AD: "attackDamage", AP: "abilityPower", ARMOR: "armor", MR: "magicResist", CRIT: "critChance", HASTE: "abilityHaste" };

function replay(name) {
  const dir = path.join(GAMES, name);
  const game = JSON.parse(fs.readFileSync(path.join(dir, "game.json"), "utf8"));
  const champ = d.champions.find((c) => c.id === game.champion);
  const rows = [];
  for (const f of fs.readdirSync(dir).filter((x) => /^live_\d+s\.json$/.test(x))) {
    const raw = JSON.parse(fs.readFileSync(path.join(dir, f), "utf8"));
    const ap = raw.activePlayer || {};
    const t = (raw.gameData || {}).gameTime || 0;
    if (!ap.championStats || !ap.championStats.moveSpeed) continue;  // 불러오는 중 (능력치가 0)
    const me = raw.allPlayers.find((p) => p.riotId === ap.riotId);
    const ids = me.items.map((i) => String(i.itemID));
    // 완성 아이템 + 부품 (게임 중 지금 능력치는 부품도 더한다: app.js partItems)
    const items = ids.map((id) => d.items.find((i) => i.id === id) || ((d.parts || {})[id] && (d.parts[id].stats ? { id, name: d.parts[id].name, stats: d.parts[id].stats } : null))).filter(Boolean);
    const augs = game.augments.filter(([, at]) => at <= t).map(([id]) => d.augments.find((a) => a.id === id));
    const c = S.withChosenFeatures(champ, augs, d.rules);
    // 게임 중 지금 능력치처럼 즉시 골드 환산은 뺀다 (app.js nowAugStats)
    const augSt = S.augStatsOf(augs, d.rules, c).map((s) => s && s.goldValue ? { ...s, goldValue: 0 } : s);
    const st = Stats.computeStats(c, ap.level, items, augSt, { aram: true }).total;
    const stats = {};
    for (const [k, lk] of Object.entries(LIVE)) stats[k] = [Math.round((st[k] || 0) * 100) / 100, Math.round(ap.championStats[lk] * 100) / 100];
    rows.push({ file: f, t: Math.round(t), level: ap.level, items: items.map((i) => i.name),
      parts: ids.filter((id) => (d.parts || {})[id]).length, stats });
  }
  return { game, rows: rows.sort((a, b) => a.t - b.t) };
}

const games = () => fs.existsSync(GAMES) ? fs.readdirSync(GAMES).filter((n) => fs.existsSync(path.join(GAMES, n, "game.json"))) : [];

if (require.main === module) {
  for (const name of games()) {
    const { game, rows } = replay(name);
    console.log(`\n${name} (${game.champion}) — 계산 / 실제`);
    for (const r of rows) {
      console.log(`  ${r.t}초 ${r.level}레벨 부품 ${r.parts}개 · ` + Object.entries(r.stats).map(([k, [c, x]]) => `${k} ${c}/${x}`).join("  "));
    }
    if (process.argv.includes("--baseline")) {
      game.gaps = Object.fromEntries(rows.map((r) => [r.file, Object.fromEntries(Object.entries(r.stats).map(([k, [c, x]]) => [k, Math.round(Math.abs(c - x) * 100) / 100]))]));
      fs.writeFileSync(path.join(GAMES, name, "game.json"), JSON.stringify(game, null, 2) + "\n");
      console.log("  → 기준 저장");
    }
  }
}

module.exports = { replay, games };
