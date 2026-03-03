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
  await loadConversations();
  selectConversation(conv.id, conv.title);
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
      <div class="message-text ${streaming ? 'loading' : ''}">${role === "user" ? escapeHtml(content) : (content ? marked.parse(content) : "")}</div>
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
  textDiv.innerHTML = marked.parse(content);
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

    if (data.error) {
      updateMessage(assistantDiv, `*Error: ${data.error}*`);
    } else {
      updateMessage(assistantDiv, data.content, data.sources || []);
    }

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
  initTheme();
  setLanguage(currentLang);
  loadConversations();

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
