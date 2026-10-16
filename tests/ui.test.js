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
  assert.equal(p.qa(".offer-table .how").length, 3, "선택지마다 쓰는 법");
  // 몰빵 빌드는 평가를 먼저 보여 준 다음 채운다 (게임 화면 카드가 늦지 않게)
  assert.match(p.text("#offer-result"), /계산 중/);
  await new Promise((r) => setTimeout(r, 0));
  assert.doesNotMatch(p.text("#offer-result"), /계산 중/);
  assert.equal(p.qa(".offer-table .mini-build").filter((td) => td.querySelector("img")).length, 3, "몰빵 빌드 아이콘");
  assert.ok(p.qa(".offer-table .chip.fun").length <= 1, "재미 픽은 많아야 하나");
  assert.ok(!rows.find((r) => r.classList.contains("best")).querySelector(".chip.fun"), "재미 픽은 추천과 다른 것");
  assert.deepEqual(p.errors, []);
});

test("연계 상태 칩: 고른 증강이 있으면 선택지·목록에 등급과 따로 보인다", async () => {
  const p = await loadPage();
  p.pickChamp("Lux");
  assert.equal(p.qa("#aug-list .link").length, 0, "첫 증강 전엔 연계 표시 없음");
  p.click(p.augRow("ARAM_BacktoBasics"));
  assert.ok(p.qa("#aug-list .link").length > 0);
  assert.ok(p.qa("#aug-list .link.l-conflict").length > 0, "궁극기 증강은 효과 충돌");
  const conflict = p.qa("#aug-list .aug").find((e) => e.querySelector(".link.l-conflict"));
  assert.match(conflict.textContent, /궁극기/, "충돌 이유 문장");
  assert.deepEqual(p.errors, []);
});

test("쓰는 법·재미 픽·변칙 설명", async () => {
  const p = await loadPage();
  p.pickChamp("Jinx");
  // 변칙 탭: 증강이 없으면 실험 빌드라고 알린다
  p.click(p.qa("#concept-tabs button").find((b) => b.dataset.k === "twist"));
  assert.match(p.text("#concept-desc"), /실험 빌드/);
  // 고른 증강 밑에 쓰는 법
  p.click(p.augRow("ARAM_BigBrain"));
  assert.match(p.text("#chosen"), /🎮 전술적 대비/);
  p.click(p.qa("#concept-tabs button").find((b) => b.dataset.k === "twist"));
  assert.match(p.text("#concept-desc"), /전술적 대비.*받쳐 주는 변칙/);
  // 선택지: 변칙 열쇠·재미 픽 (빌드가 크게 바뀌는 증강)
  p.click(p.qa("#chosen .slot[data-i]")[0]);
  const names = ["전술적 대비", "도주 계획", "거인"];
  p.qa("#offers input[list]").forEach((inp, i) => {
    inp.value = names[i];
    for (const t of ["input", "change"]) inp.dispatchEvent(new p.w.Event(t, { bubbles: true }));
  });
  assert.match(p.text("#offer-result"), /변칙 열쇠/);
  // 재미 픽은 플레이 방향이 실제로 바뀌는 것(변신·변칙 열쇠)만
  for (const row of p.qa(".offer-table tr").slice(1)) {
    if (row.querySelector(".chip.fun")) assert.match(row.textContent, /변신|변칙 열쇠/);
  }
  assert.match(p.text("#offer-result"), /같이 살 것|🎮/);
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
  // 골드만 1초마다 오르면 상점이 닫혀 있을 땐 10초에 한 번만 다시 그린다 (게임 중 CPU)
  p.w.applyLive({ enabled: true, inGame: true, champion: "Jinx", level: 9, items: ["1001"], gold: 900 });
  assert.match(p.text("#buy-now"), /지금 골드 500/, "상점이 닫혀 있으면 바로 안 그림");
  // 상점을 열면 바로 (게임 화면 상자에 보인다)
  p.w.applyLive({ enabled: true, inGame: true, champion: "Jinx", level: 9, items: ["1001"], gold: 1800, aug: { shop: { x: 0 } } });
  assert.match(p.text("#buy-now"), /지금 골드 1,800/);
  assert.equal(p.q("#aug-list").innerHTML, list);
  // 부품이 바뀌면 상점이 닫혀 있어도 바로
  p.w.applyLive({ enabled: true, inGame: true, champion: "Jinx", level: 9, items: ["1001", "1036"], gold: 1450 });
  assert.match(p.text("#buy-now"), /지금 골드 1,450/);
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
  // 증강 칸 그림으로 확인: 못 정한 것은 채우고 (가벼운 모드도)
  p.w.applyAug({ offers: null, seq: 2, picked: "정신 변환", pickedFrom: ["눈 폭발", "탱크 엔진", "정신 변환"], by: "hud", replaces: null, pickSeq: 3 });
  assert.match(p.text("#chosen"), /정신 변환/);
  // 클릭 짐작이 틀렸으면 빼고 바꾼다
  p.w.applyAug({ offers: null, seq: 1, picked: "눈 폭발", pickedFrom: offers, by: "hud", replaces: "강타자", pickSeq: 4 });
  assert.match(p.text("#chosen"), /눈 폭발/);
  assert.doesNotMatch(p.text("#chosen"), /강타자/);
  assert.deepEqual(p.errors, []);
});

