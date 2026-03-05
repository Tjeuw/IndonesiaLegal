/* =====================================================
   INDONESIA LAW AI — admin.js
   ===================================================== */

let selectedFile = null;

async function loadDocuments() {
  const res = await fetch("/api/admin/documents");
  if (!res.ok) { window.location.href = "/admin"; return; }
  const docs = await res.json();
  const list = document.getElementById("admin-docs-list");
  const count = document.getElementById("admin-doc-count");
  count.textContent = `${docs.length} document${docs.length !== 1 ? "s" : ""}`;
  if (docs.length === 0) {
    list.innerHTML = '<div class="admin-empty">No documents uploaded yet.</div>';
    return;
  }
  list.innerHTML = docs.map(d => {
    const badgeClass = d.status === "berlaku" ? "badge-berlaku" : d.status === "diubah" ? "badge-diubah" : "badge-dicabut";
    const badgeLabel = d.status === "berlaku" ? "Active" : d.status === "diubah" ? "Amended" : "Revoked";
    const embedded   = d.embedded_chunks || 0;
    const total      = d.total_chunks || 0;
    const fullyEmbedded = embedded >= total && total > 0;
    const embedBadge = fullyEmbedded
      ? `<span class="embed-badge embed-badge-done" title="${embedded}/${total} chunks embedded">⚡ Embedded</span>`
      : `<span class="embed-badge embed-badge-pending" title="${embedded}/${total} chunks embedded">${embedded > 0 ? embedded + "/" + total : "No"} embeddings</span>`;
    const embedBtn = fullyEmbedded ? "" : `
      <button class="admin-embed-btn" id="embed-btn-${d.id}" onclick="embedDocument(${d.id}, ${total})" title="Generate embeddings for vector search">
        ⚡ Generate Embeddings
      </button>`;
    return `
    <div class="admin-doc-row" id="doc-row-${d.id}">
      <div class="admin-doc-info">
        <div class="admin-doc-title">${escapeHtml(d.title)}</div>
        <div class="admin-doc-meta">
          <span>${escapeHtml(d.doc_type.toUpperCase())}</span>
          ${d.nomor_tahun ? `<span>${escapeHtml(d.nomor_tahun)}</span>` : ""}
          ${d.teu ? `<span>${escapeHtml(d.teu)}</span>` : ""}
          <span>${d.total_chunks} chunks</span>
          <span class="admin-doc-badge ${badgeClass}">${badgeLabel}</span>
          ${embedBadge}
        </div>
        <div id="embed-progress-${d.id}" class="embed-progress" style="display:none"></div>
      </div>
      <div class="admin-doc-actions">
        ${embedBtn}
        <button class="admin-doc-delete" onclick="deleteDocument(${d.id})" title="Delete">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="3 6 5 6 21 6"/>
            <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
          </svg>
        </button>
      </div>
    </div>`;
  }).join("");
}

async function embedDocument(docId, totalChunks) {
  const btn      = document.getElementById(`embed-btn-${docId}`);
  const progress = document.getElementById(`embed-progress-${docId}`);
  if (btn) { btn.disabled = true; btn.textContent = "Generating…"; }
  if (progress) { progress.style.display = "block"; progress.textContent = "Calling Gemini embedding API — this may take several minutes for large documents…"; }
  try {
    const res = await fetch(`/api/admin/embed/${docId}`, { method: "POST" });
    const data = await res.json();
    if (res.ok) {
      if (progress) progress.textContent = `✓ ${data.embedded} chunks embedded${data.errors > 0 ? `, ${data.errors} errors` : ""}`;
      setTimeout(() => loadDocuments(), 1500);
    } else {
      if (progress) progress.textContent = `✗ Error: ${data.error}`;
      if (btn) { btn.disabled = false; btn.textContent = "⚡ Generate Embeddings"; }
    }
  } catch (e) {
    if (progress) progress.textContent = "✗ Network error. Please try again.";
    if (btn) { btn.disabled = false; btn.textContent = "⚡ Generate Embeddings"; }
  }
}

async function deleteDocument(id) {
  if (!confirm("Delete this document and all its chunks?")) return;
  const res = await fetch(`/api/admin/documents/${id}`, { method: "DELETE" });
  if (res.ok) {
    const row = document.getElementById(`doc-row-${id}`);
    if (row) row.remove();
    loadDocuments();
  }
}

// ─── Chunked upload constants ────────────────────────────
const CHUNK_SIZE = 1 * 1024 * 1024;  // 1MB per chunk — Railway 5min proxy limit

async function sha256(buffer) {
  const hashBuffer = await crypto.subtle.digest("SHA-256", buffer);
  return Array.from(new Uint8Array(hashBuffer))
    .map(b => b.toString(16).padStart(2, "0")).join("");
}

