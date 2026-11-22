// Local AI Core web UI. It uses only the public /v1 API, so every click is also an API test.
"use strict";

const T = {
  en: {
    tagline: "AI on your own GPU. Nothing leaves this machine.", api_docs: "API docs",
    gpu: "GPU", in_memory: "In memory", queue: "Queue", engine: "Engine",
    tab_ask: "Ask", tab_search: "Search", tab_services: "Services",
    ask_ph: "Ask a question about your notes…", ask_go: "Ask",
    ask_hint: "Answers come only from your notes, and every source is checked.",
    sources: "Sources", search_ph: "Search by meaning, not by exact words…", search_go: "Search",
    search_hint: "Your notes folder is indexed at startup and kept up to date.", reindex: "Rebuild index",
    key_title: "API key", key_text: "This gateway asks for a key (LOCAL_AI_API_KEY).", key_save: "Save",
    no_gpu: "not visible", nothing: "nothing", idle: "idle", waiting: "waiting",
    running: "running", reachable: "ready", unreachable: "not reachable",
    st_starting: "Starting…", st_waiting_ollama: "Waiting for the engine (Ollama)…",
    st_pulling: "Downloading model {m}", st_indexing: "Indexing your notes…", st_error: "Startup problem",
    thinking: "Searching your notes…", not_grounded: "No source backs this answer: treat it with care.",
    invalid: "Cited numbers that match no excerpt: {n}. Treat the answer with care.",
    truncated: "The answer was cut at the length limit.", no_results: "Nothing above the score threshold.",
    model_secs: "{model} · {n} excerpts read · {s} s", cited: "cited",
    st_ready: "ready", st_model_missing: "model missing", st_off: "off", st_ollama_unreachable: "engine down",
    model: "Model", vram: "VRAM", vram_val: "~{e} GB estimated · {u} GB in use", license: "License",
    last_test: "Last test", never: "never", passed: "passed", failed: "failed", run_test: "Run test",
    testing: "Testing…", profile: "Profile", indexing: "Indexing… {d}/{t}", indexed: "Index: {f} files, {c} chunks ({ch} re-read)",
    error: "Error",
  },
  it: {
    tagline: "AI sulla tua GPU. Niente esce da questa macchina.", api_docs: "Documentazione API",
    gpu: "GPU", in_memory: "In memoria", queue: "Coda", engine: "Motore",
    tab_ask: "Chiedi", tab_search: "Cerca", tab_services: "Servizi",
    ask_ph: "Fai una domanda sulle tue note…", ask_go: "Chiedi",
    ask_hint: "Le risposte vengono solo dalle tue note, e ogni fonte è controllata.",
    sources: "Fonti", search_ph: "Cerca per significato, non per parole esatte…", search_go: "Cerca",
    search_hint: "La cartella delle note si indicizza all'avvio e resta aggiornata.", reindex: "Rifai l'indice",
    key_title: "Chiave API", key_text: "Questo gateway chiede una chiave (LOCAL_AI_API_KEY).", key_save: "Salva",
    no_gpu: "non visibile", nothing: "niente", idle: "libera", waiting: "in attesa",
    running: "in corso", reachable: "pronto", unreachable: "non raggiungibile",
    st_starting: "Avvio…", st_waiting_ollama: "Aspetto il motore (Ollama)…",
    st_pulling: "Scarico il modello {m}", st_indexing: "Indicizzo le note…", st_error: "Problema all'avvio",
    thinking: "Cerco nelle note…", not_grounded: "Nessuna fonte sostiene questa risposta: prendila con cautela.",
    invalid: "Citati numeri che non corrispondono a nessun estratto: {n}. Prendi la risposta con cautela.",
    truncated: "La risposta è stata tagliata al limite di lunghezza.", no_results: "Niente sopra la soglia.",
    model_secs: "{model} · {n} estratti letti · {s} s", cited: "citato",
    st_ready: "pronto", st_model_missing: "modello assente", st_off: "spento", st_ollama_unreachable: "motore giù",
    model: "Modello", vram: "VRAM", vram_val: "~{e} GB stimati · {u} GB in uso", license: "Licenza",
    last_test: "Ultimo collaudo", never: "mai", passed: "superato", failed: "fallito", run_test: "Collauda",
    testing: "Collaudo…", profile: "Profilo", indexing: "Indicizzo… {d}/{t}", indexed: "Indice: {f} file, {c} pezzi ({ch} riletti)",
    error: "Errore",
  },
};

