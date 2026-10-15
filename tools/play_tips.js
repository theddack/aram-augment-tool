// 쓰는 법 문장 목록: 증강마다 화면(🎮)에 나오는 '쓰는 법'을 표로 뽑는다. 사람이 눈으로 맞는지 확인하는 용도.
// 문장은 화면 코드(web/app.js playTip)를 그대로 불러서 만든다 (같은 규칙을 두 번 쓰지 않으려고).
//   node tools/play_tips.js > docs/쓰는법_목록.md
const { loadPage } = require("../tests/page");

(async () => {
  const p = await loadPage();
  p.pickChamp("Jinx");  // 쓰는 법은 챔피언과 무관 (챔피언이 있어야 화면 함수가 돈다)
  const d = p.data;
  const strip = (h) => String(h || "").replace(/<[^>]+>/g, "").replace(/\s+/g, " ").trim();
  const rows = d.augments.slice().sort((a, b) => a.rarity - b.rarity || a.name.localeCompare(b.name, "ko"));
  const out = ["# 증강 쓰는 법 목록", "",
    `증강 ${rows.length}개의 화면 '🎮 쓰는 법' 문장 (데이터 ${d.version}). 설명과 안 맞는 문장을 찾아 rules.json 이나 web/app.js(playTip)를 고친다.`,
    "규칙으로 만든 문장이라 실제 게임에서 확인하지 않았어요. 빈 칸 = 문장을 만들 근거가 없어 표시하지 않음.", "",
    "| 등급 | 증강 | 쓰는 법 | 게임 설명 (앞 80자) |", "|---|---|---|---|"];
  for (const a of rows) {
    const tip = p.w.playTip(a);
    out.push(`| ${["실버", "골드", "프리즘"][a.rarity] || a.rarity} | ${a.name} | ${tip.replace(/\|/g, "/")} | ${strip(a.desc).slice(0, 80).replace(/\|/g, "/")} |`);
  }
  const empty = rows.filter((a) => !p.w.playTip(a)).length;
  out.push("", `문장 있음 ${rows.length - empty}개 · 없음 ${empty}개`);
  console.log(out.join("\n"));
  process.exit(0);
})();