function splitIntoChunks(buffer) {
  const chunks = [];
  let offset = 0;
  while (offset < buffer.byteLength) {
    chunks.push(buffer.slice(offset, offset + CHUNK_SIZE));
    offset += CHUNK_SIZE;
  }
  return chunks;
}

function setProgress(pct, label) {
  const bar  = document.getElementById("upload-progress-bar");
  const fill = document.getElementById("upload-progress-fill");
  const text = document.getElementById("upload-progress-text");
  if (!bar) return;
  bar.style.display = "block";
  fill.style.width  = pct + "%";
  text.textContent  = label;
}

function hideProgress() {
  const bar = document.getElementById("upload-progress-bar");
  if (bar) bar.style.display = "none";
}

async function previewFile(file) {
  // Small files (<4MB): send directly to existing preview route — unchanged
  if (file.size < 4 * 1024 * 1024) {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch("/api/admin/documents/preview", { method: "POST", body: fd });
    if (!res.ok) {
      alert("Failed to read file. Please check the file format.");
      return null;
    }
    return await res.json();
  }

  // Large files: chunked upload
  try {
    setProgress(2, "Reading file...");
    const buffer = await file.arrayBuffer();
    const chunks = splitIntoChunks(buffer);
    const total  = chunks.length;

    setProgress(5, "Calculating checksum...");
    const checksum = await sha256(buffer);

    setProgress(8, "Initialising upload...");
    const initRes = await fetch("/api/admin/upload/init", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        filename:     file.name,
        file_size:    file.size,
        total_chunks: total,
        checksum:     checksum
      })
    });
    if (!initRes.ok) throw new Error("Failed to initialise upload");
    const { upload_id } = await initRes.json();

    // Store on file object so uploadDocument can finalize
    selectedFile._uploadId = upload_id;

    // Send chunk 0 first
    setProgress(12, `Uploading chunk 1 of ${total}...`);
    const fd0 = new FormData();
    fd0.append("upload_id",   upload_id);
    fd0.append("chunk_index", "0");
    fd0.append("chunk", new Blob([chunks[0]]), "chunk0");
    const c0Res = await fetch("/api/admin/upload/chunk", { method: "POST", body: fd0 });
    if (!c0Res.ok) throw new Error("Failed to upload first chunk");

    // Send remaining chunks
    for (let i = 1; i < total; i++) {
      const pct = Math.round(12 + (i / total) * 78);
      setProgress(pct, `Uploading chunk ${i + 1} of ${total}...`);
      const fd = new FormData();
      fd.append("upload_id",   upload_id);
      fd.append("chunk_index", String(i));
      fd.append("chunk", new Blob([chunks[i]]), "chunk" + i);
      const cRes = await fetch("/api/admin/upload/chunk", { method: "POST", body: fd });
      if (!cRes.ok) throw new Error(`Failed to upload chunk ${i + 1}`);
    }

    // Get metadata + chunk preview AFTER all chunks received (full document available)
    setProgress(95, "Reading document structure...");
    const pvFd = new FormData();
    pvFd.append("upload_id", upload_id);
    const pvRes = await fetch("/api/admin/upload/preview", { method: "POST", body: pvFd });
    if (!pvRes.ok) throw new Error("Failed to read file metadata");
    const metadata = await pvRes.json();

    setProgress(98, "All chunks received — ready to process");
    return metadata;

  } catch(e) {
    hideProgress();
    alert("Failed to read file: " + e.message);
    return null;
  }
}

function showPreviewSection(metadata) {
  document.getElementById("admin-preview-section").style.display = "block";
  document.getElementById("admin-title").value = metadata.title || "";
  document.getElementById("admin-doc-type").value = metadata.doc_type || "general";
  document.getElementById("admin-nomor-tahun").value = metadata.nomor_tahun || "";
  document.getElementById("admin-teu").value = metadata.teu || "";
  document.getElementById("admin-subjek").value = metadata.subjek || "";
  document.getElementById("admin-status").value = metadata.status || "berlaku";
  document.getElementById("admin-abstrak").value = metadata.abstrak || "";
  document.getElementById("admin-dasar-hukum").value = metadata.dasar_hukum || "";
  const info = document.getElementById("admin-preview-info");
  info.innerHTML = `
    <strong>${escapeHtml(metadata.filename)}</strong><br>
    ${metadata.text_length ? `${metadata.text_length.toLocaleString()} characters extracted` : ""}
  `;
  // Chunk browser
  const chunkBrowser = document.getElementById("admin-chunk-browser");
  if (metadata.chunk_preview && metadata.chunk_preview.length > 0) {
    const method = metadata.chunk_method === "pasal"
      ? `<span class="chunk-method-badge chunk-method-pasal">Pasal-aware</span>`
      : `<span class="chunk-method-badge chunk-method-paragraph">Paragraph</span>`;
    const total   = metadata.chunk_count || metadata.chunk_preview.length;
    const showing = metadata.chunk_preview.length;
    const rows = metadata.chunk_preview.map((c, i) => {
      const pasalRef = c.pasal_ref
        ? `<span class="chunk-pasal-ref">${escapeHtml(c.pasal_ref)}</span>`
        : `<span class="chunk-pasal-ref chunk-pasal-none">Chunk ${i + 1}</span>`;
      const section = c.section_header
        ? `<span class="chunk-section-header">${escapeHtml(c.section_header)}</span>` : "";
      return `<div class="chunk-row">
        <div class="chunk-row-meta">${pasalRef}${section}</div>
        <div class="chunk-row-preview">${escapeHtml(c.preview)}</div>
      </div>`;
    }).join("");
    chunkBrowser.innerHTML = `
      <div class="chunk-browser-header">
        <span>Chunk Structure ${method}</span>
        <span class="chunk-count-label">${total.toLocaleString()} total — showing first ${showing}</span>
      </div>
      <div class="chunk-browser-list">${rows}</div>
      ${total > showing ? `<div class="chunk-browser-more">+ ${(total - showing).toLocaleString()} more chunks</div>` : ""}
    `;
    chunkBrowser.style.display = "block";
  } else {
    chunkBrowser.style.display = "none";
  }
}

