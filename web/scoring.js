// 점수 공식. 화면(app.js)과 분리해 두어 node 로도 검증할 수 있게 한다.
(function (root) {
  const Stats = typeof module !== "undefined" ? require("./stats.js") : root.Stats;
  const TAG_LABEL = {
    AD: "공격력", AP: "주문력", AS: "공격 속도", CRIT: "치명타", ONHIT: "적중 시 효과",
    LETHAL: "물리 관통", MPEN: "마법 관통", HP: "체력", ARMOR: "방어력", MR: "마법 저항력",
    HEALSHIELD: "회복·보호막", VAMP: "흡혈", HASTE: "스킬 가속", ULT: "궁극기",
    MANA: "마나", MS: "이동 속도", CC: "군중 제어", TRUE: "고정 피해",
  };

  // 역할군별 기본 가중치. 챔피언이 여러 역할이면 큰 값을 쓴다.
  const ROLE_PRESET = {
    Marksman: { AS: 1, CRIT: 0.9, ONHIT: 0.5, LETHAL: 0.4, VAMP: 0.4 },
    Mage: { HASTE: 0.8, MPEN: 0.8, MANA: 0.4, ULT: 0.5 },
    Assassin: { LETHAL: 0.9, HASTE: 0.6, MS: 0.4 },
    Fighter: { HP: 0.6, HASTE: 0.6, VAMP: 0.5, ARMOR: 0.3, MR: 0.3 },
    Tank: { HP: 1, ARMOR: 0.8, MR: 0.8, HASTE: 0.4, CC: 0.6 },
    Support: { HEALSHIELD: 1, HASTE: 0.7, MS: 0.3, CC: 0.3 },
  };
  const BASELINE = { HASTE: 0.3, ULT: 0.3, MS: 0.2, TRUE: 0.3 };
  const TRAIT_BOOST = 0.4; // 스킬 특성 강도가 가중치에 더해지는 비율
  const RARITY_BONUS = { 0: 0, 1: 0.1, 2: 0.2 };
  const UNCLASSIFIED_SCORE = 0.25; // 효과 태그를 못 뽑은 증강의 기본 점수
  const GENERIC_SCORE = 0.6; // 골드·모루·전환 같은 범용 증강
  const SYNERGY = 0.4; // 이미 고른 증강과 태그가 겹칠 때 가산 비율
  const ITEM_SYNERGY = 0.25; // 고른 아이템과 태그가 겹칠 때 가산 비율
  const REQ_BONUS = 0.4; // 증강 조건(돌진 스킬 등)을 챔피언이 충족할 때 가산
  const REQ_PENALTY = 0.3; // 조건을 못 맞추면 곱함
  const RULE_ITEM_SYNERGY = 0.3; // 증강이 원하는 아이템을 이미 골랐을 때 가산
  // 증강 태그 기여를 큰 순서로 감쇠 합산. 설명이 길어 태그가 많은 증강이 무조건 유리해지는 것을 막는다.
  const BREADTH_DECAY = [1, 0.75, 0.55, 0.4, 0.3, 0.2, 0.15, 0.1];

  const SLOTS = 6;
  const ITEM_DECAY = 0.7; // 같은 태그 아이템을 또 고를 때 감쇠
  const MAIN_STAT_DECAY = 0.92; // 공격력·주문력은 계속 쌓는 게 정상이라 감쇠를 약하게
  const MISMATCH = 0.3; // 주 공격 스탯이 반대인 아이템 감점
  const RULE_ITEM_BOOST = 1.5; // 고른 증강이 요구하는 아이템(퀘스트·업그레이드) 가산
  // 아이템 점수 = 수치 증가분 × NUM_K + 태그 점수 × TAG_K.
  // 수치: 최종 능력치로 계산한 스킬 콤보·DPS·유효 체력·회복/보호막의 상대 증가율 (18레벨, 칼바람 보정 포함)
  // 태그: 고유 효과·증강 연계처럼 수치로 안 잡히는 부분
  const NUM_K = 4;
  const TAG_K = 0.4;
  const METRIC_FLOOR = { combo: 300, dps: 60, ehp: 2000, support: 600, mobility: 380, control: 1.5 };

  // 빌드 컨셉: 챔피언 가중치와 증강 태그를 섞는 비율
  const CONCEPTS = [
    { key: "augment", label: "증강 몰빵", desc: "고른 증강 효과를 극대화", champ: 0.35, aug: 1.2, mismatch: true, focus: 1 },
    { key: "balanced", label: "균형", desc: "증강과 챔피언 성향을 절반씩", champ: 1, aug: 0.7, mismatch: true },
    { key: "twist", label: "변칙", desc: "챔피언 성향을 뒤집고 증강에 맞춤 (AP 마스터 이, 탱커 징크스 같은)", champ: 0.5, aug: 1, flip: true, mismatch: false },
  ];

  const round = (v) => Math.round(v * 20) / 20;

  function profile(champ, tags) {
    const w = Object.fromEntries(tags.map((t) => [t, 0]));
    const { attack, magic, defense } = champ.info;
    const infoAd = attack + magic > 0 ? attack / (attack + magic) : 0.5;
    // 스킬의 실제 피해 유형 비율이 있으면 라이엇 성향 점수와 반반 섞는다
    const ad = champ.magicShare == null ? infoAd : 0.5 * infoAd + 0.5 * (1 - champ.magicShare);
    w.AD = ad;
    w.AP = 1 - ad;
    for (const [t, v] of Object.entries(BASELINE)) w[t] = Math.max(w[t], v);
    for (const role of champ.tags) {
      for (const [t, v] of Object.entries(ROLE_PRESET[role] || {})) w[t] = Math.max(w[t], v);
    }
    w.CRIT *= 0.3 + 0.7 * ad;
    w.AS *= 0.3 + 0.7 * ad;
    w.LETHAL *= 0.3 + 0.7 * ad;
    w.MPEN *= 0.3 + 0.7 * (1 - ad);
    w.HP += defense / 20;
    w.ARMOR += defense / 25;
    w.MR += defense / 25;
    for (const [t, v] of Object.entries(champ.traits || {})) if (t in w) w[t] += TRAIT_BOOST * v;
    if (champ.partype !== "마나") w.MANA = 0;
    for (const t of tags) w[t] = Math.min(1.5, round(w[t]));
    return w;
  }

  const ruleOf = (rules, aug) => (rules && rules.augments && rules.augments[aug.id]) || {};

  // 고른 증강이 챔피언 특징을 바꾸는 경우(검을 뽑아라: 원거리 → 근접)를 반영한 챔피언
  function withChosenFeatures(champ, chosen, rules) {
    if (!champ) return champ;
    let f = [...(champ.features || [])];
    let changed = false;
    for (const a of chosen) {
      const sf = ruleOf(rules, a).setsFeatures;
      if (!sf) continue;
      f = f.filter((x) => !(sf.remove || []).includes(x)).concat(sf.add || []);
      changed = true;
    }
    return changed ? { ...champ, features: [...new Set(f)], melee: f.includes("melee") } : champ;
  }
  // 규칙에 tags 가 있으면 자동 분류 대신 그것을 쓴다
  const effTags = (rules, aug) => ruleOf(rules, aug).tags || aug.tags;

  function maxTags(list) {
    const s = {};
    for (const tags of list) for (const [t, v] of Object.entries(tags)) s[t] = Math.max(s[t] || 0, v);
    return s;
  }

  // 아이템 태그 중 0.5 이상만 '그 아이템의 성격'으로 본다
  function strongTags(tags) {
    return Object.fromEntries(Object.entries(tags).filter(([, v]) => v >= 0.5));
  }

  function scoreAugment(aug, w, chosen, chosenItems = [], champ = null, rules = null) {
    const rule = ruleOf(rules, aug);
    const parts = [];
    let score = RARITY_BONUS[aug.rarity] || 0;
    const info = { note: rule.note || "", req: null };
    if (rule.generic) {
      return { score: score + GENERIC_SCORE, parts, generic: true, ...info };
    }
    const tags = effTags(rules, aug);
    const entries = Object.entries(tags);
    if (!entries.length) {
      return { score: score + UNCLASSIFIED_SCORE, parts, unclassified: true, ...info };
    }
    const sel = maxTags(chosen.map((a) => effTags(rules, a)));
    const selItem = maxTags(chosenItems.map((i) => strongTags(i.tags)));
    for (const [t, s] of entries) {
      const base = (w[t] || 0) * s;
      const syn = (sel[t] ? SYNERGY * base : 0) + (selItem[t] ? ITEM_SYNERGY * base : 0);
      if (base + syn > 0) parts.push({ tag: t, value: base + syn, synergy: syn > 0 });
    }
    parts.sort((a, b) => b.value - a.value);
    parts.forEach((p, i) => {
      p.value *= BREADTH_DECAY[Math.min(i, BREADTH_DECAY.length - 1)];
      score += p.value;
    });
    // 조건: 위키에 적용 스킬 목록(eligible)이 있으면 그 챔피언만, 없으면 rules.json 의 스킬 특징
    if (champ && (aug.eligible || rule.requires)) {
      const met = aug.eligible ? aug.eligible.includes(champ.id) : (champ.features || []).includes(rule.requires);
      info.req = { feature: aug.eligible ? "eligible" : rule.requires, met };
      score = met ? score + REQ_BONUS : score * REQ_PENALTY;
    }
    if (rule.items && chosenItems.some((i) => rule.items.includes(i.name))) {
      score += RULE_ITEM_SYNERGY;
      info.itemMatch = true;
    }
    return { score, parts, unclassified: false, ...info };
  }

  function rankAugments(augs, w, chosen, chosenItems = [], champ = null, rules = null) {
    champ = withChosenFeatures(champ, chosen, rules);
    const chosenIds = new Set(chosen.map((a) => a.id));
    return augs
      .filter((a) => !chosenIds.has(a.id))
      .map((a) => ({ aug: a, ...scoreAugment(a, w, chosen, chosenItems, champ, rules) }))
      .sort((a, b) => b.score - a.score);
  }

  // 고른 증강에서 빌드에 필요한 정보(태그·환산·무시 태그·요구 아이템)를 모은다
  function augContext(chosen, rules) {
    const tags = {}, src = {}, convert = {}, zero = new Set(), wanted = {}, must = [], lift = {}, liftSrc = {}, liftBy = {};
    for (const a of chosen) {
      const rule = ruleOf(rules, a);
      const et = effTags(rules, a);
      for (const [t, v] of Object.entries(et)) {
        if (v > (tags[t] || 0)) tags[t] = v;
        (src[t] = src[t] || []).push(a.name);
      }
      // 빌드가 따라갈 태그(lift): 증강이 그 능력치 아이템의 가치를 올릴 때만 (계수·배율이 붙거나, 숫자 없이 효과 종류만 있는 증강).
      // 효과 종류 태그를 그대로 따르면 '부식'(적 방어력 감소)에 방어력 아이템, '사악한 정신'(주문력 +80)에 징크스 주문력 아이템이 들어갔다
      // (2026-10-15 추천 품질 점검). 숫자가 있는 증강의 나머지 효과는 수치 계산이 맡는다
      const scales = scaleTags(a, rule);
      const mine = (liftBy[a.name] = liftBy[a.name] || { _scales: scales, _convert: Object.values(rule.convert || {}) });
      const up = (t, v) => { lift[t] = Math.max(lift[t] || 0, v); (liftSrc[t] = liftSrc[t] || []).push(a.name); mine[t] = Math.max(mine[t] || 0, v); };
      for (const [t, v] of Object.entries(et)) if (scales === null || scales.has(t)) up(t, v);
      for (const to of Object.values(rule.convert || {})) up(to, 1);
      Object.assign(convert, rule.convert || {});
      for (const t of rule.zero || []) zero.add(t);
      for (const n of rule.items || []) (wanted[n] = wanted[n] || []).push(a.name);
      if ((rule.items || []).length) must.push(rule.items);
    }
    return { tags, src, convert, zero, wanted, must, lift, liftSrc, liftBy };
  }
  // 증강이 '키우는' 능력치: 효과 계수(주문력 300% 보호막 → 주문력)·배율(체력 +35% → 체력). 고정 수치(주문력 +80)는 아이템 가치를 안 올린다
  // 숫자가 하나도 없는 증강은 null (효과 종류만 믿는다)
  const RATIO_TAG = { AP: ["AP"], AD: ["AD"], bonusAD: ["AD"], HP: ["HP"], bonusHP: ["HP"], bonusARMOR: ["ARMOR"], bonusMR: ["MR"] };
  const MULT_TAG = { APmult: ["AP"], ADmult: ["AD"], HPmult: ["HP"], ASmult: ["AS"], adaptiveMult: ["AD", "AP"], ehpMult: ["HP", "ARMOR", "MR"], shieldMult: ["HEALSHIELD"], HSP: ["HEALSHIELD"] };
  function scaleTags(a, rule) {
    const st = rule.statsResolved;
    if (!(a.effects && a.effects.length) && !st) return null;
    const out = new Set();
    for (const e of a.effects || []) for (const k of Object.keys(e.ratios || {})) for (const t of RATIO_TAG[k] || []) out.add(t);
    for (const k of Object.keys(st || {})) for (const t of MULT_TAG[k] || []) out.add(t);
    return out;
  }

  function flipWeights(w) {
    const f = { ...w };
    [f.AD, f.AP] = [w.AP, w.AD];
    [f.LETHAL, f.MPEN] = [w.MPEN, w.LETHAL];
    // 딜러는 탱커 쪽으로, 탱커는 딜러 쪽으로
    const tanky = (w.HP + w.ARMOR + w.MR) / 3;
    const dps = (w.AS + w.CRIT) / 2;
    f.HP = f.ARMOR = f.MR = Math.max(0.2, dps);
    f.AS = f.CRIT = Math.max(0.1, tanky * 0.6);
    return f;
  }

  // 변칙을 받쳐 주는 증강: 챔피언 성향을 뒤집었을 때 커지는 쪽(징크스면 체력·방어·주문력)을 고른 증강이 키우는가.
  // 성향만 뒤집은 빌드는 재미를 보장하지 않는다 (원딜에 탱커 아이템). 증강 때문에 플레이가 바뀔 때가 진짜 변칙 (2026-10-15 검토)
  // s: 0(받쳐 주는 증강 없음) ~ 1. 환산 증강(공격력→주문력 등)이 뒤집힌 쪽으로 바꾸면 1
  const TWIST_TAGS = ["AD", "AP", "LETHAL", "MPEN", "HP", "ARMOR", "MR", "AS", "CRIT"];
  const TWIST_FOCUS = 2; // 변칙: 받쳐 주는 증강이 보태는 가치를 키우는 비율
  const TWIST_GAP = 0.3; // 뒤집었을 때 이만큼 커지는 태그가 '반대쪽'
  function twistSupport(w, ctx) {
    const f = flipWeights(w);
    const opp = TWIST_TAGS.filter((t) => (f[t] || 0) - (w[t] || 0) >= TWIST_GAP);
    let s = 0;
    const by = new Set();
    // 원래 쪽(제드면 공격력)도 같이 키우는 증강(적응형: 공격력·주문력 둘 다 계수)은 뒤집을 이유가 없다 → 반대쪽만 키우는 증강만
    const own = TWIST_TAGS.filter((t) => !opp.includes(t) && (w[t] || 0) >= 0.5);
    // 숫자 없이 효과 종류만 있는 증강은 변칙 근거로 쓰지 않는다 (메아리 시전 '주문력' 태그로 제드가 뒤집혔다: 숫자로 확인되는 것만)
    for (const [name, lifted] of Object.entries(ctx.liftBy || {})) {
      const sc = lifted._convert.length ? new Set(lifted._convert) : lifted._scales;
      if (!sc || own.some((t) => sc.has(t))) continue;
      for (const t of opp) if (lifted[t] && sc.has(t)) { s = Math.max(s, lifted[t]); by.add(name); }
    }
    return { s: Math.min(1, s), by: [...by], opp };
  }
  // 받쳐 주는 증강이 있으면 그만큼만 뒤집는다 (0.5~1). 없으면 성향만 뒤집은 '실험' 빌드 (화면에서 그렇게 알린다)
  const twistK = (sup) => (sup.s > 0 ? 0.5 + 0.5 * sup.s : 1);
  const lerp = (a, b, k) => {
    const out = {};
    for (const t of new Set([...Object.keys(a), ...Object.keys(b)])) out[t] = (a[t] || 0) * (1 - k) + (b[t] || 0) * k;
    return out;
  };

  function conceptWeights(w, ctx, concept) {
    const base = concept.flip ? lerp(w, flipWeights(w), twistK(twistSupport(w, ctx))) : w;
    const lift = ctx.lift || ctx.tags;
    const hasAug = Object.keys(lift).length > 0;  // 빌드가 따라갈 태그가 없는 증강이면 챔피언 성향 그대로
    const champK = hasAug ? concept.champ : 1;
    const out = {};
    for (const t of Object.keys(w)) out[t] = (base[t] || 0) * champK + (lift[t] || 0) * concept.aug;
    for (const t of ctx.zero) out[t] = 0;
    return out;
  }

  // 주 피해 유형 (라이엇 게임 데이터 tacticalInfo.damageType): 물리 챔피언 기본 빌드에 AP 아이템, 마법 챔피언에 AD·치명타를 넣지 않는다.
  // 스킬 계수만으로 계산하면 이렐리아(E·R 이 주문력 계수)가 라바돈·리치베인으로 쏠렸다 (2026-10-14 실제 게임에선 징수의 총).
  // 막지 않는 경우: 변칙(일부러 반대로), 환산 증강(공격력→주문력 등), 고른 증강이 반대 유형을 키울 때 (증강 따라 변신이 재미)
  const OFF_TYPE = {
    physical: { off: ["AP", "MPEN"], own: ["AD", "CRIT", "LETHAL"] },
    magic: { off: ["AD", "CRIT", "LETHAL"], own: ["AP", "MPEN"] },
  };
  // 치명타 아이템은 원거리 딜러·원거리 물리 챔피언·치명타 특성이 있는 챔피언(야스오·요네·트린다미어)만.
  // 다리우스·트런들·볼리베어 같은 전사 기본 빌드에 윤 탈 야생화살이 들어가던 것을 막는다
  const critUser = (champ) => champ.tags.includes("Marksman") || !champ.melee || ((champ.traits || {}).CRIT || 0) > 0;
  function offTypeFilter(champ, ctx, concept) {
    const rule = champ && OFF_TYPE[champ.damageType];
    if (!rule || (concept && concept.flip)) return null;
    // 치명타 아이템은 공격력이 같이 붙어 있어도 뺀다 (치명타를 못 살리는 챔피언)
    const lift = ctx.lift || ctx.tags;
    const noCrit = champ.damageType === "physical" && !critUser(champ) && !((lift.CRIT || 0) >= 0.5);
    if (Object.keys(ctx.convert || {}).some((t) => rule.off.includes(ctx.convert[t]) || rule.off.includes(t))) return null;
    if (rule.off.some((t) => (lift[t] || 0) >= 0.5)) return null;
    return (item) => {
      const t = convertTags(item.tags || {}, ctx.convert || {});
      // 치명타가 능력치 줄에만 있고 태그엔 없는 아이템이 있다 (윤 탈 야생화살: 치명타 0 에서 기본 지속 효과로 쌓임)
      const st = item.stats || {};
      if (Array.isArray(st) ? st.includes("CRIT") : "CRIT" in st) t.CRIT = Math.max(t.CRIT || 0, 1);
      if (noCrit && (t.CRIT || 0) >= 0.5) return true;
      return rule.off.some((k) => (t[k] || 0) >= 0.5) && !rule.own.some((k) => (t[k] || 0) >= 0.5);
    };
  }

  // 환산 증강(공격력→주문력 등)이 있으면 아이템 태그를 바꿔서 본다
  function convertTags(tags, convert) {
    const out = { ...tags };
    for (const [from, to] of Object.entries(convert)) {
      if (out[from]) {
        out[to] = Math.max(out[to] || 0, out[from]);
        delete out[from];
      }
    }
    return out;
  }

  function scoreItem(item, iw, covered, opts = {}) {
    const ctx = opts.ctx;
    const tags = convertTags(item.tags, (ctx && ctx.convert) || {});
    const parts = [];
    let score = 0;
    for (const [t, s] of Object.entries(tags)) {
      const rate = t === "AD" || t === "AP" ? MAIN_STAT_DECAY : ITEM_DECAY;
      const decay = Math.pow(rate, (covered && covered[t]) || 0);
      const v = (iw[t] || 0) * Math.min(s, 1.5) * decay;
      if (v > 0) parts.push({ tag: t, value: v });
      score += v;
    }
    parts.sort((a, b) => b.value - a.value);
    const reasons = [];
    if (ctx) {
      if (ctx.wanted[item.name]) {
        score += RULE_ITEM_BOOST;
        reasons.push(`${ctx.wanted[item.name].join("·")} 증강과 연계`);
      }
      for (const p of parts.slice(0, 3)) {
        if (ctx.src[p.tag]) reasons.push(`${ctx.src[p.tag][0]}: ${TAG_LABEL[p.tag]}`);
      }
    }
    let mismatch = false;
    if (opts.mismatch !== false) {
      const offAD = (tags.AD || 0) >= 0.5 && !(tags.AP >= 0.5) && (iw.AD || 0) < 0.3;
      const offAP = (tags.AP || 0) >= 0.5 && !(tags.AD >= 0.5) && (iw.AP || 0) < 0.3;
      if (offAD || offAP) { score *= MISMATCH; mismatch = true; }
    }
    return { score, parts, reasons: [...new Set(reasons)].slice(0, 3), mismatch };
  }

  // 빌드 가중치 → 지표 가중치 (합 1)
  function metricWeights(iw) {
    const mw = {
      combo: (iw.AP || 0) + (iw.AD || 0) + 0.5 * ((iw.LETHAL || 0) + (iw.MPEN || 0)) + 0.3 * (iw.HASTE || 0),
      dps: (iw.AS || 0) + (iw.CRIT || 0) + 0.5 * (iw.ONHIT || 0) + 0.3 * (iw.AD || 0),
      ehp: ((iw.HP || 0) + (iw.ARMOR || 0) + (iw.MR || 0)) / 2,
      support: iw.HEALSHIELD || 0,
      mobility: 0.4 * (iw.MS || 0),  // 기동성은 피해·생존보다 덜 중요하게
      control: 0.8 * (iw.CC || 0),
    };
    const sum = Object.values(mw).reduce((a, b) => a + b, 0) || 1;
    for (const k of Object.keys(mw)) mw[k] /= sum;
    return mw;
  }

  function addCovered(covered, tags) {
    for (const [t, s] of Object.entries(tags)) if (s >= 0.5) covered[t] = (covered[t] || 0) + 1;
  }

  // 빌드 가치(수치 부분) = Σ 지표 가중치 × 지표 / 기준값. 기준값은 아이템 없는 상태(최소 METRIC_FLOOR)
  function valueOf(m, num) {
    let v = 0;
    for (const k of Object.keys(num.mw)) v += num.mw[k] * m[k] / num.norm[k];
    return v;
  }

  // 이 빌드(아이템 목록)에서 증강 하나가 보태는 가치: 증강을 더한 뒤 − 더하기 전 (지표 가중치는 증강을 고른 균형 기준으로 고정)
  // 추천 품질 점검(tools/augment_quality.js)에서 '몰빵 빌드가 증강을 더 살리나'를 잴 때 쓴다
  function augContribution(items, w, chosen, aug, rules, champ, statOpts = {}) {
    const ctx = augContext([...chosen, aug], rules);
    const num = { mw: metricWeights(conceptWeights(w, ctx, CONCEPTS[1])), norm: {} };
    const c = withChosenFeatures(champ, [...chosen, aug], rules);
    const base = augStatsOf(chosen, rules, c);
    const empty = Stats.evaluate(c, 18, [], base, statOpts).m;
    for (const k of Object.keys(METRIC_FLOOR)) num.norm[k] = Math.max(empty[k], METRIC_FLOOR[k]);
    const v0 = valueOf(Stats.evaluate(c, 18, items, base, statOpts).m, num);
    const v1 = valueOf(Stats.evaluate(c, 18, items, [...base, ...augStatsOf([aug], rules, c)], statOpts).m, num);
    return v1 - v0;
  }

  // 화면 이유 문장용 실제 숫자 (퍼센트만 보면 원래 값이 작을 때 과장돼 보여서 '112 → 407'로 보여 준다)
  function absParts(m0, m1) {
    const out = {};
    for (const k of Object.keys(METRIC_FLOOR)) out[k] = [m0[k], m1[k]];
    return out;
  }

  function gainParts(m0, m1) {
    const parts = {};
    for (const k of Object.keys(METRIC_FLOOR)) parts[k] = (m1[k] - m0[k]) / Math.max(m0[k], METRIC_FLOOR[k]);
    return parts;
  }

  const BEAM = 6; // 동시에 키우는 후보 빌드 수
  const PER_BEAM = 5; // 후보 빌드마다 다음 아이템 후보 수

  // 6칸. 고정(fixed) 아이템은 그대로 두고 빈 칸만 채운다. 신발은 최대 1개(게임 규칙).
  // 신발이 없고 빈 칸이 있으면 신발 1칸을 먼저 채운다. 증강 보상 아이템(special)은 자동 추천하지 않는다.
  // 빔 탐색: 후보 빌드 BEAM 개를 같이 키워서, 2~3개가 모여야 강해지는 조합(치명타 등)도 찾는다.
  // 이미 많이 확보한 태그는 감쇠시켜 한쪽으로 쏠리지 않게 한다.
  function buildWith(items, iw, fixed, opts) {
    const num = opts.num ? { ...opts.num, mw: opts.mw || metricWeights(iw) } : null;
    const evalM = (list) => Stats.evaluate(num.champ, num.level, list, num.augStats, num.statOpts).m;
    // 증강 몰빵: 빌드 전체 가치에 더해 '증강이 보태는 가치'(증강 있음 − 없음)도 키운다 = 증강 효과 극대화
    const focus = num && opts.focus && num.augStats.length ? opts.focus : 0;
    const evalBare = (list) => Stats.evaluate(num.champ, num.level, list, [], num.statOpts).m;
    if (num) {
      const m0 = evalM([]);
      num.norm = {};
      for (const k of Object.keys(METRIC_FLOOR)) num.norm[k] = Math.max(m0[k], METRIC_FLOOR[k]);
    }
    const step = (state, item, fixedFlag) => {
      const tag = scoreItem(item, iw, state.covered, opts);
      const list = [...state.items, item];
      const m = num ? evalM(list) : null;
      const bare = focus ? evalBare(list) : null;
      const augPart = focus ? (valueOf(m, num) - valueOf(bare, num)) - (valueOf(state.m, num) - valueOf(state.bare, num)) : 0;
      const numGain = num ? (valueOf(m, num) - valueOf(state.m, num) + focus * augPart) * NUM_K : 0;
      const score = num ? numGain + tag.score * TAG_K : tag.score;
      const covered = { ...state.covered };
      addCovered(covered, convertTags(item.tags, opts.ctx.convert));
      return {
        items: list, covered, m, bare, total: state.total + score,
        picks: [...state.picks, { item, ...tag, tagScore: tag.score, score, gain: num ? gainParts(state.m, m) : null, fixed: fixedFlag }],
      };
    };
    let beams = [{ items: [], covered: {}, m: num ? evalM([]) : null, bare: focus ? evalBare([]) : null, total: 0, picks: [] }];
    for (const item of fixed) beams = [step(beams[0], item, true)];
    const expand = (pool, perBeam) => {
      const next = [];
      const seen = new Set();
      for (const st of beams) {
        const names = new Set(st.items.map((i) => i.name));
        const limits = new Set(st.items.map((i) => i.limit).filter(Boolean));
        // 같은 계열 고유 효과(주문 검·히드라·치명 …)는 하나만 적용되니 같이 고르지 않는다
        pool.filter((i) => !names.has(i.name) && !(i.limit && limits.has(i.limit)))
          .map((i) => step(st, i, false))
          .sort((a, b) => b.picks[b.picks.length - 1].score - a.picks[a.picks.length - 1].score)
          .slice(0, perBeam)
          .forEach((c) => {
            const key = c.items.map((i) => i.id).sort().join(",");
            if (!seen.has(key)) { seen.add(key); next.push(c); }
          });
      }
      if (next.length) beams = next.sort((a, b) => b.total - a.total).slice(0, BEAM);
      return next.length > 0;
    };
    const all = items;
    if (opts.off) items = items.filter((i) => !opts.off(i));
    if (!fixed.some((i) => i.kind === "boots") && fixed.length < SLOTS) {
      expand(items.filter((i) => i.kind === "boots"), 2);
    }
    // 아이템 업그레이드 증강(징수의 총 업그레이드 등)은 그 아이템을 사야 효과가 있다: 후보 중 하나를 먼저 넣는다.
    // 점수 가산만으로는 원래 안 사던 챔피언(징크스·징수의 총)에게 끝내 안 들어갔다 (2026-10-15 추천 품질 점검)
    for (const group of (opts.ctx && opts.ctx.must) || []) {
      if (beams[0].items.length >= SLOTS || fixed.some((i) => group.includes(i.name))) continue;
      const pool = all.filter((i) => group.includes(i.name) && i.kind === "legendary");
      if (pool.length) expand(pool, 1);
    }
    const legendary = items.filter((i) => i.kind === "legendary");
    while (beams[0].items.length < SLOTS) {
      if (!expand(legendary, PER_BEAM)) break;
    }
    const best = beams[0];
    return { pick: best.picks, final: best.m };
  }

  // 능력치 계산에 넘길 증강 데이터: 능력치(rules stats) + 피해·보호막 효과(게임 데이터)
  // champ 를 주면 증강 조건(밀어내기 스킬 등)을 못 맞출 때 효과를 뺀다 (발동하지 않으니까)
  function augStatsOf(chosen, rules, champ = null) {
    champ = withChosenFeatures(champ, chosen, rules);
    return chosen.map((a) => {
      const rule = ruleOf(rules, a);
      const unmet = champ && (a.eligible ? !a.eligible.includes(champ.id) : rule.requires && !(champ.features || []).includes(rule.requires));
      // 조건을 못 맞추면 능력치도 효과도 없다 (능력치만 남아 'D등급인데 계산상 강해짐'이 나왔다: 럭스 + 승리를 위한 회전)
      return unmet ? { _effects: [] } : { ...(rule.statsResolved || {}), _effects: (a.effects || []).map((e) => ({ ...e, src: a.name })) };
    });
  }

  const hasNumbers = (a, rules) => (a.effects && a.effects.length > 0) || !!ruleOf(rules, a).statsResolved;

  // 실제 숫자 평가: 지금 균형 빌드(완성 6칸, 18레벨)에 이 증강을 더했을 때 빌드 가치가 얼마나 오르나.
  // 숫자가 없는 증강(무적·CC·쿨 반환 등)은 null.
  function numericAugmentGainsRaw(augs, itemPool, w, chosen, fixed, rules, champ, statOpts = {}) {
    champ = withChosenFeatures(champ, chosen, rules);
    const ctx = augContext(chosen, rules);
    const iw = conceptWeights(w, ctx, CONCEPTS[1]);
    const bal = buildConcepts(itemPool, w, chosen, fixed, rules, champ, statOpts, ["balanced"])[0];
    const items = bal.pick.map((p) => p.item);
    const num = { mw: metricWeights(iw), norm: {} };
    const base = augStatsOf(chosen, rules, champ);
    const empty = Stats.evaluate(champ, 18, [], base, statOpts).m;
    for (const k of Object.keys(METRIC_FLOOR)) num.norm[k] = Math.max(empty[k], METRIC_FLOOR[k]);
    const m0 = Stats.evaluate(champ, 18, items, base, statOpts).m;
    const v0 = valueOf(m0, num);
    const out = {};
    for (const a of augs) {
      if (!hasNumbers(a, rules) || chosen.some((c) => c.id === a.id)) continue;
      const m1 = Stats.evaluate(champ, 18, items, [...base, ...augStatsOf([a], rules, champ)], statOpts).m;
      out[a.id] = { gain: valueOf(m1, num) - v0, parts: gainParts(m0, m1), abs: absParts(m0, m1) };
    }
    return { gains: out, build: items };
  }

  // champ 를 주면 수치 평가(18레벨)를 같이 한다
  // keys 를 주면 그 컨셉만 계산한다 (선택지 비교처럼 하나만 필요할 때)
  function buildConceptsRaw(items, w, chosen, fixed = [], rules = null, champ = null, statOpts = {}, keys = null) {
    champ = champ && withChosenFeatures(champ, chosen, rules);  // 고른 증강이 챔피언 특징을 바꾸면(검을 뽑아라 → 근접) 그 기준으로
    const ctx = augContext(chosen, rules);
    const num = champ && champ.spells ? { champ, level: 18, augStats: augStatsOf(chosen, rules, champ), statOpts } : null;
    // 변칙: 균형 빌드가 중시하는 지표를 뒤집어서, 챔피언이 원래 약한 쪽(럭스면 탱킹·평타)을 키운다
    const normal = metricWeights(conceptWeights(w, ctx, CONCEPTS[1]));
    const inv = {};
    for (const k of Object.keys(normal)) inv[k] = Math.max(0.05, 1 - normal[k] * 2);
    const invSum = Object.values(inv).reduce((a, b) => a + b, 0);
    for (const k of Object.keys(inv)) inv[k] /= invSum;
    return CONCEPTS.filter((c) => !keys || keys.includes(c.key)).map((c) => {
      const iw = conceptWeights(w, ctx, c);
      if (!c.flip) return { ...c, weights: iw, ...buildWith(items, iw, fixed, { ctx, mismatch: c.mismatch, num, mw: null, focus: c.focus, off: offTypeFilter(champ, ctx, c) }) };
      const twist = twistSupport(w, ctx);
      const k = twistK(twist);
      const mw = k >= 1 ? inv : lerp(metricWeights(iw), inv, k);
      // 받쳐 주는 증강이 있으면 그 증강이 보태는 가치도 키운다 (뒤집은 쪽 아이템이 증강 덕분에 쓸모가 생기는 방향으로)
      return { ...c, weights: iw, twist: { ...twist, k }, ...buildWith(items, iw, fixed, { ctx, mismatch: c.mismatch, num, mw, focus: twist.s > 0 ? TWIST_FOCUS : 0, off: null }) };
    });
  }

  // 아이템 목록 정렬용: 균형 컨셉 기준으로 '지금 빌드에 하나 더 넣으면' 점수 (추천 빌드와 같은 가치 함수)
  // conceptKey: 아이템 목록 정렬 기준 컨셉 (증강을 골랐으면 "augment" 로 연계 아이템을 위로)
  function rankItems(items, w, chosen, fixed = [], rules = null, champ = null, statOpts = {}, conceptKey = "balanced") {
    champ = champ && withChosenFeatures(champ, chosen, rules);
    const ctx = augContext(chosen, rules);
    const concept = CONCEPTS.find((c) => c.key === conceptKey) || CONCEPTS[1];
    const iw = conceptWeights(w, ctx, concept);
    const off = offTypeFilter(champ, ctx, concept);
    const covered = {};
    for (const it of fixed) addCovered(covered, convertTags(it.tags, ctx.convert));
    const num = champ && champ.spells ? { champ, level: 18, augStats: augStatsOf(chosen, rules, champ), statOpts, mw: metricWeights(iw) } : null;
    let m0 = null;
    if (num) {
      const empty = Stats.evaluate(champ, 18, [], num.augStats, statOpts).m;
      num.norm = {};
      for (const k of Object.keys(METRIC_FLOOR)) num.norm[k] = Math.max(empty[k], METRIC_FLOOR[k]);
      m0 = Stats.evaluate(champ, 18, fixed, num.augStats, statOpts).m;
    }
    return items
      .filter((i) => !fixed.some((f) => f.name === i.name))
      .map((i) => {
        const tag = scoreItem(i, iw, covered, { ctx });
        if (!num) return { item: i, ...tag, tagScore: tag.score };
        const m1 = Stats.evaluate(champ, 18, [...fixed, i], num.augStats, statOpts).m;
        const score = (valueOf(m1, num) - valueOf(m0, num)) * NUM_K + tag.score * TAG_K;
        return { item: i, ...tag, tagScore: tag.score, score, gain: gainParts(m0, m1) };
      })
      .map((r) => (off && off(r.item) ? { ...r, offType: true } : r))
      // 주 피해 유형과 반대인 아이템은 목록 맨 뒤로 (지우지는 않는다: 직접 골라 보는 건 자유)
      .sort((a, b) => (a.offType ? 1 : 0) - (b.offType ? 1 : 0) || b.score - a.score);
  }

  // 연계 티어: 지금까지 고른 증강 기준으로 같은 등급 증강을 S/A/B/C/D 로 나눈다.
  // 숫자로 평가되는 증강은 빌드 가치 증가(같이 골랐을 때)로, 나머지는 효과 점수로 각각 순위를 매긴다.
  // 궁합(interaction) = 같이 골랐을 때 가치 − 혼자 골랐을 때 가치 (고른 증강이 있을 때만)
  const TIER_CUTS = [["S", 0.1], ["A", 0.3], ["B", 0.6], ["C", 0.85], ["D", 1]];
  const SYNERGY_MARK = 0.05; // 궁합 표시 기준 (가치 ×100 기준 ±5)

  // ---- 연계 상태: 등급(S~D, 같은 등급 증강끼리의 순위)과 따로 '고른 증강과 실제로 맞는가'를 보여 준다 (2026-10-15 기획)
  // strong 강한 연계 / some 일반 연계 / none 직접 연계 없음 / conflict 효과 충돌 / unsure 판정 불확실
  // 숫자 근거 = 궁합(같이 골랐을 때 가치 − 따로 골랐을 때 가치, 고른 증강 전부 누적). 숫자가 없으면 연계라고 단정하지 않는다
  const LINK_STRONG = SYNERGY_MARK; // 화면 '궁합'과 같은 기준 (가치 ×100 기준 +5)
  const LINK_SOME = 0.01;
  const ULT_STATS = ["ultHaste", "ultHasteAdd", "ultRefundEvery", "ultTriggerHaste", "ultTriggerDur", "ultTriggerMs"];
  const setsMelee = (r) => ((r.setsFeatures || {}).add || []).includes("melee");
  const usesUlt = (rules, a) => (effTags(rules, a).ULT || 0) >= 0.6 || ULT_STATS.some((k) => k in (ruleOf(rules, a).statsResolved || {}));
  // 규칙으로 아는 충돌: 한 증강이 다른 증강의 발동·효과를 막는 경우만 (단순히 안 맞는 건 '직접 연계 없음')
  function ruleConflict(a, chosen, rules) {
    const ra = ruleOf(rules, a);
    for (const c of chosen) {
      const rc = ruleOf(rules, c);
      if ((rc.zero || []).includes("ULT") && usesUlt(rules, a)) return `${c.name} 때문에 궁극기를 못 써서 이 증강의 궁극기 효과가 사라져요.`;
      if ((ra.zero || []).includes("ULT") && usesUlt(rules, c)) return `궁극기를 봉인해서 ${c.name}의 궁극기 효과가 사라져요.`;
      for (const [f, to] of Object.entries(ra.convert || {})) {
        if ((rc.convert || {})[to] === f) return `${c.name}의 능력치 환산과 방향이 반대라 서로 효과를 지워요.`;
        if (f === "AS" && (effTags(rules, c).AS || 0) >= 0.6) return `공격 속도가 스킬 가속으로 바뀌어서 ${c.name}의 공격 속도 효과가 사라져요.`;
      }
      if ((rc.convert || {}).AS && (effTags(rules, a).AS || 0) >= 0.6) return `${c.name} 때문에 공격 속도가 스킬 가속으로 바뀌어서 이 증강의 공격 속도 효과가 사라져요.`;
      if (setsMelee(rc) && ra.requires === "ranged") return `${c.name}로 근접이 돼서 원거리 전용 효과가 사라져요.`;
      if (setsMelee(ra) && rc.requires === "ranged") return `근접이 돼서 ${c.name}의 원거리 전용 효과가 사라져요.`;
    }
    return null;
  }
  function linkOf(a, chosen, rules, interaction, numeric, synTag) {
    if (!chosen.length) return null;
    const why = ruleConflict(a, chosen, rules);
    if (why) return { kind: "conflict", why };
    // 숫자상 손해(궁합 음수)는 '충돌'이라 하지 않는다: 같이/따로 비교할 때 기준 빌드가 달라 생기는 차이가 섞여 있다.
    // 충돌은 규칙으로 아는 것(궁극기 봉인·반대 환산·근접 전환)만
    const sure = numeric && chosen.every((c) => hasNumbers(c, rules));
    if (interaction != null && interaction >= LINK_STRONG) return { kind: "strong" };
    if (interaction != null && interaction >= LINK_SOME) return { kind: "some" };
    if (sure) return { kind: "none" };
    // 숫자로 계산이 안 되는 쪽이 있으면 '연계 없음'이라고도 '강한 연계'라고도 하지 않는다
    return { kind: "unsure", hint: synTag > 0 };
  }

  // mode: "total"(같이 골랐을 때 총 가치 + 궁합 보정) / "synergy"(고른 증강과의 궁합만)
  function augmentTiers(augs, itemPool, w, chosen, fixed, rules, champ, statOpts = {}, mode = "total") {
    const synergyMode = mode === "synergy" && chosen.length > 0;
    const withC = numericAugmentGains(augs, itemPool, w, chosen, fixed, rules, champ, statOpts).gains;
    const alone = chosen.length ? numericAugmentGains(augs, itemPool, w, [], fixed, rules, champ, statOpts).gains : withC;
    const tagged = rankAugments(augs, w, chosen, fixed, champ, rules);
    const tagScore = Object.fromEntries(tagged.map((r) => [r.aug.id, r]));
    const out = {};
    const chosenIds = new Set(chosen.map((a) => a.id));
    for (const rarity of [0, 1, 2]) {
      const pool = augs.filter((a) => a.rarity === rarity && !chosenIds.has(a.id));
      // 연계만 보기: 숫자 증강은 궁합(같이 − 혼자), 나머지는 효과 점수 중 시너지로 붙은 부분만
      const inter = (a) => withC[a.id].gain - (alone[a.id] ? alone[a.id].gain : 0);
      const synTag = (a) => ((tagScore[a.id] || {}).parts || []).filter((p) => p.synergy).reduce((t, p) => t + p.value, 0)
        + ((tagScore[a.id] || {}).itemMatch ? 0.3 : 0);
      const num = pool.filter((a) => withC[a.id])
        .sort((x, y) => (synergyMode ? inter(y) - inter(x) : withC[y.id].gain - withC[x.id].gain));
      const rest = pool.filter((a) => !withC[a.id])
        .sort((x, y) => (synergyMode ? synTag(y) - synTag(x) : 0) || (tagScore[y.id]?.score || 0) - (tagScore[x.id]?.score || 0));
      for (const [list, basis] of [[num, "수치"], [rest, "효과"]]) {
        list.forEach((a, i) => {
          const pct = (i + 1) / list.length;
          let tier = TIER_CUTS.find(([, cut]) => pct <= cut)[0];
          const sc = tagScore[a.id] || {};
          const g = withC[a.id];
          const reasons = [];
          if (sc.req && !sc.req.met) { tier = "D"; reasons.push("조건 없음"); }
          if (!synergyMode && g && g.gain < 0) { tier = "D"; reasons.push("손해"); }
          const interaction = g && chosen.length && alone[a.id] ? g.gain - alone[a.id].gain : null;
          if (synergyMode && interaction != null && interaction <= 0 && tier !== "D") { tier = "D"; reasons.push("연계 없음"); }
          if (synergyMode && !g && synTag(a) <= 0 && tier !== "D") { tier = "D"; reasons.push("연계 없음"); }
          // 종합 보기에서는 궁합이 크면 한 단계 올리고, 충돌이면 한 단계 내린다 (연계만 보기는 궁합이 이미 기준)
          const order = ["S", "A", "B", "C", "D"];
          if (!synergyMode && interaction != null && tier !== "D") {
            if (interaction >= SYNERGY_MARK) { tier = order[Math.max(0, order.indexOf(tier) - 1)]; reasons.push("궁합"); }
            else if (interaction <= -SYNERGY_MARK) { tier = order[Math.min(4, order.indexOf(tier) + 1)]; reasons.push("충돌"); }
          }
          if (synergyMode && interaction != null && interaction > 0) reasons.push("궁합");
          const link = sc.req && !sc.req.met ? null : linkOf(a, chosen, rules, interaction, !!g, synTag(a));
          // 규칙으로 아는 충돌(궁극기 봉인 + 궁극기 증강 등)은 숫자에 안 잡히는 경우가 있어 등급도 한 단계 내린다
          if (link && link.kind === "conflict" && link.why && !reasons.includes("충돌")) {
            reasons.push("충돌");
            if (tier !== "D") tier = order[Math.min(4, order.indexOf(tier) + 1)];
          }
          out[a.id] = { tier, pct, basis, gain: g ? g.gain : null, interaction, reasons, link, rank: i + 1, n: list.length, mode: synergyMode ? "synergy" : "total" };
        });
      }
    }
    return out;
  }

  // 아이템 연계: 고른 증강이 있을 때와 없을 때 점수 차이. 클수록 "증강 때문에 좋아진" 아이템
  function itemSynergy(items, w, chosen, fixed, rules, champ, statOpts = {}) {
    if (!chosen.length) return {};
    const withA = rankItems(items, w, chosen, fixed, rules, champ, statOpts, "augment");
    const without = Object.fromEntries(rankItems(items, w, [], fixed, rules, champ, statOpts, "balanced").map((r) => [r.item.id, r.score]));
    return Object.fromEntries(withA.map((r) => [r.item.id, { delta: r.score - (without[r.item.id] || 0), score: r.score, reasons: r.reasons, gain: r.gain }]));
  }

  // ---- 계산 결과 기억 (같은 챔피언·증강·아이템·가중치·상대면 다시 계산하지 않는다)
  // 증강 목록·선택지·추천 빌드·게임 화면 표시가 같은 계산을 여러 번 부르고, 게임 연동은 3초마다 다시 그린다.
  // 결과는 읽기 전용으로 쓴다 (부르는 쪽에서 고치면 다음 호출에 그대로 나온다).
  let MEMO_MAX = 40;  // 가벼운 모드(저사양 PC)에선 setMemoMax 로 줄인다
  const memo = new Map();
  const ids = (list) => (list || []).map((x) => x.id);
  // 목록·규칙은 내용(id)으로 구분한다 (길이만 보면 다른 후보 목록에 앞 결과가 나왔다). 규칙은 객체마다 번호 (데이터 갱신)
  const objNo = new WeakMap();
  let objSeq = 0;
  const objId = (o) => (o && typeof o === "object" ? (objNo.get(o) || (objNo.set(o, ++objSeq), objSeq)) : null);
  function memoKey(name, list, w, chosen, fixed, champ, statOpts, extra, rules = null) {
    const T = (typeof Stats !== "undefined" && Stats.TARGET) || {};
    return JSON.stringify([name, ids(list).join(","), champ && champ.id, (champ && champ.features) || null, ids(chosen), ids(fixed),
      w, statOpts, extra, objId(rules), [T.ARMOR, T.MR, T.HP]]);
  }
  function remember(key, fn) {
    if (memo.has(key)) {
      const v = memo.get(key);
      memo.delete(key); memo.set(key, v);  // 최근 사용으로
      return v;
    }
    const v = fn();
    memo.set(key, v);
    if (memo.size > MEMO_MAX) memo.delete(memo.keys().next().value);
    return v;
  }
  function numericAugmentGains(augs, itemPool, w, chosen, fixed, rules, champ, statOpts = {}) {
    return remember(memoKey("gains", augs, w, chosen, fixed, champ, statOpts, ids(itemPool).join(","), rules),
      () => numericAugmentGainsRaw(augs, itemPool, w, chosen, fixed, rules, champ, statOpts));
  }
  // 빌드는 종류(증강 몰빵·균형·변칙)마다 따로 기억한다: 균형만 계산해 둔 뒤 3종을 달라고 하면 균형을 다시 찾던 중복을 없앤다
  function buildConcepts(items, w, chosen, fixed = [], rules = null, champ = null, statOpts = {}, keys = null) {
    return CONCEPTS.filter((c) => !keys || keys.includes(c.key)).map((c) =>
      remember(memoKey("build", items, w, chosen, fixed, champ, statOpts, c.key, rules),
        () => buildConceptsRaw(items, w, chosen, fixed, rules, champ, statOpts, [c.key])[0]));
  }
  const clearMemo = () => memo.clear();
  const setMemoMax = (n) => { MEMO_MAX = n; while (memo.size > MEMO_MAX) memo.delete(memo.keys().next().value); };

  const api = {
    SLOTS, TAG_LABEL, CONCEPTS, profile, scoreAugment, rankAugments, augContext, augStatsOf, metricWeights,
    numericAugmentGains, hasNumbers, augmentTiers, twistSupport, augContribution, ruleConflict, SYNERGY_MARK, itemSynergy,
    conceptWeights, buildConcepts, rankItems, scoreItem, clearMemo, setMemoMax, withChosenFeatures,
  };
  if (typeof module !== "undefined") module.exports = api;
  else root.Scoring = api;
})(this);