test("챔피언 선택 중: 롤에서 고른 챔피언을 따라가고, 대기석에서 바꾸면 화면·상점 추천도 바뀐다", async () => {
  const p = await loadPage({ simple: true });
  const writes = [];
  const api = { write_item_set: async (key, title) => { writes.push(title); return { ok: true, blocks: 3 }; } };
  p.q("#itemset-toggle").checked = true;
  await p.w.applyLcu(api, { connected: true, phase: "ChampSelect", championKey: 39 });
  assert.match(p.text("#champ-card"), /이렐리아/);
  await p.w.applyLcu(api, { connected: true, phase: "ChampSelect", championKey: 799 });  // 대기석에서 암베사로
  assert.match(p.text("#champ-card"), /암베사/);
  assert.match(writes.at(-1), /암베사/);
  assert.match(p.text("#itemset-status"), /암베사 상점 추천 등록됨/);
  p.pickChamp("Jinx");  // 그 사이 다른 챔피언을 눌러 보면
  await p.w.applyLcu(api, { connected: true, phase: "ChampSelect", championKey: 799 });
  assert.match(p.text("#champ-card"), /징크스/, "롤 챔피언이 그대로면 덮어쓰지 않는다");
  assert.deepEqual(p.errors, []);
});

test("게임에서 산 부품도 고른 아이템 밑에 보인다", async () => {
  const p = await loadPage({ simple: true });
  p.w.applyLive({ enabled: true, inGame: true, champion: "Nami", level: 3, items: ["2052", "1001", "1042"], gold: 500 });
  assert.match(p.text("#chosen-items"), /게임에서 산 부품: 장화 · 단검/);
  p.w.applyLive({ enabled: true, inGame: true, champion: "Nami", level: 3, items: ["2052", "1001", "1042", "1052"], gold: 100 });
  assert.match(p.text("#chosen-items"), /증폭의 고서/, "골드·부품만 바뀌어도 갱신");
});

test("게임 화면 상자(상점 옆): 산 아이템 평가, 증강 TOP 은 넣지 않음", async () => {
  const p = await loadPage({ simple: true });
  const sent = [];
  p.w.pywebview = { api: { set_overlay: (x) => sent.push(x) } };
  p.w.applyLive({ enabled: true, inGame: true, champion: "Irelia", level: 6, items: ["3153"] });  // 몰락한 왕의 검
  p.w.sendOverlay();
  const hud = sent.at(-1).hud.join("\n");
  assert.doesNotMatch(hud, /첫 증강 추천/, "상점 옆 상자엔 증강 TOP 을 넣지 않는다 (게임을 덜 가리게)");
  assert.match(hud, /산 아이템: 몰락한 왕의 검 (✅|👍)/);
  p.w.applyLive({ enabled: true, inGame: true, champion: "Irelia", level: 7, items: ["3153", "3089"] });  // 라바돈
  p.w.sendOverlay();
  assert.match(sent.at(-1).hud.join("\n"), /라바돈의 죽음모자 ⚠️/, "물리 챔피언의 AP 아이템은 경고");
  assert.match(p.text("#chosen-items"), /라바돈의 죽음모자 물리 챔피언에겐 반대 유형이에요/);
  assert.deepEqual(p.errors, []);
});

