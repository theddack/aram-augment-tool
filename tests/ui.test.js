// 화면 흐름 테스트: 챔피언 → 증강 → 아이템 → 초기화, 선택지 비교, 쉽게 보기, 상대 고르기
const test = require("node:test");
const assert = require("node:assert/strict");
const { loadPage } = require("./page");

test("처음 화면: 챔피언 목록과 데이터 요약", async () => {
  const p = await loadPage();
  assert.equal(p.qa(".champ").length, p.data.champions.length);
  assert.match(p.text("#meta"), /증강 \d+개/);
  assert.deepEqual(p.errors, []);
});

test("챔피언 → 첫 증강 → 빌드", async () => {
  const p = await loadPage();
  p.pickChamp("Jinx");
  assert.match(p.text("#champ-card"), /징크스/);
  assert.equal(p.qa(".aug").length, p.data.augments.length, "자세히 보기는 증강 전체");
  assert.equal(p.qa("#concept-tabs button").length, 3, "빌드 3종");
  assert.equal(p.qa("#build .item").length, 6, "완성 빌드 6칸");
  p.click(p.augRow("ARAM_DrawYourSword"));
  assert.equal(p.qa("#chosen .slot[data-i]").length, 1);
  assert.match(p.text("#aug-title"), /다음 증강/);
  assert.equal(p.qa("#build .item").length, 6);
  assert.deepEqual(p.errors, []);
});

test("아이템: 고정·신발 1개·6칸·같은 계열", async () => {
  const p = await loadPage();
  p.pickChamp("Jinx");
  p.click(p.itemRow("무한의 대검"));
  assert.equal(p.qa("#chosen-items .islot[data-id]").length, 1);
  assert.ok(p.qa("#build .item.fixed").length >= 1, "고른 아이템은 빌드에 고정");
  const boots = p.qa("#item-list .item").filter((e) => e.textContent.includes("신발"));
  p.click(boots[0]);
  p.click(p.qa("#item-list .item").filter((e) => e.textContent.includes("신발"))[1]);
  assert.equal(p.qa("#chosen-items .islot[data-id]").length, 2, "신발은 1개까지");
  assert.ok(p.alerts.some((m) => m.includes("신발")), "신발 2개째는 안내");
  for (let i = 0; i < 8; i++) {
    const it = p.qa("#item-list .item").find((e) => !e.textContent.includes("신발"));
    if (it) p.click(it);
  }
  assert.equal(p.qa("#chosen-items .islot[data-id]").length, 6, "최대 6칸");
  p.click(p.q("#reset-items"));
  assert.equal(p.qa("#chosen-items .islot[data-id]").length, 0);
});

test("같은 계열 고유 효과는 하나만 (주문 검)", async () => {
  const p = await loadPage();
  p.pickChamp("Ezreal");
  p.click(p.itemRow("삼위일체"));
  p.click(p.itemRow("리치베인"));
  assert.equal(p.qa("#chosen-items .islot[data-id]").length, 1);
  assert.ok(p.alerts.some((m) => m.includes("주문 검")));
});

test("조건 없는 챔피언은 ⛔ 효과 없음 (위키 적용 스킬 목록)", async () => {
  const p = await loadPage();
  p.pickChamp("Lux");
  const row = p.augRow("ARAM_SpinToWin");
  assert.ok(row, "승리를 위한 회전 행");
  assert.match(row.textContent, /효과 없음|⛔/);
});

test("이번 선택지: 3개 비교·추천 1개·새로고침 권장", async () => {
  const p = await loadPage();
  p.pickChamp("Seraphine");
  const names = ["치명적 치유", "도주 계획", "거인"];
  p.qa("#offers input[list]").forEach((inp, i) => {
    inp.value = names[i];
    for (const t of ["input", "change"]) inp.dispatchEvent(new p.w.Event(t, { bubbles: true }));
  });
  const rows = p.qa(".offer-table tr").slice(1);
  assert.equal(rows.length, 3);
  assert.equal(rows.filter((r) => r.classList.contains("best")).length, 1, "추천은 하나");
  assert.deepEqual(p.errors, []);
});