const store = {
  get(k) { try { return localStorage.getItem(k); } catch { return null; } },
  set(k, v) { try { localStorage.setItem(k, v); } catch { /* private mode: fine */ } },
};
let lang = store.get("lang") || ((navigator.language || "en").startsWith("it") ? "it" : "en");
const $ = (s) => document.querySelector(s);
const t = (k, vars = {}) => (T[lang][k] ?? T.en[k] ?? k).replace(/\{(\w+)\}/g, (_, v) => vars[v] ?? "");
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

// ------------------------------------------------------------------------------------------- API

async function api(path, opts = {}) {
  const headers = { ...(opts.headers || {}) };
  const key = store.get("apiKey");
  if (key) headers["X-API-Key"] = key;
  if (opts.json !== undefined) { headers["Content-Type"] = "application/json"; opts.body = JSON.stringify(opts.json); }
  const r = await fetch(path, { ...opts, headers });
  if (r.status === 401) { await askKey(); return api(path, opts); }
  return r;
}
async function apiJSON(path, opts) {
  const r = await api(path, opts);
  const body = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(typeof body.detail === "string" ? body.detail : `HTTP ${r.status}`);
  return body;
}
function askKey() {
  return new Promise((resolve) => {
    const d = $("#key-dialog");
    if (d.open) { d.addEventListener("close", resolve, { once: true }); return; }
    d.addEventListener("close", () => { store.set("apiKey", $("#key-input").value.trim()); resolve(); }, { once: true });
    d.showModal();
  });
}

// Follows a job's Server-Sent Events with fetch (EventSource cannot send the API key header).
async function follow(jobId, onEvent) {
  let after = 0;
  for (let attempt = 0; attempt < 5; attempt++) {
    try {
      const r = await api(`/v1/jobs/${jobId}/events?after=${after}`);
      if (!r.ok) throw new Error(`HTTP ${r.status}`);
      const reader = r.body.getReader();
      const dec = new TextDecoder();
      let buf = "";
      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf("\n\n")) >= 0) {
          const block = buf.slice(0, i); buf = buf.slice(i + 2);
          const ev = {};
          for (const line of block.split("\n")) {
            const j = line.indexOf(": ");
            if (j > 0) ev[line.slice(0, j)] = line.slice(j + 2);
          }
          after = Number(ev.id) || after;
          onEvent(ev.event, JSON.parse(ev.data));
        }
      }
      return;
    } catch (e) {
      await new Promise((res) => setTimeout(res, 800 * (attempt + 1)));
    }
  }
}

// ------------------------------------------------------------------------------------- status

function fmtStartup(s) {
  const title = { starting: t("st_starting"), waiting_ollama: t("st_waiting_ollama"), indexing: t("st_indexing"),
    error: t("st_error"), pulling: t("st_pulling", { m: s.pull?.model || s.detail }) }[s.phase] || s.phase;
  let detail = s.phase === "error" ? `${s.error} — ${s.detail}` : "";
  let pct = null;
  if (s.phase === "pulling" && s.pull?.total) {
    pct = (100 * (s.pull.completed || 0)) / s.pull.total;
    detail = `${s.pull.status} · ${(s.pull.completed / 2 ** 30 || 0).toFixed(1)} / ${(s.pull.total / 2 ** 30).toFixed(1)} GB`;
  } else if (s.phase === "pulling") detail = s.pull?.status || "";
  return { title, detail, pct };
}