test("고른 증강을 못 정해도 게임 화면 카드 표시는 지운다", async () => {
  const p = await loadPage({ simple: true });
  const sent = [];
  p.w.pywebview = { api: { set_overlay: (x) => sent.push(x) } };
  p.w.applyLive({ enabled: true, inGame: true, champion: "DrMundo", level: 3, items: [] });
  const offers = ["포로 쇄도", "강타자", "지옥불 난사 발동"];
  p.w.applyAug({ offers, seq: 1, pickSeq: 0 });
  assert.equal(sent.at(-1).cards.filter(Boolean).length, 3);
  p.w.applyAug({ offers: null, seq: 1, picked: null, pickedFrom: offers, pickSeq: 1 });
  assert.equal(sent.at(-1).cards.filter(Boolean).length, 0, "선택 화면이 닫혔으니 게임 화면 카드는 지움");
  assert.match(p.text("#offer-result"), /이걸로 선택/, "애드온엔 직접 고를 수 있게 남김");
  p.w.applyLive({ enabled: true, inGame: true, champion: "DrMundo", level: 4, items: [] });  // 다시 그려도
  assert.equal(sent.at(-1).cards.filter(Boolean).length, 0);
});

test("적 구성: 받는 피해 비율·회복·탱커 조언이 뜨고 계산에 반영된다", async () => {
  const p = await loadPage({ simple: true });
  const sent = [];
  p.w.pywebview = { api: { set_overlay: (x) => sent.push(x) } };
  const base = { enabled: true, inGame: true, champion: "Darius", level: 9, items: [] };
  p.w.applyLive({ ...base, enemies: [] });
  assert.equal(p.w.enemyInfo(), null, "적을 모르면 반반(기본값)");
  const before = p.text("#next-buy");
  // 마법 위주 + 회복 많은 + 탱커 많은 적
  p.w.applyLive({ ...base, enemies: ["Lux", "Vladimir", "Soraka", "Malphite", "Sejuani"] });
  assert.ok(p.w.enemyInfo().magic >= 0.6);
  const adv = p.text("#next-buy");
  assert.match(adv, /적 구성 추정: 마법 \d+%/);
  assert.match(adv, /적 탱커 많음/);
  assert.notEqual(adv, before);
  p.w.sendOverlay();
  assert.match(sent.at(-1).hud.join("\n"), /적 구성 추정: 마법/);
  assert.deepEqual(p.errors, []);
});

test("증강 칸 아이콘으로 읽은 고른 증강을 더한다 (클릭으로 잡은 것과 합침)", async () => {
  const p = await loadPage({ simple: true });
  p.w.applyLive({ enabled: true, inGame: true, champion: "Naafiri", level: 9, items: [] });
  p.w.applyAug({ offers: null, seq: 1, picked: "골드 강탈", pickedFrom: ["골드 강탈", "신성한 눈덩이", "거인"], pickSeq: 1 });
  assert.match(p.text("#chosen"), /골드 강탈/);
  const giant = p.data.augments.find((a) => a.name === "거인");
  p.w.applyAug({ offers: null, seq: 1, picked: "골드 강탈", pickSeq: 1, hudPicked: [giant.id] });
  assert.match(p.text("#chosen"), /거인/, "증강 칸에서 찾은 것을 더한다");
  assert.match(p.text("#chosen"), /골드 강탈/, "클릭으로 잡은 것은 지우지 않는다");
});

test("고른 증강이 4개인데 선택 화면이 또 뜨면 증강 칸에서 확인 못 한 것을 빼고 카드를 띄운다", async () => {
  // 2026-10-15 케일 판: 틀린 클릭 짐작(극악무도)이 남아 4개로 차서 마지막 선택 화면 카드가 안 떴다
  const p = await loadPage({ simple: true });
  const sent = [];
  p.w.pywebview = { api: { set_overlay: (x) => sent.push(x) } };
  p.w.applyLive({ enabled: true, inGame: true, champion: "Kayle", level: 15, items: [] });
  const id = (n) => p.data.augments.find((a) => a.name === n).id;
  p.w.applyAug({ offers: null, seq: 1, picked: "극악무도", pickSeq: 1 });
  const real = ["광휘의 검 업그레이드", "시작부터 끝까지", "미친 과학자"];
  p.w.applyAug({ offers: null, seq: 1, pickSeq: 1, hudPicked: real.map(id) });
  assert.match(p.text("#chosen"), /극악무도/);
  p.w.applyAug({ offers: ["속전속결", "민병대", "조준경 부착"], seq: 2, pickSeq: 1, hudPicked: real.map(id) });
  assert.doesNotMatch(p.text("#chosen"), /극악무도/, "확인 못 한 짐작만 뺀다");
  for (const n of real) assert.match(p.text("#chosen"), new RegExp(n));
  assert.equal(sent.at(-1).cards.filter(Boolean).length, 3, "게임 화면에 카드 3장");
  assert.deepEqual(p.errors, []);
});