test("레벨만 바뀐 게임 연동은 순위를 다시 계산하지 않는다", async () => {
  const p = await loadPage();
  p.w.applyLive({ enabled: true, inGame: true, champion: "Jinx", level: 10, items: ["3031"] });
  const before = p.q("#aug-list").innerHTML;
  p.w.applyLive({ enabled: true, inGame: true, champion: "Jinx", level: 11, items: ["3031"] });
  assert.equal(p.q("#level-val").textContent, "11");
  assert.equal(p.q("#aug-list").innerHTML, before, "증강 목록은 그대로");
  assert.equal(p.qa("#chosen-items .islot[data-id]").length, 1, "산 아이템 반영");
});

test("쉽게 보기: 15개 + 더 보기, 근거 펼쳐도 선택 안 됨, 자세히 보기 전환", async () => {
  const p = await loadPage({ simple: true });
  assert.ok(p.doc.body.classList.contains("simple"));
  p.pickChamp("Jinx");
  assert.equal(p.qa(".aug").length, 15);
  assert.ok(p.q(".more-augs"));
  const basis = p.q(".aug .basis summary");
  assert.ok(basis, "근거 보기 있음");
  p.click(basis);
  assert.equal(p.qa("#chosen .slot[data-i]").length, 0, "근거를 눌러도 증강 선택 안 됨");
  p.click(p.q(".more-augs"));
  assert.equal(p.qa(".aug").length, p.data.augments.length);
  p.click(p.q("#simple-toggle"));
  assert.ok(!p.doc.body.classList.contains("simple"));
  assert.ok(p.qa("#metrics .tile").length === 6, "자세히 보기로 바꾸면 능력치 표가 그려짐");
});

