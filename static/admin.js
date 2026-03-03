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
        </div>
      </div>
      <button class="admin-doc-delete" onclick="deleteDocument(${d.id})" title="Delete">
        <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <polyline points="3 6 5 6 21 6"/>
          <path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/>
        </svg>
      </button>
    </div>`;
  }).join("");
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

async function previewFile(file) {
  const fd = new FormData();
  fd.append("file", file);
  const res = await fetch("/api/admin/documents/preview", { method: "POST", body: fd });
  if (!res.ok) {
    alert("Failed to read file. Please check the file format.");
    return null;
  }
  return await res.json();
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
    ${metadata.text_preview ? `<br><br><em>Preview:</em> ${escapeHtml(metadata.text_preview)}` : ""}
  `;
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
  const btn = document.getElementById("admin-upload-btn");
  const status = document.getElementById("admin-upload-status");
  btn.disabled = true;
  btn.textContent = "Uploading...";
  status.textContent = "";

  const fd = new FormData();
  fd.append("file", selectedFile);
  fd.append("title", document.getElementById("admin-title").value);
  fd.append("doc_type", document.getElementById("admin-doc-type").value);
  fd.append("nomor_tahun", document.getElementById("admin-nomor-tahun").value);
  fd.append("teu", document.getElementById("admin-teu").value);
  fd.append("subjek", document.getElementById("admin-subjek").value);
  fd.append("status", document.getElementById("admin-status").value);
  fd.append("abstrak", document.getElementById("admin-abstrak").value);
  fd.append("dasar_hukum", document.getElementById("admin-dasar-hukum").value);

  const res = await fetch("/api/admin/documents", { method: "POST", body: fd });

  if (res.ok) {
    status.textContent = "✓ Document uploaded successfully!";
    status.className = "admin-upload-status success";
    setTimeout(() => {
      hidePreviewSection();
      loadDocuments();
    }, 1500);
  } else {
    const data = await res.json();
    status.textContent = `✗ Upload failed: ${data.error || "Unknown error"}`;
    status.className = "admin-upload-status error";
  }

  btn.disabled = false;
  btn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg> Upload Document`;
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