test("아이템이 있어야 하는 증강(강철 같은 심장)을 고르면 그 아이템이 '다음 구매' 맨 앞", async () => {
  const p = await loadPage({ simple: true });
  const sent = [];
  p.w.pywebview = { api: { set_overlay: (x) => sent.push(x) } };
  p.w.applyLive({ enabled: true, inGame: true, champion: "Jinx", level: 9, items: [] });
  p.w.applyAug({ offers: null, seq: 1, picked: "강철 같은 심장", pickSeq: 1 });
  assert.match(sent.at(-1).hud.find((x) => x.startsWith("다음 구매")), /^다음 구매: 강철심장/);
  assert.match(p.text("#next-buy"), /증강에 필요/);
  // 이미 샀으면 다시 권하지 않는다
  p.w.applyLive({ enabled: true, inGame: true, champion: "Jinx", level: 9, items: ["3084"] });
  assert.doesNotMatch(p.text("#next-buy"), /증강에 필요/);
  assert.deepEqual(p.errors, []);
});

test("적 대응: 적이 산 아이템 때문에 순위가 크게 오른 아이템·증강을 표시한다 (케일 판 실제 적 아이템)", async () => {
  const p = await loadPage({ simple: true });
  const sent = [];
  p.w.pywebview = { api: { set_overlay: (x) => sent.push(x) } };
  const enemyBuilds = [{ champion: "Velkoz", level: 16, items: ["6655", "3135", "3020", "4645", "4646"] },
    { champion: "Rammus", level: 15, items: ["3075", "3047", "3084", "3068"] },
    { champion: "Rell", level: 16, items: ["3121", "3143", "3050", "3047"] },
    { champion: "TahmKench", level: 16, items: ["3084", "3111", "3083", "3075", "3067"] },
    { champion: "Kalista", level: 16, items: ["3006", "3153", "3302", "6672"] }];
  const live = (x) => p.w.applyLive({ enabled: true, inGame: true, champion: "Kayle", level: 11, items: ["3100", "3115", "3006"],
    enemies: enemyBuilds.map((e) => e.champion), ...x });
  live({ enemyBuilds: enemyBuilds.map((e) => ({ ...e, items: [] })) });
  assert.doesNotMatch(p.text("#next-buy"), /적 대응/, "적이 아무것도 안 샀으면 표시 안 함");
  live({ enemyBuilds });
  assert.match(p.text("#next-buy"), /🛡️ 적 대응/);
  const box = sent.at(-1).hud.join("\n");
  assert.match(box, /적 (가시 갑옷|강철심장) 2·(가시 갑옷|강철심장) 2 \(체력·방어력 높음\) → 적 대응: 리안드리의 고통/, "상자에 무엇 때문에 무엇을 사라는지");
  assert.match(box, /다음 구매: .*\(적 대응\)/);
  // 증강 선택: 고정 피해 증강(마법 미사일)은 단단한 적에게 더 좋다
  p.w.applyAug({ offers: ["마법 미사일", "강타자", "마법 명사수"], seq: 1, pickSeq: 0 });
  const cards = sent.at(-1).cards;
  assert.match(cards[0].reason, /적 대응/);
  assert.doesNotMatch(cards[2].reason, /적 대응/);
  assert.deepEqual(p.errors, []);
});

test("게임 화면 상자: 상점·죽음·시작 직후에만 띄우라고 보낸다", async () => {
  const p = await loadPage({ simple: true });
  const sent = [];
  p.w.pywebview = { api: { set_overlay: (x) => sent.push(x) } };
  const live = (x) => p.w.applyLive({ enabled: true, inGame: true, champion: "Kayle", level: 9, items: [], gold: 100, ...x });
  live({ gameTime: 30 });
  assert.equal(sent.at(-1).box, "start");
  live({ gameTime: 300, gold: 120 });
  assert.equal(sent.at(-1).box, null);
  live({ gameTime: 310, dead: true, gold: 130 });
  assert.equal(sent.at(-1).box, "dead");
  live({ gameTime: 320, dead: false, gold: 140, aug: { shop: { left: 0.2 } } });
  assert.equal(sent.at(-1).box, "shop");
  assert.deepEqual(p.errors, []);
});

