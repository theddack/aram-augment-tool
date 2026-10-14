// 받은 파일 확인: 파일의 SHA-256 을 브라우저에서 계산해 GitHub 공식 릴리스 파일 지문(digest)과 비교한다.
// 지문은 GitHub 가 릴리스 파일마다 서버에서 계산해 API 로 공개하는 값이다 (우리가 올린 값이 아니라서 더 믿을 만하다).
// 파일은 어디에도 보내지 않는다.
const REPO = "theddack/aram-augment-tool";
const RELEASES = `https://api.github.com/repos/${REPO}/releases?per_page=100`;
const PAGE = `https://github.com/${REPO}/releases/latest`;
const $ = (s) => document.querySelector(s);
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

let official = null; // 지문 → {tag, asset, date, latest}
async function loadOfficial() {
  if (official) return official;
  const r = await fetch(RELEASES, { headers: { Accept: "application/vnd.github+json" } });
  if (!r.ok) throw new Error(`GitHub 응답 ${r.status}`);
  const rels = (await r.json()).filter((x) => !x.draft && !x.prerelease);
  const latestTag = rels.length ? rels[0].tag_name : null;  // 최신이 먼저 온다
  official = {};
  for (const rel of rels) {
    for (const a of rel.assets || []) {
      const m = /^sha256:([0-9a-f]{64})$/.exec(a.digest || "");
      if (m) official[m[1]] = { tag: rel.tag_name, asset: a.name, date: (rel.published_at || "").slice(0, 10), latest: rel.tag_name === latestTag, latestTag };
    }
  }
  official._latestTag = latestTag;
  return official;
}

async function sha256(file) {
  const buf = await file.arrayBuffer();
  const h = await crypto.subtle.digest("SHA-256", buf);
  return [...new Uint8Array(h)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function show(kind, html) {
  const el = $("#result");
  el.className = kind;
  el.style.display = "block";
  el.innerHTML = html;
}

async function check(file) {
  if (!file) return;
  show("", `<h2>확인하는 중…</h2><div class="muted small">${esc(file.name)} · ${(file.size / 1048576).toFixed(1)}MB</div>`);
  let hash;
  try {
    hash = await sha256(file);
  } catch (e) {
    return show("warn", `<h2>⚠️ 파일을 읽지 못했어요</h2><div class="small">${esc(e.message)}</div>`);
  }
  let list;
  try {
    list = await loadOfficial();
  } catch (e) {
    return show("warn", `<h2>⚠️ 공식 지문을 받아 오지 못했어요</h2>
      <div class="small">인터넷 연결을 확인하고 다시 해 보세요 (${esc(e.message)}). 직접 비교하려면 <a href="${PAGE}" target="_blank" rel="noopener">릴리스 페이지</a>의 SHA-256 값과 아래 값을 비교하세요.</div>
      <div class="hash">이 파일의 SHA-256: ${hash}</div>`);
  }
  const hit = list[hash];
  const os = hit && /Windows/i.test(hit.asset) ? "윈도우용" : hit && /mac/i.test(hit.asset) ? "맥용" : "";
  if (hit && hit.asset.endsWith(".txt")) {
    return show("warn", `<h2>⚠️ 프로그램 파일이 아니에요</h2><div class="small">체크섬 목록(${esc(hit.asset)})이에요. 받은 zip 파일을 넣어 주세요.</div>`);
  }
  if (hit && hit.latest) {
    return show("ok", `<h2>✅ 공식 파일이에요</h2>
      <div>칼바람 증강 Addon <b>${esc(hit.tag)}</b> · ${os} · ${esc(hit.date)} 릴리스</div>
      <div class="small" style="margin-top:6px">압축을 풀고 실행하세요. 윈도우에서 경고가 뜨면 <b>추가 정보 → 실행</b>, 맥은 <b>시스템 설정 → 개인정보 보호 및 보안 → 그래도 열기</b>.</div>
      <div class="hash">SHA-256 ${hash}</div>`);
  }
  if (hit) {
    return show("warn", `<h2>⚠️ 예전 공식 파일이에요 (${esc(hit.tag)})</h2>
      <div class="small">공식 파일은 맞지만 최신이 아니에요. <a href="${PAGE}" target="_blank" rel="noopener">최신 ${esc(list._latestTag)}</a>를 받으세요.</div>
      <div class="hash">SHA-256 ${hash}</div>`);
  }
  show("bad", `<h2>⛔ 공식 파일이 아니에요</h2>
    <div>어떤 공식 릴리스 파일과도 일치하지 않아요. <b>실행하지 말고 지우세요.</b></div>
    <div class="small" style="margin-top:6px"><a href="${PAGE}" target="_blank" rel="noopener">공식 릴리스 페이지</a>에서 다시 받으세요.
      압축을 푼 폴더나 exe 를 넣었다면, 받은 zip 파일 그대로 다시 넣어 주세요.</div>
    <div class="hash">이 파일의 SHA-256: ${hash}</div>`);
}

const drop = $("#drop");
$("#pick").addEventListener("click", (e) => { e.stopPropagation(); $("#file").click(); });
drop.addEventListener("click", () => $("#file").click());
drop.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); $("#file").click(); } });
$("#file").addEventListener("change", (e) => check(e.target.files[0]));
["dragenter", "dragover"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add("over"); }));
["dragleave", "drop"].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
drop.addEventListener("drop", (e) => check(e.dataTransfer.files[0]));
loadOfficial().catch(() => {}); // 미리 받아 두기
