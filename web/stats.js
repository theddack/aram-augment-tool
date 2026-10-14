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
    const bonusAS = base.asLevelBonus + add.AS;
    t.AS = Math.min(AS_CAP, (base.AS + bonusAS * champ.asRatio) * (1 + mult.ASmult) * (aram.total_as || 1));
    t.CRIT = Math.min(1, add.CRIT);
    t.CRITDMG = BASE_CRIT_DMG + add.CRITDMG;
    t.MS = (base.MS + add.MS + (conv.HASTEtoMS ? t.HASTE * conv.HASTEtoMS : 0)) * (1 + add.MSP) * (1 + mult.MSmult);
    for (const k of ["LETHALITY", "MPEN", "ARPEN", "MPENP", "LS", "OMNIVAMP", "HSP"]) t[k] = add[k];
    t.RANGE = base.RANGE;
    return { total: t, base, flags, aram, effects };
  }

  // 레벨 L 에서의 스킬 랭크 (궁극기 6/11/16, 기본 스킬은 남는 포인트를 Q→W→E 순으로 균등 배분)
  function ranksAt(L, spells) {
    const r = L >= 16 ? 3 : L >= 11 ? 2 : L >= 6 ? 1 : 0;
    let pts = L - r;
    const out = [0, 0, 0];
    for (let i = 0; pts > 0 && i < 30; i++) {
      const slot = i % 3;
      if (out[slot] < (spells[slot] ? spells[slot].ranks : 5)) { out[slot]++; pts--; }
    }
    return [...out, Math.min(r, spells[3] ? spells[3].ranks : 3)];
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
    const alt = /(min|floor|base)$/i;
    const hasMax = vals.some((v) => /max/i.test(v.name));
    return vals.filter((v) => !(hasMax && alt.test(v.name)));
  }

  function mitigation(resist, pctPen, flatPen) {
    const eff = Math.max(0, resist * (1 - pctPen) - flatPen);
    return 100 / (100 + eff);
  }

  // 기준 상대: 칼바람 18레벨 평균 정도의 방어력·마법 저항력·체력 (비교용 고정값)
  // 현재 체력 비례 피해는 체력 70% 상태, 잃은 체력 비례는 30% 잃은 상태로 가정
  const TARGET = { ARMOR: 80, MR: 60, HP: 3000, CUR: 0.7 };
  const COMBO_WINDOW = 4; // 콤보 한 번(스킬 4개)에 걸리는 시간 가정(초): 쿨다운 있는 효과의 발동 횟수 상한

  function metrics(champ, L, stats, opts = {}) {
    const { total: t, flags, aram } = stats;
    const dealt = (aram.dmg_dealt || 1) * (1 + flags.dmgDealtMult);
    const taken = aram.dmg_taken || 1;
    const physMit = mitigation(TARGET.ARMOR, t.ARPEN, t.LETHALITY);
    const magMit = mitigation(TARGET.MR, t.MPENP, t.MPEN);
    const ranks = ranksAt(L, champ.spells);
    const out = { spells: [], combo: 0, comboRaw: 0, skillDps: 0, shield: 0, heal: 0 };
    champ.spells.forEach((sp, i) => {
      const rank = ranks[i];
      const disabled = rank === 0 || (flags.noUlt && sp.slot === "R");
      const haste = t.HASTE + (flags.slotHaste[sp.slot] || 0) + (sp.slot === "R" ? flags.ultHaste * 1.5 : 0);
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
    const castRate = out.spells.reduce((a, s) => a + (!s.disabled && s.cd > 0 ? 1 / Math.max(s.cd, 0.5) : 0), 0);
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
      const comboTimes = (n) => {
        let k = e.cooldown ? Math.min(n, 1 + Math.floor(COMBO_WINDOW / e.cooldown)) : n;
        return e.needsAuto ? Math.min(k, autos) : k;
      };
      const rate = (r) => {
        let k = e.cooldown ? Math.min(r, 1 / e.cooldown) : r;
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
      const hit = v * mit * dealt * (1 + flags.trueBonus);
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
    out.ehpPhys = t.HP * (1 + t.ARMOR / 100) / taken;
    out.ehpMag = t.HP * (1 + t.MR / 100) / taken;
    out.ehp = (out.ehpPhys + out.ehpMag) / 2;
    out.dps = out.autoDps + 0.3 * out.skillDps + out.augDps;  // 스킬을 쿨마다 계속 쓰는 가정은 과대평가라 30%만
    out.support = out.shield + out.heal;
    return out;
  }

  function evaluate(champ, L, items, augStats, opts) {
    const stats = computeStats(champ, L, items, augStats, opts);
    return { stats, m: metrics(champ, L, stats, opts) };
  }

  const api = { ROWS, TARGET, baseStats, computeStats, metrics, evaluate, ranksAt };
  if (typeof module !== "undefined") module.exports = api;
  else root.Stats = api;
})(this);