test("증강 칸 결과와 고른 증강이 한 번에 오면 화면은 한 번만 다시 그린다", async () => {
  const p = await loadPage({ simple: true });
  p.w.applyLive({ enabled: true, inGame: true, champion: "Naafiri", level: 9, items: [] });
  const giant = p.data.augments.find((a) => a.name === "거인");
  const orig = p.w.renderAll;
  let n = 0;
  p.w.renderAll = () => { n++; orig(); };
  p.w.applyAug({ offers: null, seq: 1, picked: "골드 강탈", pickSeq: 1, hudPicked: [giant.id] });
  p.w.renderAll = orig;
  assert.equal(n, 1);
  assert.match(p.text("#chosen"), /거인/);
  assert.match(p.text("#chosen"), /골드 강탈/);
  // 고른 것을 못 정했을 때 안내 문구는 다시 그린 뒤에도 남는다
  p.w.applyAug({ offers: null, seq: 1, picked: null, pickedFrom: ["신성한 눈덩이", "부식", "흡수"], pickSeq: 2 });
  assert.match(p.text("#offer-result"), /확인하지 못했어요/);
  assert.deepEqual(p.errors, []);
});

test("증강 쿨타임: 자동 주기는 고른 시각부터, 체력 발동은 체력이 기준 아래로 떨어질 때부터 (모두 예상)", async () => {
  const p = await loadPage({ simple: true });
  const sent = [];
  p.w.pywebview = { api: { set_overlay: (x) => sent.push(x) } };
  const live = (hp, extra = {}) => p.w.applyLive({ enabled: true, inGame: true, champion: "Lulu", level: 9, items: [],
    stats: { currentHealth: hp, maxHealth: 1000 }, ...extra });
  live(1000);
  p.w.applyAug({ offers: null, seq: 1, picked: "퀸카", pickSeq: 1 });
  p.w.applyAug({ offers: null, seq: 1, picked: "도주 계획", pickSeq: 2 });
  p.w.applyAug({ offers: null, seq: 1, picked: "빛의 인도자 결의", pickSeq: 3 });
  p.w.applyAug({ offers: null, seq: 1, picked: "강타자", pickSeq: 4 });
  live(900);
  let t = sent.at(-1).timers;
  assert.equal(JSON.stringify(t.map((x) => x.name).sort()), JSON.stringify(["도주 계획", "빛의 인도자 결의", "퀸카"]), "쿨타임 있는 증강만");
  assert.equal(t.find((x) => x.name === "퀸카").period, 35);
  assert.equal(t.find((x) => x.name === "도주 계획").readyAt, 0, "아직 발동 안 함 = 준비");
  live(300);  // 30%: 도주 계획(35%)·빛의 인도자(50%) 발동
  t = sent.at(-1).timers;
  const now = Date.now() / 1000;
  assert.ok(Math.abs(t.find((x) => x.name === "도주 계획").readyAt - (now + 75)) < 2);
  assert.ok(Math.abs(t.find((x) => x.name === "빛의 인도자 결의").readyAt - (now + 45)) < 2);
  live(0, { dead: true });  // 사망: 빛의 인도자만 초기화
  t = sent.at(-1).timers;
  assert.equal(t.find((x) => x.name === "빛의 인도자 결의").readyAt, 0);
  assert.ok(t.find((x) => x.name === "도주 계획").readyAt > now);
  const n = sent.length;
  live(0, { dead: true });
  assert.equal(sent.length, n, "바뀐 게 없으면 다시 보내지 않는다");
  assert.deepEqual(p.errors, []);
});