function hidePreviewSection() {
  document.getElementById("admin-preview-section").style.display = "none";
  selectedFile = null;
  document.getElementById("admin-upload-zone").style.display = "flex";
  document.getElementById("admin-upload-status").textContent = "";
  document.getElementById("admin-upload-status").className = "admin-upload-status";
}

async function uploadDocument() {
  if (!selectedFile) return;
  const btn    = document.getElementById("admin-upload-btn");
  const status = document.getElementById("admin-upload-status");
  btn.disabled       = true;
  status.textContent = "";

  try {
    // ── Small file: original direct upload path ───────────
    if (!selectedFile._uploadId) {
      btn.textContent = "Processing document...";
      const fd = new FormData();
      fd.append("file",        selectedFile);
      fd.append("title",       document.getElementById("admin-title").value);
      fd.append("doc_type",    document.getElementById("admin-doc-type").value);
      fd.append("nomor_tahun", document.getElementById("admin-nomor-tahun").value);
      fd.append("teu",         document.getElementById("admin-teu").value);
      fd.append("subjek",      document.getElementById("admin-subjek").value);
      fd.append("status",      document.getElementById("admin-status").value);
      fd.append("abstrak",     document.getElementById("admin-abstrak").value);
      fd.append("dasar_hukum", document.getElementById("admin-dasar-hukum").value);
      const res = await fetch("/api/admin/documents", { method: "POST", body: fd });
      if (!res.ok) { const d = await res.json(); throw new Error(d.error || "Upload failed"); }
      const result = await res.json();
      showUploadSuccess(status, result);
      return;
    }

    // ── Large file: finalize chunked upload ───────────────
    setProgress(97, "Processing document — writing chunks to database...");
    btn.textContent = "Processing document...";

    const finalRes = await fetch("/api/admin/upload/finalize", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        upload_id:   selectedFile._uploadId,
        title:       document.getElementById("admin-title").value,
        doc_type:    document.getElementById("admin-doc-type").value,
        nomor_tahun: document.getElementById("admin-nomor-tahun").value,
        teu:         document.getElementById("admin-teu").value,
        subjek:      document.getElementById("admin-subjek").value,
        status:      document.getElementById("admin-status").value,
        abstrak:     document.getElementById("admin-abstrak").value,
        dasar_hukum: document.getElementById("admin-dasar-hukum").value
      })
    });

    if (!finalRes.ok) {
      const d = await finalRes.json();
      throw new Error(d.error || "Finalization failed");
    }

    const finalData = await finalRes.json();

    // If server returned 202, processing is in background — poll for completion
    if (finalData.status === "processing") {
      const uploadId = finalData.upload_id;
      let elapsed = 0;
      while (true) {
        await new Promise(r => setTimeout(r, 3000));
        elapsed += 3;
        setProgress(98, `Processing document — ${elapsed}s elapsed...`);
        const pollRes = await fetch(`/api/admin/upload/status/${uploadId}`);
        const pollData = await pollRes.json();
        if (pollData.status === "done") {
          setProgress(100, "Complete");
          showUploadSuccess(status, pollData.result);
          return;
        }
        if (pollData.status === "error") {
          throw new Error(pollData.error || "Processing failed");
        }
        // still "processing" — keep polling
      }
    }

    // Synchronous response (small file fallback)
    setProgress(100, "Complete");
    showUploadSuccess(status, finalData);

  } catch(e) {
    hideProgress();
    status.textContent = `✗ Upload failed: ${e.message}`;
    status.className   = "admin-upload-status error";
    btn.disabled       = false;
    btn.innerHTML      = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg> Upload Document`;
  }
}

function showUploadSuccess(status, result) {
  hideProgress();
  const mb    = result.file_size_mb ? ` · ${result.file_size_mb} MB` : "";
  const check = result.verified     ? " · ✓ checksum verified"       : "";
  status.textContent = `✓ Uploaded: ${result.total_chunks} chunks created${mb}${check}`;
  status.className   = "admin-upload-status success";
  const btn = document.getElementById("admin-upload-btn");
  btn.disabled  = false;
  btn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg> Upload Document`;
  setTimeout(() => { hidePreviewSection(); loadDocuments(); }, 2500);
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = text;
  return div.innerHTML;
}

