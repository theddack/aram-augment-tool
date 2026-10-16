// 테스트용: web/index.html 을 jsdom 에 띄우고 stats.js·scoring.js·app.js 를 실행한다 (실제 data.json 사용).
const { JSDOM } = require("jsdom");
const fs = require("fs");
const path = require("path");

const WEB = path.join(__dirname, "..", "web");
const DATA = fs.readFileSync(path.join(WEB, "data.json"), "utf8");

async function loadPage({ simple = false, edit = null } = {}) {  // edit: data.json 을 바꿔서 띄울 때 (데이터에 값이 없는 경우 등)
  const html = fs.readFileSync(path.join(WEB, "index.html"), "utf8").replace(/<script src="[^"]+"><\/script>/g, "");
  const dom = new JSDOM(html, { runScripts: "outside-only", url: "http://localhost/" });
  const w = dom.window;
  w.localStorage.setItem("simple", simple ? "1" : "0");
  const errors = [];
  const alerts = [];
  w.addEventListener("error", (e) => errors.push(e.message));
  w.alert = (m) => alerts.push(m);
  w.fetch = async () => ({ json: async () => {
    const x = JSON.parse(DATA);
    if (edit) edit(x);
    return x;
  } });
  for (const f of ["stats.js", "scoring.js", "app.js"]) w.eval(fs.readFileSync(path.join(WEB, f), "utf8"));
  await new Promise((r) => setTimeout(r, 50));
  const doc = w.document;
  const q = (s) => doc.querySelector(s);
  const qa = (s) => [...doc.querySelectorAll(s)];
  const click = (el) => {
    if (!el) throw new Error("누를 요소가 없음");
    el.dispatchEvent(new w.MouseEvent("click", { bubbles: true }));
  };
  const text = (s) => (q(s) ? q(s).textContent.replace(/\s+/g, " ").trim() : "");
  const pickChamp = (id) => click(qa(".champ").find((e) => e.dataset.id === id));
  const augRow = (id) => qa(".aug").find((e) => e.dataset.id === id);
  const itemRow = (name) => qa("#item-list .item").find((e) => e.textContent.includes(name));
  return { w, doc, q, qa, click, text, errors, alerts, pickChamp, augRow, itemRow, data: JSON.parse(DATA) };
}

module.exports = { loadPage, WEB, DATA };