test("이유 문장은 실제 숫자 (A → B)", async () => {
  const p = await loadPage({ simple: true });
  p.pickChamp("Seraphine");
  const reasons = p.qa(".aug .why").map((e) => e.textContent);
  assert.ok(reasons.some((r) => /\(\d[\d,.]* → \d[\d,.]*/.test(r)), reasons.slice(0, 3).join(" / "));
  assert.ok(!reasons.some((r) => /\d{3,}% 늘어요/.test(r)), "큰 퍼센트 문장 없음");
});

test("상대 고르기: 탱커를 고르면 피해가 줄고, 기준으로 돌아오면 원래대로", async () => {
  const p = await loadPage();
  p.pickChamp("Lux");
  const combo = () => p.text("#metrics .tile .big");
  const base = combo();
  const sel = p.q("#target-champ");
  sel.value = "Malphite"; sel.dispatchEvent(new p.w.Event("change"));
  assert.match(p.text("#target-info"), /말파이트/);
  assert.ok(Number(combo().replace(/,/g, "")) < Number(base.replace(/,/g, "")));
  sel.value = ""; sel.dispatchEvent(new p.w.Event("change"));
  assert.equal(combo(), base);
});

test("받은 데이터의 이상한 id 는 화면에서 뺀다", async () => {
  const p = await loadPage();
  const d = { champions: [{ id: "Jinx", key: "222" }, { id: "<img src=x>", key: "1" }], augments: [{ id: "A_1" }, { id: "../x" }], items: [{ id: "3031" }, { id: "1;x" }] };
  const out = p.w.sanitizeData(d);
  assert.deepEqual(out.champions.map((c) => c.id), ["Jinx"]);
  assert.deepEqual(out.augments.map((a) => a.id), ["A_1"]);
  assert.deepEqual(out.items.map((i) => i.id), ["3031"]);
});

test("쉽게 보기에서도 '다음에 살 아이템'과 추천 빌드가 나온다", async () => {
  const p = await loadPage({ simple: true });
  p.pickChamp("Jinx");
  assert.ok(p.qa("#next-buy .next-row").length >= 1, "다음에 살 아이템");
  assert.equal(p.qa("#build .item").length, 6, "추천 빌드");
  p.click(p.qa(".aug")[0]);
  assert.ok(p.qa("#next-buy .next-row").length >= 1, "증강을 고른 뒤에도");
});

test("지금 골드로 살 것: 남은 금액·살 부품·완성 가능", async () => {
  const p = await loadPage({ simple: true });
  const IE = p.data.items.find((i) => i.name === "무한의 대검");
  const b1 = p.w.buyNow(IE, 1300, []);
  assert.equal(b1.left, IE.gold);
  assert.equal(b1.part.name, "B.F. 대검", "1300골드로 살 수 있는 가장 비싼 부품");
  const owned = ["1038", "1037"];  // B.F. 대검 + 곡괭이
  const b2 = p.w.buyNow(IE, 1400, owned);
  assert.equal(b2.left, IE.gold - 1300 - 875);
  assert.ok(b2.done, "남은 금액보다 골드가 많으면 완성 가능");
  const b3 = p.w.buyNow(IE, 100, []);
  assert.equal(b3.part, null, "가장 싼 부품보다 골드가 적으면 없음");
  // 게임 연동: 골드만 바뀌면 순위는 그대로, '지금 살 것' 줄만 갱신
  p.w.applyLive({ enabled: true, inGame: true, champion: "Jinx", level: 9, items: ["1001"], gold: 500 });
  assert.match(p.text("#buy-now"), /지금 골드 500/);
  const list = p.q("#aug-list").innerHTML;
  p.w.applyLive({ enabled: true, inGame: true, champion: "Jinx", level: 9, items: ["1001"], gold: 1800 });
  assert.match(p.text("#buy-now"), /지금 골드 1,800/);
  assert.equal(p.q("#aug-list").innerHTML, list);
  p.w.applyLive({ enabled: true, inGame: false });
  assert.equal(p.text("#buy-now"), "", "게임이 끝나면 지움");
});

test("상대 능력치는 앞에 고른 상대와 무관", async () => {
  const p = await loadPage();
  p.pickChamp("Lux");
  const sel = p.q("#target-champ");
  const pick = (id) => { sel.value = id; sel.dispatchEvent(new p.w.Event("change")); return p.text("#target-info"); };
  const a = pick("Garen");
  pick("Malphite");
  const b = pick("Garen");
  assert.equal(a, b);
});

test("같은 챔피언으로 다음 게임: 고른 증강을 비운다", async () => {
  const p = await loadPage();
  p.w.applyLive({ enabled: true, inGame: true, champion: "Jinx", level: 5, items: [] });
  p.click(p.qa(".aug")[0]);
  assert.equal(p.qa("#chosen .slot[data-i]").length, 1);
  p.w.applyLive({ enabled: true, inGame: false });
  p.w.applyLive({ enabled: true, inGame: true, champion: "Jinx", level: 1, items: [] });
  assert.equal(p.qa("#chosen .slot[data-i]").length, 0);
});

test("쉽게 보기 '전체 보기'는 챔피언을 바꾸면 다시 15개", async () => {
  const p = await loadPage({ simple: true });
  p.pickChamp("Jinx");
  p.click(p.q(".more-augs"));
  p.pickChamp("Lux");
  assert.equal(p.qa(".aug").length, 15);
});

test("증강 인식: 읽은 선택지를 채우고 게임 화면 카드에 보내며, 닫히면 고른 증강을 넣는다", async () => {
  const p = await loadPage({ simple: true });
  const sent = [];
  p.w.pywebview = { api: { set_overlay: (x) => sent.push(x) } };
  p.w.applyLive({ enabled: true, inGame: true, champion: "Poppy", level: 6, items: ["3084"] });
  const offers = ["눈 폭발", "강타자", "정신 변환"];
  p.w.applyAug({ offers, seq: 1, picked: null, pickSeq: 0 });
  assert.deepEqual(p.qa("#offers input[type=search]").map((e) => e.value), offers);
  const cards = sent.at(-1).cards;
  assert.equal(cards.filter(Boolean).length, 3, "카드 3장 모두 게임 화면 표시");
  assert.ok(cards.every((c) => c.label), "평가 글자가 있다");
  // 같은 seq 는 다시 그리지 않는다 (직접 지운 걸 덮어쓰지 않게)
  p.click(p.q("#reset-offers"));
  p.w.applyAug({ offers, seq: 1, picked: null, pickSeq: 0 });
  assert.equal(p.qa("#offers input[type=search]")[0].value, "");
  // 선택 화면이 닫힘 → 고른 증강
  p.w.applyAug({ offers: null, seq: 1, picked: "강타자", pickedFrom: offers, pickSeq: 1 });
  assert.match(p.text("#chosen"), /강타자/);
  assert.deepEqual(p.qa("#offers input[type=search]").map((e) => e.value), ["", "", ""]);
  // 못 정했으면 선택지를 남기고 직접 누르게 한다
  p.w.applyAug({ offers: null, seq: 2, picked: null, pickedFrom: ["눈 폭발", "탱크 엔진", "정신 변환"], pickSeq: 2 });
  assert.match(p.text("#offer-result"), /이걸로 선택/);
  assert.deepEqual(p.errors, []);
});