document.addEventListener("DOMContentLoaded", () => {
  loadDocuments();

  // Logout
  document.getElementById("admin-logout-btn").addEventListener("click", async () => {
    await fetch("/admin/logout", { method: "POST" });
    window.location.href = "/admin";
  });

  // Upload zone click
  const zone = document.getElementById("admin-upload-zone");
  const fileInput = document.getElementById("admin-file-input");

  zone.addEventListener("click", () => fileInput.click());

  zone.addEventListener("dragover", e => {
    e.preventDefault();
    zone.classList.add("dragover");
  });
  zone.addEventListener("dragleave", () => zone.classList.remove("dragover"));
  zone.addEventListener("drop", async e => {
    e.preventDefault();
    zone.classList.remove("dragover");
    const file = e.dataTransfer.files[0];
    if (file) await handleFileSelection(file);
  });

  fileInput.addEventListener("change", async e => {
    if (e.target.files[0]) await handleFileSelection(e.target.files[0]);
    fileInput.value = "";
  });

  document.getElementById("admin-upload-btn").addEventListener("click", uploadDocument);
  document.getElementById("admin-cancel-btn").addEventListener("click", hidePreviewSection);
});

async function handleFileSelection(file) {
  selectedFile = file;
  const zone = document.getElementById("admin-upload-zone");
  zone.style.display = "none";
  const info = document.getElementById("admin-preview-info");
  info.innerHTML = "Reading file...";
  document.getElementById("admin-preview-section").style.display = "block";

  const metadata = await previewFile(file);
  if (metadata) {
    showPreviewSection(metadata);
  } else {
    hidePreviewSection();
  }
}

// ─── Demo Controls ───────────────────────────────────────
async function loadSettings() {
  const res = await fetch('/api/admin/settings');
  if (!res.ok) return;
  const settings = await res.json();

  const pwEnabled = settings.demo_password_enabled === 'true';
  const limitEnabled = settings.message_limit_enabled === 'true';

  document.getElementById('password-enabled-toggle').checked = pwEnabled;
  document.getElementById('limit-enabled-toggle').checked = limitEnabled;
  document.getElementById('demo-password-input').value = settings.demo_password || '';
  document.getElementById('message-limit-input').value = settings.message_limit || '20';

  document.getElementById('password-body').classList.toggle('visible', pwEnabled);
  document.getElementById('limit-body').classList.toggle('visible', limitEnabled);
}

async function saveSetting(key, value) {
  const status = document.getElementById('demo-save-status');
  const res = await fetch('/api/admin/settings', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({[key]: value})
  });
  if (res.ok) {
    status.textContent = '✓ Saved';
    status.className = 'demo-save-status success';
  } else {
    status.textContent = '✗ Failed to save';
    status.className = 'demo-save-status error';
  }
  setTimeout(() => { status.textContent = ''; status.className = 'demo-save-status'; }, 2000);
}

document.addEventListener('DOMContentLoaded', () => {
  loadSettings();

  document.getElementById('password-enabled-toggle').addEventListener('change', async (e) => {
    document.getElementById('password-body').classList.toggle('visible', e.target.checked);
    await saveSetting('demo_password_enabled', e.target.checked ? 'true' : 'false');
  });

  document.getElementById('limit-enabled-toggle').addEventListener('change', async (e) => {
    document.getElementById('limit-body').classList.toggle('visible', e.target.checked);
    await saveSetting('message_limit_enabled', e.target.checked ? 'true' : 'false');
  });

  document.getElementById('save-password-btn').addEventListener('click', async () => {
    const pw = document.getElementById('demo-password-input').value.trim();
    if (!pw) { alert('Please enter a password first.'); return; }
    await saveSetting('demo_password', pw);
  });

  document.getElementById('save-limit-btn').addEventListener('click', async () => {
    const limit = parseInt(document.getElementById('message-limit-input').value);
    if (!limit || limit < 1) { alert('Please enter a valid number.'); return; }
    await saveSetting('message_limit', limit.toString());
  });
});
