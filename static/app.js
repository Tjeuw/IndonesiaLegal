/* =====================================================
   INDONESIA LAW AI — app.js
   ===================================================== */

let currentConversationId = null;
let isStreaming = false;
let currentLang = localStorage.getItem("lang") || "en";
let pendingFiles = [];

const translations = {
  en: {
    title: "Indonesia Law AI",
    welcome: "Indonesia Law AI",
    subtitle: "Your legal research assistant for Indonesian business, investment, and corporate law.",
    placeholder: "Ask your legal question about Indonesia...",
    chip1: "Setting Up a PT PMA",
    chip1q: "What are the steps to establish a PT PMA (foreign-invested company) in Indonesia?",
    chip2: "Foreign Investment Restrictions",
    chip2q: "What sectors are restricted or closed to foreign investment under the current regulations?",
    chip3: "Employment Law",
    chip3q: "What are the key employment law obligations for foreign companies operating in Indonesia?",
    newChat: "New Chat",
    you: "You",
    assistant: "Legal Assistant",
    darkMode: "Dark Mode",
    lightMode: "Light Mode",
    deleteTitle: "Delete",
    attachTitle: "Attach document",
    processing: "Processing...",
    uploaded: "Uploaded",
    sources: "Sources Referenced",
    thinking: "Thinking...",
  },
  zh: {
    title: "印尼法律AI",
    welcome: "印尼法律AI",
    subtitle: "您的印尼商业、投资和公司法法律研究助手。",
    placeholder: "请输入您关于印尼法律的问题...",
    chip1: "设立PT PMA",
    chip1q: "在印尼设立PT PMA（外资公司）的步骤是什么？",
    chip2: "外资限制",
    chip2q: "根据现行法规，哪些行业限制或禁止外国投资？",
    chip3: "劳动法",
    chip3q: "在印尼经营的外国公司有哪些主要的劳动法义务？",
    newChat: "新对话",
    you: "您",
    assistant: "法律助手",
    darkMode: "深色模式",
    lightMode: "浅色模式",
    deleteTitle: "删除",
    attachTitle: "附加文件",
    processing: "处理中...",
    uploaded: "已上传",
    sources: "参考来源",
    thinking: "思考中...",
  },
  id: {
    title: "Indonesia Law AI",
    welcome: "Indonesia Law AI",
    subtitle: "Asisten riset hukum untuk bisnis, investasi, dan hukum perusahaan Indonesia.",
    placeholder: "Ketik pertanyaan hukum Anda tentang Indonesia...",
    chip1: "Mendirikan PT PMA",
    chip1q: "Apa saja langkah-langkah untuk mendirikan PT PMA di Indonesia?",
    chip2: "Pembatasan Investasi Asing",
    chip2q: "Sektor apa saja yang dibatasi atau ditutup untuk investasi asing?",
    chip3: "Hukum Ketenagakerjaan",
    chip3q: "Apa saja kewajiban hukum ketenagakerjaan utama bagi perusahaan asing di Indonesia?",
    newChat: "Percakapan Baru",
    you: "Anda",
    assistant: "Asisten Hukum",
    darkMode: "Mode Gelap",
    lightMode: "Mode Terang",
    deleteTitle: "Hapus",
    attachTitle: "Lampirkan dokumen",
    processing: "Memproses...",
    uploaded: "Berhasil diunggah",
    sources: "Sumber Referensi",
    thinking: "Sedang berpikir...",
  }
};

function t(key) {
  return translations[currentLang]?.[key] || translations.en[key] || key;
}

function setLanguage(lang) {
  currentLang = lang;
  localStorage.setItem("lang", lang);
  document.querySelectorAll(".lang-btn").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.lang === lang);
  });
  applyTranslations();
}