test("상대 아이템 반영: 적이 산 아이템으로 상대 능력치·관통·치유 감소, 기록, 게임 끝나면 원래대로", async () => {
  const p = await loadPage({ simple: true });
  const sent = [];
  p.w.pywebview = { api: { set_overlay: (x) => sent.push(x) } };
  const id = (n) => p.data.items.find((i) => i.name === n).id;
  const foes = ["Malphite", "Leona", "Soraka", "Vayne", "Lux"];
  const live = (builds, lv = 12) => p.w.applyLive({ enabled: true, inGame: true, champion: "Lux", level: 12, items: [], enemies: foes,
    enemyBuilds: foes.map((c, k) => ({ champion: c, level: lv, items: builds[k] || [] })) });
  const T = () => ({ ...p.w.Stats.TARGET });
  live([]);
  const t0 = T();
  assert.match(p.text("#target-info"), /아이템·증강 비교: 이번 판 적 5명 18레벨 예상/);
  assert.match(p.text("#target-info"), /지금 적 \(평균 12레벨, 산 아이템만\)/);
  assert.ok(Math.abs(t0.MR - p.w.Stats.DEFAULT_TARGET.MR) < 15, "아직 안 산 칸은 평균 빌드: 기준 상대와 비슷");
  live([[id("가시 갑옷"), id("태양불꽃 방패")], [id("대자연의 힘"), id("정령의 형상"), id("케이닉 루컨")], [], [id("몰락한 왕의 검")], [id("케이닉 루컨")]]);
  const t1 = T();
  assert.ok(t1.MR > t0.MR + 30 && t1.ARMOR > t0.ARMOR, `방어 아이템을 사면 상대가 단단해진다 ${JSON.stringify([t0, t1])}`);
  p.w.sendOverlay();
  const e = sent.at(-1).enemy;
  assert.ok(e && e.auto && e.target.MR === t1.MR, "기록용으로 상대 능력치를 보낸다");
  assert.ok(e.builds.find((b) => b.champ === "Leona").items.includes("케이닉 루컨"));
  // 지금 상대(실제 레벨·산 아이템)는 18레벨 예상보다 약하다 (아직 안 산 칸·레벨 차이)
  assert.ok(e.now && e.now.level === 12 && e.now.MR < e.target.MR && e.now.HP < e.target.HP, JSON.stringify(e.now));
  assert.equal(e.resistItems.MR, 4, "실제로 산 마법 저항 아이템 수");
  const advice = e.advice.join("\n");
  assert.match(advice, /적이 마법 저항 아이템 4개 \(지금 평균 마법 저항력 \d+\) → 관통/);
  assert.match(advice, /회복 많음 \([^)]*베인/, "흡혈 아이템을 산 적은 회복 많음");
  // 레벨만 오르면 예상 상대는 그대로, 지금 상대만 바뀐다
  const tBefore = T();
  live([[id("가시 갑옷"), id("태양불꽃 방패")], [id("대자연의 힘"), id("정령의 형상"), id("케이닉 루컨")], [], [id("몰락한 왕의 검")], [id("케이닉 루컨")]], 16);
  assert.deepEqual(T(), tBefore);
  assert.match(p.text("#target-info"), /지금 적 \(평균 16레벨/);
  // 방어 아이템이 2개뿐이면 관통 조언은 탱커 기준만 (예상 빌드로 채운 값으로는 조언하지 않는다)
  live([[], [id("대자연의 힘")], [], [], [id("케이닉 루컨")]]);
  p.w.sendOverlay();
  assert.doesNotMatch(sent.at(-1).enemy.advice.join("\n"), /마법 저항 아이템/);
  // 상대를 직접 고르면 그대로 둔다
  const sel = p.q("#target-champ");
  sel.value = "Garen"; sel.dispatchEvent(new p.w.Event("change", { bubbles: true }));
  const tg = T();
  live([[id("가시 갑옷")], [], [], [], []]);
  assert.deepEqual(T(), tg, "직접 고른 상대는 적 아이템으로 바꾸지 않는다");
  sel.value = ""; sel.dispatchEvent(new p.w.Event("change", { bubbles: true }));
  // 게임이 끝나면 기준 상대로
  p.w.applyLive({ enabled: true, inGame: false });
  assert.deepEqual(T(), { ...p.w.Stats.DEFAULT_TARGET, CUR: T().CUR, DPS_TAKEN: T().DPS_TAKEN });
  assert.match(p.text("#target-info"), /기준 상대/);
  assert.deepEqual(p.errors, []);
});

test("윈도우 프로그램: 챔피언 검색·목록을 숨기고, 게임에서 고른 챔피언은 가운데 칸에", async () => {
  const p = await loadPage({ simple: true });
  p.w.document.body.classList.add("winapp");
  // 롤 클라이언트 연결이 안 되면(자동 감지 실패) 직접 고르게 목록을 보인다
  p.w.renderChamps();
  assert.ok(p.qa("#champ-grid .champ").length > 100, "연결 전엔 직접 고를 수 있다");
  await p.w.applyLcu({ write_item_set: async () => ({ ok: true }) }, { connected: true, phase: "Lobby" });
  assert.equal(p.qa("#champ-grid .champ").length, 0, "연결되면 목록을 만들지 않는다");
  p.w.applyLive({ enabled: true, inGame: true, champion: "Jinx", level: 6, items: [] });
  assert.match(p.text("#champ-card"), /징크스/);
  await p.w.applyLcu({}, { connected: false });
  assert.equal(p.qa("#champ-grid .champ").length, 0, "게임 중이면 연결이 끊겨도 게임이 알려 준다");
  p.w.applyLive({ enabled: true, inGame: false });
  assert.ok(p.qa("#champ-grid .champ").length > 100, "연결도 게임도 없으면 다시 직접");
  assert.deepEqual(p.errors, []);
});

test("아이템 칸이 다 찼으면 바꾸기(판다 → 산다)를 아이콘으로 보낸다 (베인 판 실제 아이템)", async () => {
  const p = await loadPage({ simple: true });
  const sent = [];
  p.w.pywebview = { api: { set_overlay: (x) => sent.push(x) } };
  const full = (items) => p.w.applyLive({ enabled: true, inGame: true, champion: "Vayne", level: 18, items, dead: true });
  full(["6672", "3006", "3032", "3036", "3153"]);
  assert.equal(sent.at(-1).swap, null, "칸이 남으면 바꾸기 없음");
  // 마법 챔피언 아이템(라바돈의 죽음모자)을 하나 낀 6칸 → 그걸 팔라고
  full(["6672", "3006", "3032", "3036", "3153", "3089"]);
  const sw = sent.at(-1).swap;
  assert.ok(sw, "바꾸기 보냄");
  assert.equal(sw.sell, "3089");
  assert.notEqual(sw.buy, "3089");
  assert.match(p.text("#next-buy"), /바꾸면 더 좋아요/);
  // 판매가(게임 데이터 gold.sell)를 뺀 실제 골드와, 지금 골드로 되는지
  const rab = p.data.items.find((i) => i.id === "3089");
  const buy = p.data.items.find((i) => i.id === sw.buy);
  assert.ok(Number(rab.sell) > 0, "아이템 데이터에 판매가가 있다");
  assert.equal(sw.cost, Math.max(0, Number(buy.gold) - Number(rab.sell)));
  assert.match(p.text("#next-buy"), /팔면 [\d,]+G 돌려받아 실제로 [\d,]+G/);
  p.w.applyLive({ enabled: true, inGame: true, champion: "Vayne", level: 18, items: ["6672", "3006", "3032", "3036", "3153", "3089"], dead: true, gold: 99999 });
  p.w.renderNextBuy && p.w.sendOverlay();
  assert.equal(sent.at(-1).swap.ready, true, "골드가 충분하면 지금 바꿀 수 있음");
  assert.ok(!sent.at(-1).hud.some((x) => /라바돈/.test(x) && /팔/.test(x)), "상자 글은 늘리지 않는다 (아이콘으로만)");
  assert.deepEqual(p.errors, []);
  // 판매가가 데이터에 없으면 짐작하지 않는다: 실제 비용 줄 없이 바꾸기만 안내
  const p2 = await loadPage({ simple: true, edit: (x) => x.items.forEach((i) => delete i.sell) });
  const sent2 = [];
  p2.w.pywebview = { api: { set_overlay: (x) => sent2.push(x) } };
  p2.w.applyLive({ enabled: true, inGame: true, champion: "Vayne", level: 18, items: ["6672", "3006", "3032", "3036", "3153", "3089"], dead: true, gold: 99999 });
  assert.equal(sent2.at(-1).swap.cost, null);
  assert.equal(sent2.at(-1).swap.ready, false);
  assert.doesNotMatch(p2.text("#next-buy"), /돌려받아/);
  assert.match(p2.text("#next-buy"), /바꾸면 더 좋아요/);
  assert.deepEqual(p2.errors, []);
});

test("적별 위협·보호막·공격 속도·확인된 적 증강 (산 아이템·소환사 주문 칸만 근거)", async () => {
  const p = await loadPage({ simple: true });
  const sent = [];
  p.w.pywebview = { api: { set_overlay: (x) => sent.push(x) } };
  const id = (n) => p.data.items.find((i) => i.name === n).id;
  const foes = ["Mordekaiser", "Xayah", "Leona", "Vayne", "Lux"];
  const builds = [[id("스테락의 도전"), id("가시 갑옷"), id("태양불꽃 방패")], [id("불멸의 철갑궁"), id("크라켄 학살자"), id("구인수의 격노검")],
    [id("케이닉 루컨")], [id("몰락한 왕의 검")], []];
  p.w.applyLive({ enabled: true, inGame: true, champion: "Garen", level: 12, items: [], enemies: foes,
    enemyBuilds: foes.map((c, k) => ({ champion: c, level: 12, items: builds[k], augments: k === 0 ? ["GrowthSpurt"] : k === 1 ? ["ARAM_FlashySpell"] : [] })) });
  p.w.sendOverlay();
  const e = sent.at(-1).enemy;
  const advice = e.advice.join("\n");
  assert.match(advice, /적별 \(산 아이템\): .*모데카이저 탱커 3/);
  assert.match(advice, /자야 보호막·공속 2/);
  assert.match(advice, /적 보호막 많음 \(보호막 아이템 3개\) → 보호막 감소: 독사의 송곳니/, advice);
  assert.match(advice, /적 공격 속도 높음 \(자야\) → 검토: /, "가렌은 앞에서 맞는다");
  assert.match(advice, /적 증강 \(확인됨\): 모데카이저 급속 성장 · 자야 점멸 난사/);
  assert.deepEqual(e.builds.find((b) => b.champ === "Mordekaiser").augments, ["급속 성장"], "기록에도 남긴다");
  const hud = sent.at(-1).hud.join("\n");
  assert.doesNotMatch(hud, /적별|적 증강/, "게임 화면 상자는 할 일만 (짧게)");
  assert.match(hud, /보호막 감소/);
  assert.deepEqual(p.errors, []);
});

test("추천 다시 계산 시간을 게임 화면 표시와 같이 보낸다 (기록용)", async () => {
  const p = await loadPage({ simple: true });
  const sent = [];
  p.w.pywebview = { api: { set_overlay: (x) => sent.push(x) } };
  p.w.applyLive({ enabled: true, inGame: true, champion: "Vayne", level: 5, items: ["3006"] });
  p.w.applyLive({ enabled: true, inGame: true, champion: "Vayne", level: 5, items: ["3006", "6672"] });
  const last = sent.at(-1);
  assert.equal(typeof last.calcMs, "number");
  assert.ok(last.calcSeq >= 1);
  assert.deepEqual(p.errors, []);
});

test("고른 증강 출처: 증강 칸에서 본 것은 확인됨, 클릭으로만 짐작한 것은 화면 추정", async () => {
  const p = await loadPage({ simple: true });
  p.w.applyLive({ enabled: true, inGame: true, champion: "Vayne", level: 5, items: [] });
  const id = (n) => p.data.augments.find((a) => a.name === n).id;
  p.w.applyAug({ pickSeq: 1, picked: "치명적 리듬", pickedFrom: ["치명적 리듬", "취약", "부메랑 투척"], hudPicked: [] });
  assert.match(p.text("#chosen"), /치명적 리듬화면 추정/);
  p.w.applyAug({ pickSeq: 1, hudPicked: [id("치명적 리듬")] });
  assert.match(p.text("#chosen"), /치명적 리듬✔ 확인됨/);
  assert.deepEqual(p.errors, []);
});

test("게임 중 능력치 표에 게임 실제 값을 옆에 보여 주고, 5% 넘게 다르면 ⚠️ · 기록용으로 보낸다", async () => {
  const p = await loadPage({ simple: false });
  const sent = [];
  p.w.pywebview = { api: { set_overlay: (x) => sent.push(x) } };
  const raw = require("./data/live_missfortune_4s.json").activePlayer.championStats;
  const stats = { ...raw, attackDamage: 80 };  // 공격력만 일부러 크게 다르게
  p.w.applyLive({ enabled: true, inGame: true, champion: "MissFortune", level: 3, items: [], dead: true, stats });
  const table = p.text("#stat-table");
  assert.match(table, /게임 실제/);
  assert.match(table, /공격력.*⚠️ 80/);
  assert.doesNotMatch(table, /이동 속도[^가-힣]*⚠️/, "이동 속도는 같다 (325)");
  p.w.sendOverlay();
  const sc = sent.at(-1).statCheck;
  assert.equal(sc.level, 3);
  assert.equal(JSON.stringify(sc.rows.MS), "[325,325]");
  assert.equal(sc.rows.AD[1], 80);
  // 게임 밖(연동 꺼짐)에선 실제 칸이 없다
  p.w.applyLive({ enabled: true, inGame: false });
  assert.deepEqual(p.errors, []);
});
