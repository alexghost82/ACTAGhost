const $ = (sel) => document.querySelector(sel);
const messagesEl = $("#messages");
const form = $("#composer");
const input = $("#input");
const sendBtn = $("#send");

let lastResponse = null;
let currentLang = "ru";

const UI = {
  ru: { placeholder: "Например: Исследуй кэширование в Postgres и набросай план внедрения", send: "Отправить", thinking: "ACTA GHOST думает…", you: "Я", err: "Ошибка" },
  he: { placeholder: "לדוגמה: חקור שיטות מטמון ב-Postgres והכן תוכנית יישום", send: "שלח", thinking: "ACTA GHOST חושבת…", you: "אני", err: "שגיאה" },
  en: { placeholder: "e.g.: Research caching best practices in Postgres and draft a plan", send: "Send", thinking: "ACTA GHOST is thinking…", you: "You", err: "Error" },
};

function applyLang(lang) {
  currentLang = lang;
  const ui = UI[lang] || UI.ru;
  input.placeholder = ui.placeholder;
  sendBtn.textContent = ui.send;
  document.body.classList.toggle("rtl", lang === "he");
  document.documentElement.lang = lang;
  document.documentElement.dir = lang === "he" ? "rtl" : "ltr";
  document.querySelectorAll(".lang").forEach((b) => b.classList.toggle("active", b.dataset.lang === lang));
}

function el(tag, cls, html) {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (html !== undefined) e.innerHTML = html;
  return e;
}

function addMessage(role, text) {
  const msg = el("div", `message ${role}`);
  const you = (UI[currentLang] || UI.ru).you;
  if (role === "user") {
    msg.appendChild(el("div", "avatar", you));
  } else {
    const av = document.createElement("img");
    av.className = "avatar";
    av.src = "/static/logo.png";
    av.alt = "ACTA GHOST";
    msg.appendChild(av);
  }
  const bubble = el("div", "bubble");
  bubble.appendChild(el("p", null, escapeHtml(text)));
  msg.appendChild(bubble);
  messagesEl.appendChild(msg);
  messagesEl.scrollTop = messagesEl.scrollHeight;
  return bubble;
}

function escapeHtml(s) {
  return (s ?? "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}

function parseSseBlock(block) {
  const lines = block.split("\n");
  let event = "message";
  const dataLines = [];
  for (const line of lines) {
    if (line.startsWith("event:")) {
      event = line.slice(6).trim();
      continue;
    }
    if (line.startsWith("data:")) {
      dataLines.push(line.slice(5).trimStart());
    }
  }
  if (!dataLines.length) return null;
  try {
    return { event, data: JSON.parse(dataLines.join("\n")) };
  } catch (_err) {
    return null;
  }
}

async function requestChatFallback(text) {
  const r = await fetch("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, metadata: { language: currentLang } }),
  });
  return r.json();
}

async function requestChatStream(text, thinking) {
  const r = await fetch("/api/chat/stream", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, metadata: { language: currentLang } }),
  });
  if (!r.ok || !r.body) {
    throw new Error(`stream unavailable: ${r.status}`);
  }
  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let answer = "";
  let response = null;
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const events = buffer.split("\n\n");
    buffer = events.pop() || "";
    for (const raw of events) {
      const parsed = parseSseBlock(raw.trim());
      if (!parsed) continue;
      if (parsed.event === "answer_delta") {
        answer += String(parsed.data.delta || "");
        thinking.innerHTML = `<p>${escapeHtml(answer)}</p>`;
      } else if (parsed.event === "complete") {
        response = parsed.data;
      }
    }
  }
  if (response) return response;
  if (answer) return { answer, trace: [] };
  throw new Error("empty stream response");
}

async function loadStatus() {
  try {
    const r = await fetch("/api/status");
    const s = await r.json();
    $("#st-providers").textContent = (s.providers || []).join(", ") || "—";
    const mem = Object.entries(s.memory || {}).map(([k, v]) => `${k}:${v}`).join(" ") || "пусто";
    $("#st-memory").textContent = mem;
    $("#st-kg").textContent = `${s.knowledge_graph.entities}/${s.knowledge_graph.relations}`;
    $("#st-conn").textContent = (s.connectors || []).join(", ");
  } catch (e) { /* ignore */ }
}

async function loadAgents() {
  try {
    const r = await fetch("/api/agents");
    const data = await r.json();
    $("#agent-count").textContent = data.count;
    const list = $("#agents");
    list.innerHTML = "";
    for (const a of data.agents) {
      const item = el("div", "agent");
      item.appendChild(el("div", "name", a.name.replace(/_/g, " ")));
      item.appendChild(el("div", "desc", escapeHtml(a.sub_prompt)));
      const caps = el("div", "caps");
      (a.capabilities || []).forEach((c) => caps.appendChild(el("span", "cap", c)));
      item.appendChild(caps);
      list.appendChild(item);
    }
  } catch (e) { /* ignore */ }
}