function applyTranslations() {
  document.getElementById("chat-title").textContent = t("title");
  const wt = document.getElementById("welcome-title");
  const ws = document.getElementById("welcome-subtitle");
  if (wt) wt.textContent = t("welcome");
  if (ws) ws.textContent = t("subtitle");
  document.getElementById("message-input").placeholder = t("placeholder");

  const chip1 = document.getElementById("chip1");
  const chip2 = document.getElementById("chip2");
  const chip3 = document.getElementById("chip3");
  if (chip1) { chip1.textContent = t("chip1"); chip1.onclick = () => useSuggestion(t("chip1q")); }
  if (chip2) { chip2.textContent = t("chip2"); chip2.onclick = () => useSuggestion(t("chip2q")); }
  if (chip3) { chip3.textContent = t("chip3"); chip3.onclick = () => useSuggestion(t("chip3q")); }

  const isDark = document.body.classList.contains("dark");
  const label = document.getElementById("theme-label");
  if (label) label.textContent = isDark ? t("lightMode") : t("darkMode");

  const attachBtn = document.getElementById("attach-btn");
  if (attachBtn) attachBtn.title = t("attachTitle");
}

marked.setOptions({ breaks: true, gfm: true });

/* ─── Theme ─── */
function initTheme() {
  const saved = localStorage.getItem("theme");
  if (saved === "dark" || (!saved && window.matchMedia("(prefers-color-scheme: dark)").matches)) {
    document.body.classList.add("dark");
    updateThemeUI(true);
  }
}

function toggleTheme() {
  const isDark = document.body.classList.toggle("dark");
  localStorage.setItem("theme", isDark ? "dark" : "light");
  updateThemeUI(isDark);
}

function updateThemeUI(isDark) {
  const sun = document.getElementById("theme-icon-sun");
  const moon = document.getElementById("theme-icon-moon");
  const label = document.getElementById("theme-label");
  if (sun) sun.style.display = isDark ? "none" : "block";
  if (moon) moon.style.display = isDark ? "block" : "none";
  if (label) label.textContent = isDark ? t("lightMode") : t("darkMode");
}

/* ─── Conversations ─── */
async function loadConversations() {
  const res = await fetch("/api/conversations");
  const conversations = await res.json();
  const list = document.getElementById("conversations-list");
  list.innerHTML = "";
  conversations.forEach(conv => {
    const item = document.createElement("div");
    item.className = "conv-item" + (conv.id === currentConversationId ? " active" : "");
    item.innerHTML = `
      <span class="conv-item-title">${escapeHtml(conv.title)}</span>
      <button class="delete-btn" title="${t("deleteTitle")}">
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polyline points="3 6 5 6 21 6"/>
          <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
        </svg>
      </button>
    `;
    item.querySelector(".conv-item-title").addEventListener("click", () => selectConversation(conv.id, conv.title));
    item.querySelector(".delete-btn").addEventListener("click", e => { e.stopPropagation(); deleteConversation(conv.id); });
    list.appendChild(item);
  });
}

async function selectConversation(id, title) {
  currentConversationId = id;
  document.getElementById("chat-title").textContent = title || t("title");
  document.getElementById("welcome-screen").style.display = "none";
  document.getElementById("messages").style.display = "block";
  clearPendingFiles();
  loadConversations();
  await loadMessages(id);
  updateMessageCounter(id);
  closeSidebar();
}

async function loadMessages(convId) {
  const res = await fetch(`/api/conversations/${convId}/messages`);
  const messages = await res.json();
  const container = document.getElementById("messages");
  container.innerHTML = "";
  messages.forEach(msg => appendMessage(msg.role, msg.content, false, msg.sources || []));
  scrollToBottom();
}

async function createNewChat() {
  const res = await fetch("/api/conversations", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ title: t("newChat") })
  });
  const conv = await res.json();
  currentConversationId = conv.id;
  document.getElementById("chat-title").textContent = conv.title || t("newChat");
  document.getElementById("welcome-screen").style.display = "none";
  document.getElementById("messages").style.display = "block";
  document.getElementById("messages").innerHTML = "";
  await loadConversations();
  closeSidebar();
}