async function refreshStatus() {
  let st;
  try { st = await apiJSON("/v1/status"); } catch { $("#st-ollama").textContent = t("unreachable"); return; }
  $("#version").textContent = `v${st.version}`;
  const g = st.gpu;
  $("#st-gpu").textContent = g ? g.name.replace(/^NVIDIA (GeForce )?/, "") : t("no_gpu");
  $("#st-vram-bar").hidden = !g;
  if (g) {
    const p = (100 * g.vram_used_gb) / g.vram_total_gb;
    $("#st-vram-bar i").style.width = `${p}%`;
    $("#st-vram-bar").classList.toggle("hot", p > 85);
    $("#st-vram").textContent = `${g.vram_used_gb} / ${g.vram_total_gb} GB · ${g.utilization}%`;
  }
  const loaded = st.ollama.loaded || [];
  $("#st-loaded").textContent = loaded.length ? loaded.map((m) => `${m.name} (${m.vram_gb} GB)`).join(", ") : t("nothing");
  const q = st.queue;
  $("#st-queue").textContent = q.running ? `${t("running")}: ${q.running.kind}${q.waiting ? ` · ${q.waiting} ${t("waiting")}` : ""}` : t("idle");
  $("#st-ollama").textContent = st.ollama.reachable ? `Ollama ${st.ollama.version}` : t("unreachable");
  $("#st-ollama").classList.toggle("ko", !st.ollama.reachable);

  const s = st.startup, banner = $("#startup");
  banner.hidden = s.phase === "ready";
  if (!banner.hidden) {
    const f = fmtStartup(s);
    banner.classList.toggle("error", s.phase === "error");
    $("#startup-title").textContent = f.title;
    $("#startup-detail").textContent = f.detail;
    $("#startup-bar").hidden = f.pct === null;
    if (f.pct !== null) $("#startup-bar i").style.width = `${f.pct}%`;
  }
}

// ---------------------------------------------------------------------------------------- ask

// A chunk of Markdown as readable text: hard-wrapped lines joined, blank lines as paragraphs, lists kept.
function excerptHTML(md) {
  const inline = (s) => esc(s).replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>").replace(/`([^`]+)`/g, "<code>$1</code>");
  return md.split(/\n\s*\n/).map((block) => {
    const lines = block.split("\n");
    if (lines.every((l) => /^\s*[-*]\s+/.test(l))) return `<ul>${lines.map((l) => `<li>${inline(l.replace(/^\s*[-*]\s+/, ""))}</li>`).join("")}</ul>`;
    return `<p>${inline(lines.join(" "))}</p>`;
  }).join("");
}

function renderAnswer(text, nSources, invalid = []) {
  const bad = new Set(invalid);
  const inline = (s) => esc(s)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/\[(\d+(?:\s*[,;]\s*\d+)*)\]/g, (_, grp) => grp.split(/\s*[,;]\s*/).map((k) => {
      const n = Number(k);
      return n >= 1 && n <= nSources && !bad.has(n) ? `<a class="cite" href="#src-${n}">${n}</a>` : `<span class="cite bad">${n}</span>`;
    }).join(""));
  const out = []; let list = [];
  const flush = () => { if (list.length) { out.push(`<ul>${list.map((x) => `<li>${inline(x)}</li>`).join("")}</ul>`); list = []; } };
  for (const line of text.split("\n")) {
    const m = line.match(/^\s*[-*]\s+(.*)/);
    if (m) list.push(m[1]);
    else { flush(); if (line.trim()) out.push(`<p>${inline(line)}</p>`); }
  }
  flush();
  return out.join("");
}

function renderSources(sources, cited = []) {
  const c = new Set(cited.map((x) => x.n));
  $("#ask-sources").innerHTML = sources.map((s, i) => `
    <li class="src${c.has(i + 1) ? " cited" : ""}" id="src-${i + 1}">
      <div class="where"><b>[${i + 1}]</b><span>${esc(s.file)}:${s.line}</span>
        <span>${esc((s.heads || []).slice(-2).join(" › "))}</span><span class="score">${s.score.toFixed(2)}${c.has(i + 1) ? ` · ${t("cited")}` : ""}</span></div>
      <div class="excerpt">${excerptHTML(s.text)}</div>
    </li>`).join("");
}