function renderInspector(resp) {
  // Trace
  const trace = $("#tab-trace");
  trace.innerHTML = "";
  (resp.trace || []).forEach((t) => {
    const step = el("div", `trace-step ${t.ok ? "" : "fail"}`);
    step.appendChild(el("div", "dot"));
    const body = el("div", "body");
    body.appendChild(el("div", "agent", `${t.step}. ${t.agent.replace(/_/g, " ")} <span class="ms">${t.duration_ms}ms</span>`));
    body.appendChild(el("div", "summary", escapeHtml(t.summary)));
    step.appendChild(body);
    trace.appendChild(step);
  });

  // Plan
  const plan = $("#tab-plan");
  plan.innerHTML = "";
  const tasks = (resp.plan && resp.plan.tasks) || [];
  if (!tasks.length) plan.appendChild(el("div", "empty", "Плана нет."));
  tasks.forEach((t) => {
    const card = el("div", "task");
    card.appendChild(el("div", "t-title", `${escapeHtml(t.title)} <span class="t-agent">@${t.agent}</span>`));
    card.appendChild(el("div", "t-desc", escapeHtml(t.description)));
    if (t.status) card.appendChild(el("div", "t-agent", `статус: ${t.status}`));
    plan.appendChild(card);
  });

  // Intent
  const intent = $("#tab-intent");
  intent.innerHTML = "";
  const i = resp.intent || {};
  const rows = [
    ["Тип", i.type], ["Уверенность", i.confidence],
    ["Резюме", i.summary], ["Цели", (i.objectives || []).join("; ")],
    ["Сущности", (i.entities || []).join(", ")], ["Внешнее", i.requires_external ? "да" : "нет"],
    ["Стратегия", resp.strategy ? resp.strategy.name : "—"],
  ];
  rows.forEach(([k, v]) => {
    const kv = el("div", "kv");
    kv.innerHTML = `<span class="k">${k}:</span> ${escapeHtml(String(v ?? "—"))}`;
    intent.appendChild(kv);
  });
}

async function loadVision() {
  try {
    const r = await fetch("/api/vision/status");
    if (!r.ok) return;
    const s = await r.json();
    $("#vision-state").textContent = s.enabled
      ? `${s.vlm_provider}/${s.quantization}`
      : "выкл";
    await loadCameras();
  } catch (e) { /* ignore */ }
}

async function loadCameras() {
  try {
    const r = await fetch("/api/cameras");
    if (!r.ok) return;
    const data = await r.json();
    const list = $("#cameras");
    list.innerHTML = "";
    if (!data.count) list.appendChild(el("div", "empty", "Камер нет."));
    for (const c of data.cameras) {
      const item = el("div", "camera");
      item.appendChild(el("div", "name", `${escapeHtml(c.name)} · ${c.sensor_type} ${c.width}×${c.height}`));
      const btn = el("button", "cam-analyze", "Анализ");
      btn.addEventListener("click", () => analyzeCamera(c.id));
      item.appendChild(btn);
      list.appendChild(item);
    }
  } catch (e) { /* ignore */ }
}

async function addCamera() {
  const name = $("#cam-name").value.trim() || "camera";
  const sensor = $("#cam-sensor").value;
  try {
    await fetch("/api/cameras", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, sensor_type: sensor }),
    });
    $("#cam-name").value = "";
    await loadCameras();
  } catch (e) { /* ignore */ }
}

async function analyzeCamera(cameraId) {
  const out = $("#vision-result");
  out.textContent = "…";
  try {
    const r = await fetch("/api/vision/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ camera_id: cameraId }),
    });
    const data = await r.json();
    if (!r.ok) { out.textContent = data.detail || "ошибка"; return; }
    const a = data.analysis;
    out.innerHTML = "";
    out.appendChild(el("div", "vr-text", escapeHtml(a.analysis.text)));
    out.appendChild(el("div", "vr-meta",
      `tiles: ${a.patch_plan.tile_count} · tokens: ${a.visual_tokens} · provider: ${escapeHtml(a.analysis.provider)}`));
  } catch (e) {
    out.textContent = String(e);
  }
}

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".tab-content").forEach((c) => c.classList.remove("active"));
    tab.classList.add("active");
    $(`#tab-${tab.dataset.tab}`).classList.add("active");
  });
});

form.addEventListener("submit", async (e) => {
  e.preventDefault();
  const text = input.value.trim();
  if (!text) return;
  addMessage("user", text);
  input.value = "";
  sendBtn.disabled = true;
  const thinking = addMessage("assistant", (UI[currentLang] || UI.ru).thinking);
  thinking.classList.add("thinking");

  try {
    let resp;
    try {
      resp = await requestChatStream(text, thinking);
    } catch (_streamErr) {
      resp = await requestChatFallback(text);
    }
    lastResponse = resp;
    thinking.classList.remove("thinking");
    thinking.innerHTML = "";
    thinking.appendChild(el("p", null, escapeHtml(resp.answer || "(пустой ответ)")));
    renderInspector(resp);
    loadStatus();
  } catch (err) {
    thinking.classList.remove("thinking");
    const errLabel = (UI[currentLang] || UI.ru).err;
    thinking.innerHTML = `<p style="color:var(--err)">${errLabel}: ${escapeHtml(String(err))}</p>`;
  } finally {
    sendBtn.disabled = false;
    input.focus();
  }
});

document.querySelectorAll(".lang").forEach((b) => {
  b.addEventListener("click", () => applyLang(b.dataset.lang));
});

const camAddBtn = $("#cam-add");
if (camAddBtn) camAddBtn.addEventListener("click", addCamera);

applyLang("ru");
loadStatus();
loadAgents();
loadVision();