async function deleteConversation(id) {
  await fetch(`/api/conversations/${id}`, { method: "DELETE" });
  if (currentConversationId === id) {
    currentConversationId = null;
    document.getElementById("chat-title").textContent = t("title");
    document.getElementById("welcome-screen").style.display = "flex";
    document.getElementById("messages").style.display = "none";
    document.getElementById("messages").innerHTML = "";
  }
  await loadConversations();
}

/* ─── Messages ─── */
function appendMessage(role, content, streaming = false, sources = []) {
  const container = document.getElementById("messages");
  const div = document.createElement("div");
  div.className = `message ${role}`;

  const avatarText = role === "user" ? "U" : "AI";
  const roleLabel = role === "user" ? t("you") : t("assistant");

  let sourcesHtml = "";
  if (sources && sources.length > 0 && role === "assistant") {
    sourcesHtml = buildSourcesHtml(sources);
  }

  div.innerHTML = `
    <div class="message-avatar">${avatarText}</div>
    <div class="message-content">
      <div class="message-role">${roleLabel}</div>
      <div class="message-text ${streaming ? 'loading' : ''}">${role === "user" ? escapeHtml(content) : (content ? parseCitations(marked.parse(content)) : "")}</div>
      ${sourcesHtml}
    </div>
  `;
  container.appendChild(div);
  scrollToBottom();
  return div;
}

function buildSourcesHtml(sources) {
  if (!sources || sources.length === 0) return "";
  const chips = sources.map(s => {
    const statusKey = s.status === 'berlaku' ? 'active' : s.status === 'diubah' ? 'amended' : s.status === 'dicabut' ? 'revoked' : '';
    const statusLabel = s.status === 'berlaku' ? 'Active' : s.status === 'diubah' ? 'Amended' : s.status === 'dicabut' ? 'Revoked' : '';
    const statusChip = statusKey ? `<span class="source-chip-status ${statusKey}">${statusLabel}</span>` : '';
    const nomor = s.nomor_tahun ? `<span>${escapeHtml(s.nomor_tahun)}</span>` : '';
    return `
      <div class="source-chip">
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
          <polyline points="14 2 14 8 20 8"/>
        </svg>
        <span class="source-chip-title">${escapeHtml(s.title)}</span>
        ${nomor}
        ${statusChip}
      </div>`;
  }).join("");
  return `
    <div class="message-sources">
      <div class="sources-label">${t("sources")}</div>
      <div class="source-chips">${chips}</div>
    </div>`;
}

function updateMessage(messageDiv, content, sources = []) {
  const textDiv = messageDiv.querySelector(".message-text");
  textDiv.innerHTML = parseCitations(marked.parse(content));
  textDiv.classList.remove("loading", "streaming-cursor");

  // Update sources
  const existing = messageDiv.querySelector(".message-sources");
  if (existing) existing.remove();
  if (sources && sources.length > 0) {
    messageDiv.querySelector(".message-content").insertAdjacentHTML("beforeend", buildSourcesHtml(sources));
  }
  scrollToBottom();
}

function setMessageLoading(messageDiv, loading) {
  const textDiv = messageDiv.querySelector(".message-text");
  if (loading) {
    textDiv.classList.add("loading");
    textDiv.innerHTML = "";
  } else {
    textDiv.classList.remove("loading");
  }
}

/* ─── File handling ─── */
function addPendingFiles(files) {
  for (const file of files) {
    if (!pendingFiles.some(f => f.name === file.name && f.size === file.size)) {
      pendingFiles.push(file);
    }
  }
  renderPendingFiles();
}

function removePendingFile(index) {
  pendingFiles.splice(index, 1);
  renderPendingFiles();
}

function clearPendingFiles() {
  pendingFiles = [];
  renderPendingFiles();
}

