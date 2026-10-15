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