$("#ask-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = $("#ask-q").value.trim();
  if (!q) return;
  const go = $("#ask-go"), text = $("#ask-text"), notice = $("#ask-notice");
  go.disabled = true;
  $("#ask-out").hidden = false;
  text.textContent = t("thinking"); text.classList.add("streaming");
  notice.hidden = true; $("#ask-meta").textContent = ""; $("#ask-sources").innerHTML = "";
  let answer = "", sources = [];
  try {
    const job = await apiJSON("/v1/ask", { method: "POST", json: { question: q } });
    await follow(job.id, (type, data) => {
      if (type === "sources") { sources = data; renderSources(sources); }
      else if (type === "token") { answer += data; text.innerHTML = renderAnswer(answer, sources.length); }
      else if (type === "result") {
        text.innerHTML = renderAnswer(data.answer, data.sources, data.invalid);
        renderSources(data.excerpts || sources, data.cited);
        const notes = [];
        if (data.invalid.length) notes.push(t("invalid", { n: data.invalid.join(", ") }));
        else if (data.sources && !data.cited.length) notes.push(t("not_grounded"));
        if (data.truncated) notes.push(t("truncated"));
        notice.hidden = !notes.length; notice.textContent = notes.join(" ");
        notice.classList.toggle("bad", !!data.invalid.length);
        $("#ask-meta").textContent = t("model_secs", { model: data.model, n: data.sources, s: data.secs });
      } else if (type === "error") { text.textContent = `${t("error")}: ${data}`; }
    });
  } catch (err) {
    text.textContent = `${t("error")}: ${err.message}`;
  } finally {
    text.classList.remove("streaming"); go.disabled = false;
  }
});
$("#ask-q").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#ask-form").requestSubmit(); }
});

// ------------------------------------------------------------------------------------- search

$("#search-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const q = $("#search-q").value.trim();
  const out = $("#search-out");
  try {
    const { results } = await apiJSON(`/v1/search?q=${encodeURIComponent(q)}&n=8`);
    out.innerHTML = results.length ? results.map((r) => `
      <li class="hit"><div class="where"><b>${esc(r.file)}:${r.line}</b><span>${esc(r.heads.slice(-2).join(" › "))}</span>
        <span class="score">${r.score.toFixed(2)}</span></div><div class="excerpt">${excerptHTML(r.text)}</div></li>`).join("")
      : `<li class="hint">${t("no_results")}</li>`;
  } catch (err) { out.innerHTML = `<li class="notice bad">${esc(err.message)}</li>`; }
});

$("#reindex").addEventListener("click", async () => {
  const btn = $("#reindex"), st = $("#index-state");
  btn.disabled = true;
  try {
    const job = await apiJSON("/v1/search/index", { method: "POST", json: { rebuild: true } });
    await follow(job.id, (type, data) => {
      if (type === "progress") st.textContent = t("indexing", { d: data.done, t: data.total });
      if (type === "result") st.textContent = t("indexed", { f: data.files, c: data.chunks, ch: data.changed });
      if (type === "error") st.textContent = `${t("error")}: ${data}`;
    });
  } catch (err) { st.textContent = `${t("error")}: ${err.message}`; }
  btn.disabled = false;
});

// ----------------------------------------------------------------------------------- services

const STATE_CLASS = { ready: "ready", model_missing: "warn", ollama_unreachable: "warn", off: "off" };