function renderPendingFiles() {
  const bar = document.getElementById("attached-files-bar");
  bar.innerHTML = "";
  pendingFiles.forEach((file, i) => {
    const chip = document.createElement("div");
    chip.className = "attached-file-chip";
    chip.innerHTML = `
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"/>
        <polyline points="14 2 14 8 20 8"/>
      </svg>
      <span class="attached-file-name">${escapeHtml(file.name)}</span>
      <button class="attached-file-remove" title="Remove">
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5">
          <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
        </svg>
      </button>
    `;
    chip.querySelector(".attached-file-remove").addEventListener("click", () => removePendingFile(i));
    bar.appendChild(chip);
  });
  updateSendButton();
}

async function uploadPendingFiles(convId) {
  if (pendingFiles.length === 0) return true;
  const progress = document.getElementById("upload-inline-progress");
  const fill = document.getElementById("upload-inline-fill");
  const text = document.getElementById("upload-inline-text");
  progress.style.display = "flex";
  document.getElementById("attached-files-bar").style.display = "none";

  let uploaded = 0, failed = 0;
  for (const file of pendingFiles) {
    text.textContent = `${t("processing")} ${file.name}...`;
    fill.style.width = `${(uploaded / pendingFiles.length) * 100}%`;
    const fd = new FormData();
    fd.append("file", file);
    fd.append("title", file.name.replace(/\.[^/.]+$/, ""));
    fd.append("doc_type", "general");
    try {
      const res = await fetch(`/api/conversations/${convId}/documents`, { method: "POST", body: fd });
      if (res.ok) uploaded++; else failed++;
    } catch (e) { failed++; }
    fill.style.width = `${((uploaded + failed) / pendingFiles.length) * 100}%`;
  }
  fill.style.width = "100%";
  text.textContent = failed === 0 ? t("uploaded") : `${uploaded} uploaded, ${failed} failed`;
  setTimeout(() => {
    progress.style.display = "none";
    document.getElementById("attached-files-bar").style.display = "flex";
    fill.style.width = "0%";
  }, 1200);
  clearPendingFiles();
  return failed === 0;
}

/* ─── Send message ─── */
async function sendMessage(content) {
  if ((!content.trim() && pendingFiles.length === 0) || isStreaming) return;
  if (!currentConversationId) await createNewChat();

  isStreaming = true;
  updateSendButton();

  // Ensure messages div is visible before appending
  document.getElementById("welcome-screen").style.display = "none";
  document.getElementById("messages").style.display = "block";

  if (pendingFiles.length > 0) await uploadPendingFiles(currentConversationId);

  if (!content.trim()) {
    isStreaming = false;
    updateSendButton();
    return;
  }

  appendMessage("user", content);
  const assistantDiv = appendMessage("assistant", "", true);

  try {
    const res = await fetch(`/api/conversations/${currentConversationId}/messages`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ content })
    });

    const data = await res.json();

    if (data.error === 'limit_reached') {
      updateMessage(assistantDiv, `*Demo limit reached: ${data.message} Please contact the team for full access.*`);
    } else if (data.error === 'demo_gate') {
      window.location.href = '/gate';
    } else if (data.error) {
      updateMessage(assistantDiv, `*Error: ${data.error}*`);
    } else {
      updateMessage(assistantDiv, data.content, data.sources || []);
    }

    updateMessageCounter(currentConversationId);
    if (data.content) {
      const firstLine = content.substring(0, 40);
      const newTitle = firstLine + (content.length > 40 ? "..." : "");
      await fetch(`/api/conversations/${currentConversationId}/title`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: newTitle })
      });
      document.getElementById("chat-title").textContent = newTitle;
      loadConversations();
    }
  } catch (err) {
    updateMessage(assistantDiv, "*An error occurred. Please try again.*");
  }

  isStreaming = false;
  updateSendButton();
}

/* ─── Helpers ─── */
function useSuggestion(text) {
  document.getElementById("message-input").value = text;
  updateSendButton();
  document.getElementById("message-form").dispatchEvent(new Event("submit"));
}

