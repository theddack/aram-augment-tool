// 능력치 계산 엔진: 챔피언 레벨별 기본 능력치 + 아이템 + 증강 → 최종 능력치 → 스킬 피해·평타 DPS·유효 체력.
// 화면(app.js)과 점수(scoring.js)가 같이 쓴다. node 로도 검증할 수 있게 UMD 형태.
(function (root) {
  const AS_CAP = 2.5;
  const BASE_CRIT_DMG = 1.75;
  const DMG_KINDS = ["magic", "physical", "true"];

  // 표시용 능력치 목록: [키, 이름, 소수점 자리, 비율(%) 여부]
  const ROWS = [
    ["HP", "체력", 0], ["AD", "공격력", 0], ["AP", "주문력", 0], ["AS", "공격 속도", 2],
    ["CRIT", "치명타 확률", 0, true], ["ARMOR", "방어력", 0], ["MR", "마법 저항력", 0],
    ["HASTE", "스킬 가속", 0], ["MS", "이동 속도", 0], ["MANA", "마나", 0],
    ["LETHALITY", "물리 관통력", 0], ["ARPEN", "방어구 관통력", 0, true],
    ["MPEN", "마법 관통력", 0], ["MPENP", "마법 관통력 %", 0, true],
    ["LS", "생명력 흡수", 0, true], ["OMNIVAMP", "모든 피해 흡혈", 0, true],
    ["HSP", "회복·보호막 효과", 0, true],
  ];

  // 레벨 성장: base + g × (L-1) × (0.7025 + 0.0175 × (L-1))
  const growth = (g, L) => g * (L - 1) * (0.7025 + 0.0175 * (L - 1));

  function baseStats(champ, L) {
    const s = champ.stats;
    return {
      HP: s.hp + growth(s.hpperlevel, L),
      MANA: s.mp + growth(s.mpperlevel, L),
      AD: s.attackdamage + growth(s.attackdamageperlevel, L),
      ARMOR: s.armor + growth(s.armorperlevel, L),
      MR: s.spellblock + growth(s.spellblockperlevel, L),
      MS: s.movespeed,
      AS: s.attackspeed,
      asLevelBonus: growth(s.attackspeedperlevel, L) / 100,
      RANGE: s.attackrange,
    };
  }

  function levelValue(v, L) {
    // 증강 수치가 {base, lvl:[1레벨, 18레벨]} 형태면 레벨 보간
    if (v && typeof v === "object" && "base" in v) {
      const b = v.base[0] || 0;
      return b + (v.lvl ? v.lvl[0] + (v.lvl[1] - v.lvl[0]) * (L - 1) / 17 : 0);
    }
    return v;
  }

  // 아이템·증강 능력치 합산 후 최종 능력치. augStats = 고른 증강들의 statsResolved 목록
  function computeStats(champ, L, items, augStats, opts = {}) {
    const base = baseStats(champ, L);
    const add = { HP: 0, AD: 0, AP: 0, ARMOR: 0, MR: 0, HASTE: 0, MANA: 0, LETHALITY: 0, MPEN: 0,
      MS: 0, AS: 0, CRIT: 0, CRITDMG: 0, MSP: 0, LS: 0, OMNIVAMP: 0, ARPEN: 0, MPENP: 0, HSP: 0 };
    for (const it of items) for (const [k, v] of Object.entries(it.stats || {})) if (k in add) add[k] += v;
    const goldAug = augStats.reduce((a, s) => a + (Number((s || {}).goldValue) || 0), 0);
    if (goldAug) {
      const main = (champ.magicShare ?? 0.5) >= 0.5 ? "AP" : "AD";
      add[main] += goldAug * 0.5 / GOLD_VALUE[main];
      add.HP += goldAug * 0.3 / GOLD_VALUE.HP;
      add.ARMOR += goldAug * 0.1 / GOLD_VALUE.ARMOR;
      add.MR += goldAug * 0.1 / GOLD_VALUE.MR;
    }
    const mult = { HPmult: 0, ADmult: 0, APmult: 0, ASmult: 0, MSmult: 0, adaptiveMult: 0 };
    const flags = { dmgDealtMult: 0, skillDmgMult: 0, trueBonus: 0, ultHaste: 0, slotHaste: {}, noUlt: false };
    const conv = {};
    const mods = [...augStats, ...items.map((it) => it.passiveStats).filter(Boolean)];
    // 효과 출처: 챔피언 패시브, 아이템 고유 효과 (증강 효과는 augStats 의 _effects 로 들어온다)
    const effects = [
      ...((champ.passive && champ.passive.effects) || []).map((e) => ({ ...e, src: "패시브" })),
      ...items.flatMap((it) => (it.effects || []).map((e) => ({ ...e, src: it.name }))),
    ];
    for (const s of mods) {
      for (const [k, raw] of Object.entries(s || {})) {
        if (k === "_effects") { effects.push(...raw); continue; }
        const v = levelValue(raw, L);
        if (k in add) add[k] += v;
        else if (k in mult) mult[k] += v;
        else if (k === "slotHaste") for (const [slot, h] of Object.entries(v)) flags.slotHaste[slot] = (flags.slotHaste[slot] || 0) + h;
        else if (k === "noUlt") flags.noUlt = flags.noUlt || v;
        else if (k in flags) flags[k] += v;
        else conv[k] = (conv[k] || 0) + v;
      }
    }
    const aram = (opts.aram !== false && champ.aram) || {};
    const t = {};
    t.bonusHP = add.HP;
    const itemHP = items.reduce((a, it) => a + ((it.stats && it.stats.HP) || 0), 0);
    t.HP = (base.HP + add.HP + itemHP * (conv.itemHPbonus || 0)) * (1 + mult.HPmult);
    t.bonusHP = t.HP - base.HP;
    let bonusAD = add.AD;
    let AP = add.AP;
    // 적응형 능력치 증가: 스킬이 마법 위주면 주문력, 아니면 추가 공격력
    if (mult.adaptiveMult) {
      if ((champ.magicShare ?? 0.5) >= 0.5) AP *= 1 + mult.adaptiveMult;
      else bonusAD *= 1 + mult.adaptiveMult;
    }
    // 적응형 능력치(고정값): 마법 위주면 주문력, 아니면 추가 공격력 (적응형 1 = 공격력 0.6)
    if (conv.adaptive) {
      if ((champ.magicShare ?? 0.5) >= 0.5) AP += conv.adaptive; else bonusAD += conv.adaptive * 0.6;
    }
    if (conv.bonusADtoAP) { AP += bonusAD * conv.bonusADtoAP; bonusAD = 0; }
    if (conv.APtoBonusAD) { bonusAD += AP * conv.APtoBonusAD; AP = 0; }
    t.AD = (base.AD + bonusAD) * (1 + mult.ADmult);
    t.bonusAD = t.AD - base.AD;
    t.baseAD = base.AD;
    t.AP = AP * (1 + mult.APmult);
    const resMult = 1 + (conv.bonusResistMult || 0);
    t.bonusARMOR = add.ARMOR * resMult;
    t.ARMOR = base.ARMOR + t.bonusARMOR;
    t.bonusMR = add.MR * resMult;
    t.MR = base.MR + t.bonusMR;
    t.MANA = champ.partype === "마나" ? base.MANA + add.MANA : 0;
    t.bonusMANA = champ.partype === "마나" ? add.MANA : 0;
    if (conv.bonusMANAtoAP) t.AP += t.bonusMANA * conv.bonusMANAtoAP * (1 + mult.APmult);
    t.HASTE = add.HASTE + (aram.ability_haste || 0) + (conv.APtoHASTE ? t.AP * conv.APtoHASTE : 0);
    let bonusAS = base.asLevelBonus + add.AS;
    if (conv.AStoHASTE) { t.HASTE += add.AS * 100 * conv.AStoHASTE; bonusAS = base.asLevelBonus; }  // 순수주의자 - 마법사
    t.AS = Math.min(AS_CAP, (base.AS + bonusAS * champ.asRatio) * (1 + mult.ASmult) * (aram.total_as || 1));
    t.CRIT = Math.min(1, add.CRIT);
    t.CRITDMG = BASE_CRIT_DMG + add.CRITDMG;
    // 이동 속도: (기본 + 고정 증가) × (1 + % 증가 합) → 게임의 감속 구간(415·490 초과분 감소)
    const rawMS = (base.MS + add.MS + (conv.HASTEtoMS ? t.HASTE * conv.HASTEtoMS : 0)) * (1 + add.MSP + mult.MSmult);
    t.MS = rawMS > 490 ? rawMS * 0.5 + 230 : rawMS > 415 ? rawMS * 0.8 + 83 : rawMS;
    t.bonusMS = t.MS - base.MS;
    t.bonusAP = t.AP;  // 주문력은 전부 추가 주문력 (기본 주문력이 없다)
    for (const k of ["LETHALITY", "MPEN", "ARPEN", "MPENP", "LS", "OMNIVAMP", "HSP"]) t[k] = add[k];
    t.RANGE = base.RANGE;
    return { total: t, base, flags, aram, effects, conv };
  }

  // 레벨 L 에서의 스킬 랭크. 궁극기: 3단계면 6/11/16, 4단계(니달리·엘리스·카르마)는 1레벨에 1단계를 그냥 받고 6/11/16,
  // 1단계(제이스)는 1레벨부터. 기본 스킬은 남는 포인트를 Q→W→E 순으로 균등 배분. 우디르처럼 R 도 6단계면 4개 모두 기본 스킬로 본다
  function ranksAt(L, spells) {
    const rr = spells[3] ? spells[3].ranks : 3;
    const basic4 = rr >= 5;
    let r = 0, freeR = 0;
    if (!basic4) {
      if (rr === 1) { r = 1; freeR = 1; }
      else if (rr === 4) { r = 1 + (L >= 6) + (L >= 11) + (L >= 16); freeR = 1; }
      else r = L >= 16 ? 3 : L >= 11 ? 2 : L >= 6 ? 1 : 0;
      r = Math.min(r, rr);
    }
    let pts = L - (r - freeR);
    const n = basic4 ? 4 : 3;
    const out = [0, 0, 0, 0];
    for (let i = 0; pts > 0 && i < 40; i++) {
      const slot = i % n;
      if (out[slot] < (spells[slot] ? spells[slot].ranks : 5)) { out[slot]++; pts--; }
    }
    return basic4 ? out : [out[0], out[1], out[2], r];
  }

  function statFor(key, t) {
    if (key === "TGT_MAXHP") return TARGET.HP;
    if (key === "TGT_CURHP") return TARGET.HP * TARGET.CUR;
    if (key === "TGT_MISSINGHP") return TARGET.HP * (1 - TARGET.CUR);
    if (key in t) return t[key];
    if (key.startsWith("base")) return t[key] ?? 0;
    return 0;
  }

  function effectValue(e, rank, L, t) {
    const i = Math.max(0, rank - 1);
    let v = e.base[Math.min(i, e.base.length - 1)] || 0;
    if (e.lvl) v += e.lvl[0] + (e.lvl[1] - e.lvl[0]) * (L - 1) / 17;
    for (const [k, arr] of Object.entries(e.ratios || {})) v += arr[Math.min(i, arr.length - 1)] * statFor(k, t);
    return v;
  }

  // 같은 스킬 안에서 최소/최대 같은 대체 수치는 큰 쪽만, 같은 이름은 한 번만 센다
  function spellTotals(effects, rank, L, t) {
    const seen = new Set();
    const vals = [];
    for (const e of effects) {
      if (seen.has(e.name)) continue;
      seen.add(e.name);
      vals.push({ kind: e.kind, name: e.name, value: effectValue(e, rank, L, t) });
    }
    // 최소/최대(또는 기본/최대) 같은 대체 수치는 같은 이름 줄기끼리 묶어서 큰 쪽 하나만 센다
    // (다리우스 R damage·maximumdamage, 그라가스 Q mindamage·maxdamage …)
    const stem = (n) => String(n).toLowerCase().replace(/^spell\.[^:]+:/, "")
      .replace(/(minimum|maximum|min|max|tooltip|total|floor|base|calc)/g, "");
    const best = new Map();
    for (const v of vals) {
      if (DMG_KINDS.includes(v.kind) && STAT_BUFF.test(String(v.name).replace(/^spell\.[^:]+:/, ""))) continue;  // 능력치 증가 수치(리븐 R bonusad)는 피해가 아니다
      const k = v.kind + "|" + stem(v.name);
      if (!best.has(k) || v.value > best.get(k).value) best.set(k, v);
    }
    return [...best.values()];
  }
  const STAT_BUFF = /^(bonus|flat|percent)?(ad|ap|attackdamage|abilitypower|armor|mr|magicresist|ms|movespeed|movementspeed|as|attackspeed)$/i;

  function mitigation(resist, pctPen, flatPen) {
    const eff = Math.max(0, resist * (1 - pctPen) - flatPen);
    return 100 / (100 + eff);
  }

  // 기준 상대: 전 챔피언 173명의 18레벨 균형 빌드 능력치 중앙값 (패치 16.20, tools/target_median.js 로 계산)
  // 현재 체력 비례 피해는 체력 70% 상태, 잃은 체력 비례는 30% 잃은 상태로 가정
  // 받는 피해량(초당): 18레벨 칼바람 한타에서 집중될 때 평균 정도. 무적·피해 감소를 버티는 힘으로 바꿀 때 쓴다
  const TARGET = { ARMOR: 113, MR: 63, HP: 2470, CUR: 0.7, DPS_TAKEN: 600 };
  // 골드 → 능력치: 위키 '골드 효율' 기준 가치 (공격력 35, 주문력 20, 체력 2.67, 방어력·마법 저항력 20골드)
  // 골드의 50%는 주 공격 능력치, 30% 체력, 20% 방어력·마법 저항력 반반으로 산다고 본다
  const GOLD_VALUE = { AD: 35, AP: 20, HP: 2.67, ARMOR: 20, MR: 20 };
  const DEFAULT_TARGET = { ...TARGET };
  // 기준 상대 바꾸기: {ARMOR, MR, HP} (실제 상대 챔피언의 18레벨 빌드 능력치 등). 인자 없으면 기본값
  function setTarget(t) {
    Object.assign(TARGET, DEFAULT_TARGET, t ? { ARMOR: t.ARMOR, MR: t.MR, HP: t.HP } : {});
  }
  const CAST_MIN_CD = 2; // '스킬 사용 시' 효과: 한 스킬은 최대 2초에 1번 (토글·충전형 스킬이 발동을 부풀리지 않게)
  const COMBO_WINDOW = 4; // 콤보 한 번(스킬 4개)에 걸리는 시간 가정(초): 쿨다운 있는 효과의 발동 횟수 상한

  // 계산 전체에 쓰는 가정값과 이유 (화면 '데이터 출처 → 계산 가정값'에 그대로 나온다)
  // src: data = 게임 데이터·위키에서 계산한 값, assume = 정한 값(실측 아님)
  const ASSUMPTIONS = [
    { what: "기준 상대: 체력 2470 · 방어력 113 · 마법 저항력 63", src: "data",
      why: "전 챔피언 173명이 18레벨에 우리 추천 빌드(균형)를 갖췄을 때의 중앙값이에요 (패치 16.20). 평균은 탱커에 끌려 올라가서 중앙값을 써요. 능력치 패널 '상대'에서 실제 챔피언으로 바꿀 수 있어요." },
    { what: "상대 현재 체력 70% (잃은 체력 30%)", src: "assume", why: "한타가 한창일 때를 기준으로 했어요. 현재·잃은 체력 비례 피해에만 쓰여요." },
    { what: "받는 피해 초당 600", src: "assume", why: "무적·피해 감소 시간을 '버티는 힘'으로 바꿀 때만 써요. 18레벨 한타에서 집중을 받을 때 정도로 잡았어요." },
    { what: "콤보 = Q·W·E·R 각 1번, 4초", src: "assume", why: "스킬 4개를 한 번씩 쓰는 데 걸리는 시간이에요. 재사용 대기시간이 있는 효과는 4초 안에 발동하는 횟수만 세요." },
    { what: "초당 피해에 스킬 피해는 30%만", src: "assume", why: "스킬을 재사용 대기시간마다 계속 맞히는 일은 드물어서, 다 넣으면 너무 커져요." },
    { what: "제어력: 기절·속박은 그대로, 둔화는 30%만", src: "assume", why: "둔화는 움직일 수는 있어서 기절보다 약하게 셌어요. 5초 넘는 지속 효과(장판 등)는 CC로 안 셌어요." },
    { what: "골드 → 능력치: 주 공격 50% · 체력 30% · 방어력·마법 저항력 20%", src: "assume", why: "능력치 1당 골드 가치는 위키 '골드 효율'(공격력 35·주문력 20·체력 2.67·방어 20골드) 그대로이고, 나눠 사는 비율만 정한 값이에요." },
    { what: "평가 기준: 18레벨 · 아이템 6칸 완성", src: "assume", why: "증강·아이템을 같은 조건에서 비교하려고 풀템 시점으로 맞췄어요. 능력치 패널 레벨 슬라이더로 다른 레벨도 볼 수 있어요." },
    { what: "스킬 레벨: 궁극기 6·11·16, 나머지는 Q→W→E 순서로 고르게", src: "assume", why: "챔피언마다 선마가 다르지만 18레벨에는 결국 다 마스터라 차이가 작아요." },
    { what: "충전 공격(스태틱의 단검 등)은 기본 공격 8번에 1번", src: "assume", why: "충전 속도가 이동 거리에 따라 달라서 정확한 숫자가 없어요." },
    { what: "치명타 확률로 발동하는 효과는 치명타 확률만큼 기대값", src: "data", why: "설명의 '치명타 확률과 같은 확률'을 그대로 계산해요." },
    { what: "재사용 대기시간은 가속을 받아도 최소 0.5초로 셈", src: "assume", why: "징크스 Q처럼 전환형 스킬이 초당 피해를 부풀리지 않게 했어요." },
    { what: "'스킬 사용 시' 효과는 한 스킬당 최대 2초에 1번", src: "assume", why: "재사용 대기시간이 2초보다 짧은 스킬은 대부분 무기 바꾸기·충전형(징크스 Q 0.9초, 티모 R 0.25초)이라 그대로 세면 발동이 부풀어요. 카시오페아 E처럼 실제로 자주 쓰는 스킬은 조금 적게 셀 수 있어요." },
    { what: "최소~최대가 있는 스킬 피해는 최대로 셈", src: "assume", why: "충전·거리·중첩에 따라 커지는 스킬(바이 Q, 다리우스 R, 사이온 Q 등)은 최대치를 기준으로 비교해요. 최소와 최대를 둘 다 더하던 오류를 고치면서 정한 기준이에요." },
    { what: "지속 피해(화상 등)는 지속 시간마다 1번", src: "data", why: "같은 대상에 다시 걸면 새로 쌓이지 않고 시간만 갱신되는 게임 규칙 그대로예요 (예: 리안드리 화상 3초)." },
  ];

  function metrics(champ, L, stats, opts = {}) {
    const { total: t, flags, aram } = stats;
    const dealt = (aram.dmg_dealt || 1) * (1 + flags.dmgDealtMult);
    const taken = aram.dmg_taken || 1;
    const physMit = mitigation(TARGET.ARMOR, t.ARPEN, t.LETHALITY);
    const magMit = mitigation(TARGET.MR, t.MPENP, t.MPEN);
    const ranks = ranksAt(L, champ.spells);
    const cv = stats.conv || {};
    const out = { spells: [], combo: 0, comboRaw: 0, skillDps: 0, shield: 0, heal: 0 };
    // ---- 스킬 가속 환산 (쿨 반환·초기화 증강·아이템)
    const ranged = !champ.melee;
    const refund = (h) => {  // 쿨의 f% 를 확률 p 로 돌려받음 → 가속 100 × (1/(1-pf) - 1)
      if (!cv.refundPct) return 0;
      const pc = ranged ? (cv.refundChanceRanged ?? 1) : (cv.refundChanceMelee ?? 1);
      return 100 * (1 / (1 - Math.min(0.9, cv.refundPct * pc)) - 1);
    };
    const rIdx = champ.spells.findIndex((sp) => sp.slot === "R");
    const rSp = champ.spells[rIdx];
    const rRank = ranks[rIdx] || 1;
    const rBaseCd = rSp ? rSp.cooldown[Math.max(0, Math.min(rRank, rSp.cooldown.length) - 1)] || 100 : 100;
    const ultHaste = t.HASTE + flags.ultHaste * 1.5 + (cv.ultHasteAdd || 0)
      + (cv.ultRefundEvery ? 100 * rBaseCd / cv.ultRefundEvery : 0) + (cv.refundAll ? refund() : 0);
    const rCd = rBaseCd * 100 / (100 + ultHaste);
    const ultUptime = (dur) => Math.min(1, (dur || 0) / Math.max(rCd, 1));
    const basicHaste = t.HASTE + (cv.basicHaste || 0) + 100 * t.AS * (cv.onhitCdr || 0)
      + 60 * t.AS * ((cv.onhitRemainingPct || 0) / 0.15) + (cv.critToHaste || 0) * t.CRIT * 100
      + ((champ.features || []).includes("dash") ? (cv.dashHaste || 0) / 3 : 0)
      + (cv.ultTriggerHaste || 0) * ultUptime(cv.ultTriggerDur) + refund();
    out.haste = { basic: basicHaste, ult: ultHaste };
    champ.spells.forEach((sp, i) => {
      const rank = ranks[i];
      const disabled = rank === 0 || (flags.noUlt && sp.slot === "R");
      const haste = (sp.slot === "R" ? ultHaste : basicHaste) + (flags.slotHaste[sp.slot] || 0);
      const cdBase = sp.cooldown[Math.max(0, Math.min(rank, sp.cooldown.length) - 1)] || 0;
      const cd = cdBase * 100 / (100 + haste);
      const vals = disabled ? [] : spellTotals(sp.effects, rank, L, t);
      let dmg = 0, raw = 0, shield = 0, heal = 0;
      for (const v of vals) {
        if (DMG_KINDS.includes(v.kind)) {
          const amp = dealt * (1 + flags.skillDmgMult);
          const mit = v.kind === "magic" ? magMit : v.kind === "physical" ? physMit : 1;
          raw += v.value * amp;
          dmg += v.value * amp * mit * (1 + flags.trueBonus);
        } else if (v.kind === "shield") shield += v.value * (aram.shielding || 1) * (1 + t.HSP);
        else if (v.kind === "heal") heal += v.value * (aram.healing || 1) * (1 + t.HSP);
      }
      out.spells.push({ slot: sp.slot, rank, cd, values: vals, dmg, shield, heal, disabled });
      out.combo += dmg;
      out.comboRaw += raw;
      out.shield += shield;
      out.heal += heal;
      // 원래 재사용 대기시간이 아주 짧은 스킬(징크스 Q 같은 전환형)은 DPS에서 제외, 가속으로 짧아진 건 0.5초까지만
      if (cdBase > 0.5) out.skillDps += dmg / Math.max(cd, 0.5);
    });
    // 증강·아이템 고유 효과·패시브: 발동 빈도에 따라 콤보·DPS·회복/보호막에 더한다
    const casts = out.spells.filter((s) => !s.disabled).length;
    // '스킬 사용 시' 효과의 초당 발동 횟수: 한 스킬은 최대 CAST_MIN_CD 초에 1번으로 센다
    // (재사용 대기시간이 아주 짧은 스킬은 대부분 토글·충전형이라 — 징크스 Q 무기 바꾸기 0.9초, 티모 R 0.25초)
    const castRate = out.spells.reduce((a, s) => a + (!s.disabled && s.cd > 0 ? 1 / Math.max(s.cd, CAST_MIN_CD) : 0), 0);
    out.augCombo = 0;
    out.augDps = 0;
    out.effects = [];
    for (const e of stats.effects || []) {
      let v = effectValue(e, 1, L, t) * (e.ticks || 1);
      if (e.rangedScale != null && !champ.melee) v *= e.rangedScale;
      if (e.chance === "CRIT") v *= t.CRIT;  // 확률 발동은 기대값
      const tr = e.trigger || { type: "once" };
      // 쿨다운이 있으면 콤보 한 번(COMBO_WINDOW초) 안 발동 횟수와 초당 발동 횟수를 제한
      // 기본 공격이 필요한 효과는 콤보 시간 동안 칠 수 있는 기본 공격 수·공격 속도로도 제한
      const autos = Math.max(1, Math.floor(t.AS * COMBO_WINDOW));
      // 지속 피해(화상 등 N초 동안 매초)는 같은 대상에 다시 걸면 시간만 갱신되므로 N초마다 최대 1번
      const refresh = e.ticks && tr.type !== "time" ? e.ticks : 0;
      const comboTimes = (n) => {
        let k = e.cooldown ? Math.min(n, 1 + Math.floor(COMBO_WINDOW / e.cooldown)) : n;
        if (refresh) k = Math.min(k, Math.max(1, Math.ceil(COMBO_WINDOW / refresh)));
        return e.needsAuto ? Math.min(k, autos) : k;
      };
      const rate = (r) => {
        let k = e.cooldown ? Math.min(r, 1 / e.cooldown) : r;
        if (refresh) k = Math.min(k, 1 / refresh);
        return e.needsAuto ? Math.min(k, t.AS) : k;
      };
      const row = { src: e.src || "", kind: e.kind, value: v, trigger: tr.type };
      out.effects.push(row);
      if (e.kind === "shield" || e.kind === "heal") {
        const k = e.kind === "shield" ? (aram.shielding || 1) : (aram.healing || 1);
        const amt = v * k * (1 + t.HSP) * (tr.type === "cast" ? comboTimes(casts) : 1);
        if (e.kind === "shield") out.shield += amt; else out.heal += amt;
        continue;
      }
      const kind = e.kind === "adaptive" ? ((champ.magicShare ?? 0.5) >= 0.5 ? "magic" : "physical") : e.kind;
      row.kind = kind;
      const mit = kind === "magic" ? magMit : kind === "physical" ? physMit : 1;
      const hit = v * mit * dealt * (1 + flags.trueBonus) * (e.src !== "패시브" ? 1 + (cv.effectDmgMult || 0) : 1);
      row.hit = hit;
      if (tr.type === "time") { out.augCombo += hit; out.augDps += hit / Math.max(tr.sec, e.cooldown || 0); }
      else if (tr.type === "attacks") out.augDps += hit * rate(t.AS / tr.n);
      else if (tr.type === "onhit") out.augDps += hit * rate(t.AS);
      else if (tr.type === "cast") { out.augCombo += hit * comboTimes(casts); out.augDps += hit * rate(castRate); }
      else out.augCombo += hit;
    }
    out.combo += out.augCombo;
    const critMul = 1 + t.CRIT * (t.CRITDMG - 1);
    out.autoDps = t.AD * t.AS * critMul * physMit * dealt * (1 + flags.trueBonus);
    // ---- 버티는 힘: 체력 × 저항 + 무적·피해 감소(그동안 받을 피해) + 생명선 회복 + 아이템 피해 감소
    const extraHp = (cv.selfHeal || 0) + (cv.invulnSec || 0) * TARGET.DPS_TAKEN / 2 + (cv.drSec || 0) * TARGET.DPS_TAKEN / 2;
    out.ehpPhys = (t.HP * (1 + t.ARMOR / 100) + extraHp * (1 + t.ARMOR / 100)) * (1 + (cv.ehpPhysMult || 0)) / taken;
    out.ehpMag = ((t.HP + extraHp) * (1 + t.MR / 100) + (cv.ehpMagFlat || 0) * (1 + t.MR / 100)) / taken;
    out.ehp = (out.ehpPhys + out.ehpMag) / 2;
    // ---- 기동성: 평균 이동 속도 (궁극기 후 가속 영역 등 가동률 반영)
    out.mobility = t.MS * (1 + (cv.ultTriggerMs || 0) * ultUptime(cv.ultTriggerDur));
    // ---- 제어력: 콤보 한 번에 거는 CC 시간 (스킬·패시브 + 증강·아이템)
    let control = (champ.passive && champ.passive.cc) || 0;
    for (const sp of out.spells) if (!sp.disabled) control += (champ.spells.find((x) => x.slot === sp.slot) || {}).cc || 0;
    control += (cv.controlSec || 0) + (cv.controlPerCast || 0) * casts;
    if (cv.fearPerCast) for (const sp of out.spells) if (!sp.disabled) control += Math.min(cv.fearPerCast, sp.cd / (cv.fearCdDiv || 20));
    out.control = control;
    out.dps = out.autoDps + 0.3 * out.skillDps + out.augDps;  // 스킬을 쿨마다 계속 쓰는 가정은 과대평가라 30%만
    out.support = out.shield + out.heal;
    return out;
  }

  function evaluate(champ, L, items, augStats, opts) {
    const stats = computeStats(champ, L, items, augStats, opts);
    return { stats, m: metrics(champ, L, stats, opts) };
  }

  const api = { ROWS, TARGET, DEFAULT_TARGET, ASSUMPTIONS, setTarget, baseStats, computeStats, metrics, evaluate, ranksAt };
  if (typeof module !== "undefined") module.exports = api;
  else root.Stats = api;
})(this);