async function loadServices() {
  const box = $("#services");
  let list;
  try { list = (await apiJSON("/v1/services")).services; } catch (err) { box.innerHTML = `<p class="notice bad">${esc(err.message)}</p>`; return; }
  box.innerHTML = list.map((s) => {
    const lt = s.last_test;
    const test = lt ? `<span class="${lt.ok ? "ok" : "ko"}">${lt.ok ? "✓" : "✗"} ${t(lt.ok ? "passed" : "failed")}</span> · ${esc(lt.date)}` : t("never");
    return `<article class="card" data-name="${esc(s.name)}">
      <h2>${esc(s.title || s.name)} <span class="badge ${STATE_CLASS[s.state] || ""}">${esc(t("st_" + s.state))}</span></h2>
      ${s.description ? `<p>${esc(s.description)}</p>` : ""}${s.detail ? `<p>${esc(s.detail)}</p>` : ""}
      ${s.model ? `<dl class="facts">
        <dt>${t("model")}</dt><dd>${esc(s.model)}</dd>
        <dt>${t("vram")}</dt><dd>${t("vram_val", { e: s.vram_gb, u: s.vram_in_use_gb })}</dd>
        <dt>${t("license")}</dt><dd>${s.license_url ? `<a href="${esc(s.license_url)}" target="_blank" rel="noopener">${esc(s.license)}</a>` : esc(s.license)}</dd>
        <dt>${t("profile")}</dt><dd>${esc(s.profile)}</dd>
        <dt>${t("last_test")}</dt><dd>${test}</dd></dl>` : ""}
      ${s.state !== "off" ? `<div><button class="btn ghost small" data-test="${esc(s.name)}" type="button">${t("run_test")}</button></div><ul class="checks"></ul>` : ""}
    </article>`;
  }).join("");
}

$("#services").addEventListener("click", async (e) => {
  const btn = e.target.closest("[data-test]");
  if (!btn) return;
  const ul = btn.closest(".card").querySelector(".checks");
  btn.disabled = true; btn.textContent = t("testing"); ul.innerHTML = "";
  try {
    const job = await apiJSON(`/v1/services/${encodeURIComponent(btn.dataset.test)}/test`, { method: "POST" });
    await follow(job.id, (type, data) => {
      if (type === "check") ul.insertAdjacentHTML("beforeend",
        `<li><span class="${data.ok ? "ok" : "ko"}">${data.ok ? "✓" : "✗"}</span><span>${esc(data.name)}</span><span class="d">${esc(data.detail)}</span></li>`);
      if (type === "error") ul.insertAdjacentHTML("beforeend", `<li><span class="ko">✗</span><span>${esc(data)}</span></li>`);
    });
  } catch (err) { ul.innerHTML = `<li><span class="ko">✗</span><span>${esc(err.message)}</span></li>`; }
  const checks = ul.innerHTML;
  await loadServices();
  const card = document.querySelector(`.card[data-name="${CSS.escape(btn.dataset.test)}"] .checks`);
  if (card) card.innerHTML = checks;
});

// --------------------------------------------------------------------------------- tabs, i18n

function showTab(name) {
  document.querySelectorAll(".tab").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
  document.querySelectorAll(".panel").forEach((p) => { p.hidden = p.id !== `panel-${name}`; });
  store.set("tab", name);
  if (name === "services") loadServices();
}
document.querySelectorAll(".tab").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));

function applyLang() {
  document.documentElement.lang = lang;
  document.querySelectorAll("[data-i18n]").forEach((el) => { el.textContent = t(el.dataset.i18n); });
  document.querySelectorAll("[data-i18n-ph]").forEach((el) => { el.placeholder = t(el.dataset.i18nPh); });
  $("#lang").textContent = lang === "en" ? "IT" : "EN";
}
$("#lang").addEventListener("click", () => {
  lang = lang === "en" ? "it" : "en"; store.set("lang", lang); applyLang(); refreshStatus();
  if (!$("#panel-services").hidden) loadServices();
});

const THEMES = ["auto", "light", "dark"];
function applyTheme(th) {
  if (th === "auto") delete document.documentElement.dataset.theme; else document.documentElement.dataset.theme = th;
  $("#theme").textContent = { auto: "◐", light: "☀", dark: "☾" }[th];
  $("#theme").title = th;
}
let theme = store.get("theme") || "auto";
$("#theme").addEventListener("click", () => { theme = THEMES[(THEMES.indexOf(theme) + 1) % 3]; store.set("theme", theme); applyTheme(theme); });

// The page can be opened on a given tab and theme (used by the screenshot script): /?tab=search&theme=dark
const params = new URLSearchParams(location.search);
if (params.get("theme")) theme = params.get("theme");
if (params.get("lang")) lang = params.get("lang");
applyTheme(theme);
applyLang();
showTab(params.get("tab") || store.get("tab") || "ask");
refreshStatus();
setInterval(refreshStatus, 3000);