function scrollToBottom() {
  const container = document.getElementById("messages-container");
  container.scrollTop = container.scrollHeight;
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

function updateSendButton() {
  const input = document.getElementById("message-input");
  const btn = document.getElementById("send-btn");
  btn.disabled = (!input.value.trim() && pendingFiles.length === 0) || isStreaming;
}

function closeSidebar() {
  document.getElementById("sidebar").classList.remove("open");
  document.getElementById("sidebar-overlay").classList.remove("visible");
}

/* ─── Init ─── */
document.addEventListener("DOMContentLoaded", () => {
  // Inject citation side panel
  document.body.insertAdjacentHTML("beforeend", `
    <div id="citation-panel" class="citation-panel" aria-hidden="true">
      <div class="citation-panel-header">
        <div class="citation-panel-meta">
          <span id="citation-doc-type" class="citation-badge"></span>
          <span id="citation-level" class="citation-level-badge"></span>
          <span id="citation-status" class="citation-status-badge"></span>
        </div>
        <button class="citation-panel-close" onclick="closeCitationPanel()" aria-label="Close">✕</button>
      </div>
      <div class="citation-panel-title" id="citation-panel-title"></div>
      <div class="citation-panel-nomor" id="citation-panel-nomor"></div>
      <div class="citation-panel-pasal" id="citation-panel-pasal"></div>
      <div class="citation-panel-body" id="citation-panel-body">
        <div class="citation-loading">Loading source text…</div>
      </div>
      <div id="citation-jdih-wrap" class="citation-jdih-wrap" style="display:none">
        <a id="citation-jdih-link" class="citation-jdih-link" href="#" target="_blank" rel="noopener noreferrer">
          View on JDIH.go.id ↗
        </a>
      </div>
    </div>
    <div id="citation-overlay" class="citation-overlay" onclick="closeCitationPanel()"></div>
  `);
  initTheme();
  setLanguage(currentLang);
  loadConversations();
  checkDemoStatus();

  document.getElementById("new-chat-btn").addEventListener("click", createNewChat);
  document.getElementById("theme-toggle-btn").addEventListener("click", toggleTheme);

  document.querySelectorAll(".lang-btn").forEach(btn => {
    btn.addEventListener("click", () => setLanguage(btn.dataset.lang));
  });

  // Chips default click handlers
  document.getElementById("chip1")?.addEventListener("click", () => useSuggestion(t("chip1q")));
  document.getElementById("chip2")?.addEventListener("click", () => useSuggestion(t("chip2q")));
  document.getElementById("chip3")?.addEventListener("click", () => useSuggestion(t("chip3q")));

  // File attach
  const attachBtn = document.getElementById("attach-btn");
  const chatFileInput = document.getElementById("chat-file-input");
  attachBtn.addEventListener("click", () => chatFileInput.click());
  chatFileInput.addEventListener("change", e => {
    if (e.target.files && e.target.files.length > 0) {
      addPendingFiles(Array.from(e.target.files));
      chatFileInput.value = "";
    }
  });

  // Drag & drop on input area
  const inputArea = document.getElementById("chat-input-area");
  inputArea.addEventListener("dragover", e => { e.preventDefault(); inputArea.style.borderColor = "var(--accent)"; });
  inputArea.addEventListener("dragleave", () => { inputArea.style.borderColor = ""; });
  inputArea.addEventListener("drop", e => {
    e.preventDefault();
    inputArea.style.borderColor = "";
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      addPendingFiles(Array.from(e.dataTransfer.files));
    }
  });

  // Textarea auto-resize
  const input = document.getElementById("message-input");
  input.addEventListener("input", () => {
    updateSendButton();
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 150) + "px";
  });

  input.addEventListener("keydown", e => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      if ((input.value.trim() || pendingFiles.length > 0) && !isStreaming) {
        const msg = input.value;
        input.value = "";
        input.style.height = "auto";
        updateSendButton();
        sendMessage(msg);
      }
    }
  });

  document.getElementById("message-form").addEventListener("submit", e => {
    e.preventDefault();
    const msg = input.value;
    input.value = "";
    input.style.height = "auto";
    updateSendButton();
    sendMessage(msg);
  });

  // Sidebar toggle
  document.getElementById("sidebar-toggle").addEventListener("click", () => {
    document.getElementById("sidebar").classList.toggle("open");
    document.getElementById("sidebar-overlay").classList.toggle("visible");
  });

  document.getElementById("sidebar-overlay").addEventListener("click", closeSidebar);
});

