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
    const r = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text, metadata: { language: currentLang } }),
    });
    const resp = await r.json();
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

applyLang("ru");
loadStatus();
loadAgents();
