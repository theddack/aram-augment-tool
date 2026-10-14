// 받은 파일 확인 페이지: 지문 비교 판정 (GitHub API 는 가짜 응답)
const test = require("node:test");
const assert = require("node:assert/strict");
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { JSDOM } = require("jsdom");
const WEB = path.join(__dirname, "..", "web");
const sha = (buf) => crypto.createHash("sha256").update(buf).digest("hex");

async function page(releases) {
  const html = fs.readFileSync(path.join(WEB, "verify.html"), "utf8").replace(/<script src="[^"]+"><\/script>/g, "");
  const dom = new JSDOM(html, { runScripts: "outside-only", url: "https://theddack.github.io/aram-augment-tool/verify.html" });
  const w = dom.window;
  w.fetch = async () => ({ ok: true, json: async () => releases });
  Object.defineProperty(w, "crypto", { value: globalThis.crypto });
  w.eval(fs.readFileSync(path.join(WEB, "verify.js"), "utf8"));
  return w;
}
async function check(w, buf, name) {
  const f = new w.File([buf], name);
  f.arrayBuffer = async () => buf.buffer.slice(buf.byteOffset, buf.byteOffset + buf.byteLength);
  await w.check(f);
  const r = w.document.querySelector("#result");
  return { cls: r.className, title: r.querySelector("h2").textContent };
}

const newZip = Buffer.from("새 버전 파일"), oldZip = Buffer.from("예전 버전 파일");
const rel = (tag, buf) => ({ tag_name: tag, draft: false, prerelease: false, published_at: "2026-10-14T00:00:00Z",
  assets: [{ name: "KalbaramAddon-Windows.zip", digest: "sha256:" + sha(buf) }] });

test("최신 공식 파일 → ✅", async () => {
  const w = await page([rel("v0.1.7", newZip), rel("v0.1.6", oldZip)]);
  const r = await check(w, newZip, "KalbaramAddon-Windows.zip");
  assert.equal(r.cls, "ok"); assert.match(r.title, /공식 파일/);
});
test("예전 공식 파일 → ⚠️ 최신 안내", async () => {
  const w = await page([rel("v0.1.7", newZip), rel("v0.1.6", oldZip)]);
  const r = await check(w, oldZip, "KalbaramAddon-Windows.zip");
  assert.equal(r.cls, "warn"); assert.match(r.title, /예전/);
});
test("1바이트라도 다르면 → ⛔", async () => {
  const w = await page([rel("v0.1.7", newZip)]);
  const bad = Buffer.from(newZip); bad[0] ^= 1;
  const r = await check(w, bad, "KalbaramAddon-Windows.zip");
  assert.equal(r.cls, "bad");
});
test("GitHub 응답 실패 → 직접 비교 안내", async () => {
  const html = fs.readFileSync(path.join(WEB, "verify.html"), "utf8").replace(/<script src="[^"]+"><\/script>/g, "");
  const dom = new JSDOM(html, { runScripts: "outside-only", url: "https://x/" });
  dom.window.fetch = async () => ({ ok: false, status: 403, json: async () => ({}) });
  Object.defineProperty(dom.window, "crypto", { value: globalThis.crypto });
  dom.window.eval(fs.readFileSync(path.join(WEB, "verify.js"), "utf8"));
  const r = await check(dom.window, newZip, "a.zip");
  assert.equal(r.cls, "warn"); assert.match(r.title, /받아 오지 못했어요/);
});