/* ─── Demo Gate & Message Counter ─── */
async function checkDemoStatus() {
  try {
    const res = await fetch('/api/demo/status');
    if (!res.ok) return;
    const data = await res.json();
    if (data.password_required) {
      window.location.href = '/gate';
    }
  } catch(e) {}
}

async function updateMessageCounter(convId) {
  if (!convId) return;
  try {
    const res = await fetch(`/api/conversations/${convId}/message_count`);
    const data = await res.json();
    renderMessageCounter(data);
  } catch(e) {}
}

function renderMessageCounter(data) {
  const existing = document.getElementById('message-counter');
  if (existing) existing.remove();

  if (!data.limit_enabled || data.limit === null) return;

  const remaining = data.remaining;
  const pct = data.count / data.limit;

  const div = document.createElement('div');
  div.id = 'message-counter';
  div.className = 'message-counter' + (pct >= 0.9 ? ' critical' : pct >= 0.7 ? ' warning' : '');
  div.innerHTML = `<span class="counter-dot"></span> ${remaining} message${remaining !== 1 ? 's' : ''} remaining in demo`;

  const inputArea = document.getElementById('chat-input-area');
  inputArea.insertBefore(div, inputArea.firstChild);
}

// ─── Citation Side Panel ─────────────────────────────────────────────────────

/**
 * Scan AI answer HTML and make legal citations clickable.
 * Matches: (PERPRES Nomor 49 Tahun 2021, Pasal 6 Ayat (4) [Level 5])
 *          (UU No. 40 Tahun 2007, Pasal 32 Ayat 1)
 * Also styles [General knowledge — verify against primary source] tags.
 */
function parseCitations(html) {
  const citationRe = /\(([^)]*(?:Nomor|No\.)[^)]*Pasal[^)]*?)\)/gi;
  html = html.replace(citationRe, (match, inner) => {
    const encoded = encodeURIComponent(inner.trim());
    return `<span class="inline-citation" onclick="openCitationPanel('${encoded}')" title="Click to view source text">${match}<span class="citation-icon">§</span></span>`;
  });
  html = html.replace(/\[General knowledge[^\]]*\]/g, m =>
    `<span class="general-knowledge-tag">${m}</span>`
  );
  return html;
}

/**
 * Parse "PERPRES Nomor 49 Tahun 2021, Pasal 6 Ayat (4) [Level 5]"
 * into { nomor_tahun, pasal_ref, doc_type_label }.
 */
function parseCitationString(raw) {
  raw = raw.replace(/\[Level\s*[\d.]+\]/gi, '').trim();
  const nomorMatch = raw.match(/(?:Nomor|No\.?)\s*([\d/]+)\s+Tahun\s+(\d{4})/i);
  const nomor_tahun = nomorMatch ? `Nomor ${nomorMatch[1]} Tahun ${nomorMatch[2]}` : '';
  const pasalMatch  = raw.match(/(Pasal\s+\d+(?:\s+Ayat\s*\(?\d+\)?)?)/i);
  const pasal_ref   = pasalMatch ? pasalMatch[1].trim() : '';
  const typeMatch   = raw.match(/^([A-Z]+(?:\s+[A-Z]+)?)/);
  const doc_type_label = typeMatch ? typeMatch[1] : '';
  return { nomor_tahun, pasal_ref, doc_type_label, raw };
}

/**
 * Try to verify a JDIH search URL client-side.
 * JDIH doesn't support CORS so we use a no-cors fetch — if it resolves
 * without network error the server is reachable and we show the link.
 * Times out after 4 seconds to avoid waiting on a dead server.
 */
async function verifyAndShowJdihLink(nomor_tahun) {
  if (!nomor_tahun) return;
  const query = encodeURIComponent(nomor_tahun);
  const url   = `https://jdih.go.id/search?q=${query}`;
  try {
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), 4000);
    await fetch(url, { method: 'HEAD', mode: 'no-cors', signal: controller.signal });
    clearTimeout(timeout);
    // If we reach here without abort/error, server responded — show link
    const wrap = document.getElementById('citation-jdih-wrap');
    const link = document.getElementById('citation-jdih-link');
    if (wrap && link) {
      link.href = url;
      wrap.style.display = 'block';
    }
  } catch (e) {
    // Timed out or network error — JDIH unreachable, silently hide link
  }
}

async function openCitationPanel(encodedCitation) {
  const raw = decodeURIComponent(encodedCitation);
  const { nomor_tahun, pasal_ref, doc_type_label } = parseCitationString(raw);

  const panel   = document.getElementById('citation-panel');
  const overlay = document.getElementById('citation-overlay');

  // Show panel immediately with loading state
  document.getElementById('citation-panel-title').textContent = raw.replace(/,.*/, '').trim();
  document.getElementById('citation-panel-nomor').textContent = nomor_tahun;
  document.getElementById('citation-panel-pasal').textContent = pasal_ref;
  document.getElementById('citation-panel-body').innerHTML    = '<div class="citation-loading">Loading source text…</div>';
  document.getElementById('citation-doc-type').textContent    = doc_type_label || 'REG';
  document.getElementById('citation-level').textContent       = '';
  document.getElementById('citation-status').textContent      = '';
  document.getElementById('citation-jdih-wrap').style.display = 'none';

  panel.classList.add('open');
  overlay.classList.add('open');
  panel.setAttribute('aria-hidden', 'false');

  // Fetch chunk and verify JDIH in parallel
  const [chunkResult] = await Promise.allSettled([
    fetch(`/api/chunk?${new URLSearchParams({ nomor_tahun, pasal_ref })}`).then(r => r.json()),
    verifyAndShowJdihLink(nomor_tahun)
  ]);

  if (chunkResult.status === 'fulfilled' && chunkResult.value.chunks?.length > 0) {
    const chunks = chunkResult.value.chunks;
    const first  = chunks[0];

    // Header badges
    if (first.hierarchy_level != null)
      document.getElementById('citation-level').textContent = `Level ${first.hierarchy_level}`;

    const statusMap = { berlaku: 'Active', diubah: 'Amended', dicabut: 'Revoked' };
    const statusEl  = document.getElementById('citation-status');
    statusEl.textContent = statusMap[first.status] || first.status || '';
    statusEl.className   = `citation-status-badge ${first.status || ''}`;

    // Chunk body — exact text + surrounding context
    document.getElementById('citation-panel-body').innerHTML = chunks.map((c, idx) => {
      const ref = c.pasal_ref     ? `<div class="chunk-pasal-ref">${escapeHtml(c.pasal_ref)}</div>`     : '';
      const sec = c.section_header ? `<div class="chunk-section">${escapeHtml(c.section_header)}</div>` : '';
      const divider = idx > 0 ? '<div class="chunk-divider"></div>' : '';
      return `${divider}<div class="chunk-block">${ref}${sec}<div class="chunk-text">${escapeHtml(c.content)}</div></div>`;
    }).join('');
  } else {
    document.getElementById('citation-panel-body').innerHTML =
      '<div class="citation-not-found">Source text not found in knowledge base.<br>This document may not yet be uploaded.</div>';
  }
}

function closeCitationPanel() {
  document.getElementById('citation-panel').classList.remove('open');
  document.getElementById('citation-overlay').classList.remove('open');
  document.getElementById('citation-panel').setAttribute('aria-hidden', 'true');
}

document.addEventListener('keydown', e => {
  if (e.key === 'Escape') closeCitationPanel();
});
