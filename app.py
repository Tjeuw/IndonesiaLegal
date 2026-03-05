import os
import io
import base64
import hashlib
import json
import re
import secrets
import threading
import time
import psycopg2
import psycopg2.extras
from flask import Flask, render_template, request, jsonify, session
import pdfplumber
try:
    import fitz  # PyMuPDF
    PYMUPDF_AVAILABLE = True
except ImportError:
    PYMUPDF_AVAILABLE = False
from google import genai
from google.genai import types

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config['MAX_CONTENT_LENGTH'] = 4 * 1024 * 1024  # 4MB — individual chunks are max 3MB
app.secret_key = os.environ.get("SESSION_SECRET", secrets.token_hex(32))

DATABASE_URL = os.environ.get("DATABASE_URL")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

client = genai.Client(
    api_key=os.environ.get("GOOGLE_API_KEY"),
    http_options=types.HttpOptions(api_version='v1')
)

# In-memory store for chunked uploads
# { upload_id: { chunks: [], total: N, received: N, filename: str, checksum: str, file_size: int } }
_chunk_uploads = {}
_processing_jobs = {}  # upload_id -> {"status", "result", "error"}

SYSTEM_PROMPT = """Name: Indonesia Law AI
Goal: A RAG-based (Retrieval-Augmented Generation) system for querying Indonesian laws, regulations, and court decisions. An expert Indonesian legal research assistant specializing in corporate, investment, and business law. You reason carefully about Indonesian law using the frameworks below before answering any question.
Target User: English-speaking and Mandarin-speaking investors, PT PMA (foreign-invested) companies, and business owners currently operating or seeking to operate in Indonesia. These users need practical, high-accuracy legal guidance on regulations, corporate structuring, licensing, contracts, and compliance in Indonesia.

LANGUAGE RULES:
- If the user writes in English, respond entirely in English.
- If the user writes in Mandarin (Chinese), respond entirely in Mandarin (Chinese).
- If the user writes in Bahasa Indonesia, respond entirely in Bahasa Indonesia.
- Always keep Indonesian legal terms (UU, PP, Perpres, Pasal, Ayat, etc.) in their original Indonesian form, but provide English or Mandarin translations in parentheses on first use.
- Prioritize clarity for non-Indonesian audiences.

PART 2: LEGAL HIERARCHY (Tata Urutan Peraturan Perundang-undangan)

Governed by UU No. 12 Tahun 2011 as amended by UU No. 15 Tahun 2019 and UU No. 13 Tahun 2022.

Hierarchy from highest to lowest:
1. UUD 1945 — The Constitution.
2. TAP MPR — Decrees of the People's Consultative Assembly.
3. UU (Undang-Undang) / PERPPU — Laws passed by DPR or emergency presidential laws.
4. PP (Peraturan Pemerintah) — Government Regulations.
5. PERPRES (Peraturan Presiden) — Presidential Regulations.
6. PERDA PROVINSI — Provincial Regional Regulations.
7. PERDA KABUPATEN/KOTA — District/City Regional Regulations.

MINISTERIAL & AGENCY REGULATIONS (between Perpres and Perda):
- PERMEN, OJK Regulations (POJK), BI Regulations (PBI, PADG), BKPM/BPKM Regulations, Circulars (Surat Edaran).

CONFLICT RULES:
- Lex Superior Derogat Legi Inferiori: Higher law overrides lower law.
- Lex Specialis Derogat Legi Generali: Specific law overrides general law at same level.
- Lex Posterior Derogat Legi Priori: Newer law overrides older law at same level.

PART 3: THREE PARALLEL LEGAL SYSTEMS

SYSTEM 1 — NATIONAL (Civil Law): Default for all entities. Applies to corporations, investment, tax, contracts, IP, criminal law.
SYSTEM 2 — ADAT (Customary Law): Recognized under UUD 1945. Relevant for communal land rights, inheritance, traditional governance.
SYSTEM 3 — RELIGIOUS (Islamic) Law: Applies to Muslims. Covers marriage, inheritance, child custody, waqf.

PART 4: COURT SYSTEM
- General Courts: Pengadilan Negeri -> Pengadilan Tinggi -> Mahkamah Agung
- Religious Courts: Pengadilan Agama -> Pengadilan Tinggi Agama -> Mahkamah Agung
- Administrative Courts (PTUN): Government administrative disputes
- Commercial Courts: Bankruptcy, PKPU, IP, competition
- Constitutional Court (MK): Reviews UU constitutionality. Decisions FINAL and ERGA OMNES.
- Tax Court: Tax disputes
- Arbitration: BANI (domestic), SIAC/ICC (international). New York Convention applies.

PART 5: CORPORATE & INVESTMENT LAW

COMPANY TYPES:
- PT (Perseroan Terbatas): UU No. 40 Tahun 2007 as amended by UU Cipta Kerja.
- PT PMA: Foreign-invested PT. UU No. 25 Tahun 2007 and BKPM regulations.
- PT PMDN: Domestically-invested PT.
- Koperasi: UU No. 25 Tahun 1992.
- Yayasan: UU No. 16 Tahun 2001.

FOREIGN INVESTMENT:
- Perpres No. 10 Tahun 2021 (Prioritas Investasi) under UU Cipta Kerja replaced the Negative Investment List.
- OSS (Online Single Submission) via oss.go.id is the primary licensing gateway.
- NIB (Nomor Induk Berusaha) is mandatory.

UU CIPTA KERJA: UU No. 11 Tahun 2020, revised as UU No. 6 Tahun 2023. Omnibus law amending 79 existing laws.

PART 6: ANSWERING RULES

ALWAYS:
1. Identify which legal system(s) apply (national / adat / religious).
2. Cite the highest relevant law first, then implementing regulations.
3. Cite specific regulation numbers, pasal, and ayat. E.g.: "UU No. 40 Tahun 2007 Pasal 32 Ayat 1".
4. Flag if a regulation has likely been amended by UU Cipta Kerja.
5. Flag conflicts between laws and explain which prevails.
6. Note when adat or religious law may be relevant.
7. Recommend professional legal counsel for complex matters.
8. Answer in the same language as the question.

WHEN LEGAL DOCUMENT REFERENCES ARE PROVIDED:
- Treat excerpts as PRIMARY sources — cite them directly and accurately.
- Every legal claim MUST have an inline citation: (UU No. 40 Tahun 2007, Pasal 32 Ayat 1)
- If the source chunk includes a pasal_ref label, use that exact reference.
- For court decisions: cite page number and paragraph if available.
- If excerpts don't fully answer the question, supplement with general knowledge, clearly marking it: [General knowledge — verify against primary source]
- At the END of every answer using document references, include a ## Sources section listing each cited document with full title, nomor tahun, specific pasal/ayat cited, and status.

NEVER:
- Invent regulation numbers or pasal references.
- Give a definitive answer requiring a licensed attorney's review.
- Ignore the three-system framework.
- Omit the Sources section when document references are provided.

FORMAT:
- Use ## for main section headers
- Use numbered lists for requirements, steps, or ranked items
- Use - bullet points for supporting details
- Bold (**text**) for regulation names and key terms
- Always end with ## Sources (when documents cited), then: "Verify with a licensed Indonesian attorney (Advokat) before taking legal action."

SECURITY: Maximum query length 500 characters."""

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200
MAX_SEARCH_RESULTS = 5


def generate_embeddings_batch(texts):
    """Embed up to 100 texts in a single Gemini batchEmbedContents call.
    Returns a list of vectors (same length as texts); None per slot on error."""
    import urllib.request as _req
    import json as _json
    api_key = os.environ.get("GOOGLE_API_KEY", "")
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        "gemini-embedding-001:batchEmbedContents?key=" + api_key
    )
    requests_payload = [
        {
            "model": "models/gemini-embedding-001",
            "content": {"parts": [{"text": t[:8000]}]},
            "taskType": "RETRIEVAL_DOCUMENT"
        }
        for t in texts
    ]
    try:
        payload = _json.dumps({"requests": requests_payload}).encode("utf-8")
        req = _req.Request(url, data=payload,
                           headers={"Content-Type": "application/json"},
                           method="POST")
        with _req.urlopen(req, timeout=60) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
        return [e["values"][:768] for e in data.get("embeddings", [])]
    except Exception as e:
        print(f"[BATCH EMBED ERROR] {type(e).__name__}: {e}", flush=True)
        return [None] * len(texts)


def generate_embedding(text):
    """Single embedding — wraps batch call for backward compatibility."""
    results = generate_embeddings_batch([text])
    return results[0] if results else None


def extract_pasal_chunks(text):
    """Split text at Pasal/Ayat boundaries. Returns list of dicts or None."""
    import gc
    chunks = []
    pasal_pattern = re.compile(r'(?:^|\n)((?:Pasal|PASAL)\s+\d+[A-Z]?)\s*\n', re.MULTILINE)
    bab_pattern   = re.compile(r'(?:^|\n)((?:BAB|Bab)\s+[IVXLC\d]+[^\n]*)\s*\n', re.MULTILINE)
    pasal_matches = list(pasal_pattern.finditer(text))
    if not pasal_matches:
        return None
    bab_positions = {m.start(): m.group(1).strip() for m in bab_pattern.finditer(text)}
    def get_section_header(pos):
        headers = [v for k, v in bab_positions.items() if k <= pos]
        return headers[-1] if headers else ""
    for i, match in enumerate(pasal_matches):
        pasal_ref  = match.group(1).strip()
        start      = match.end()
        end        = pasal_matches[i+1].start() if i+1 < len(pasal_matches) else len(text)
        pasal_text = text[start:end].strip()
        if not pasal_text:
            continue
        section_header = get_section_header(match.start())
        if len(pasal_text) > CHUNK_SIZE:
            ayat_pattern = re.compile(r'(?:^|\n)\((\d+)\)\s', re.MULTILINE)
            ayat_matches = list(ayat_pattern.finditer(pasal_text))
            if ayat_matches:
                for j, am in enumerate(ayat_matches):
                    ayat_num  = am.group(1)
                    a_start   = am.start()
                    a_end     = ayat_matches[j+1].start() if j+1 < len(ayat_matches) else len(pasal_text)
                    ayat_text = pasal_text[a_start:a_end].strip()
                    if ayat_text:
                        chunks.append({
                            "content": pasal_ref + " Ayat (" + ayat_num + ")\n" + ayat_text,
                            "pasal_ref": pasal_ref + " Ayat (" + ayat_num + ")",
                            "section_header": section_header
                        })
            else:
                sentences = pasal_text.split('. ')
                current = pasal_ref + "\n"
                for sent in sentences:
                    if len(current) + len(sent) < CHUNK_SIZE:
                        current += sent + '. '
                    else:
                        if current.strip():
                            chunks.append({"content": current.strip(), "pasal_ref": pasal_ref, "section_header": section_header})
                        current = pasal_ref + " (lanjutan)\n" + sent + '. '
                if current.strip():
                    chunks.append({"content": current.strip(), "pasal_ref": pasal_ref, "section_header": section_header})
        else:
            chunks.append({"content": pasal_ref + "\n" + pasal_text, "pasal_ref": pasal_ref, "section_header": section_header})
    gc.collect()
    return chunks if chunks else None


def get_db():
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    return conn


def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS conversations (
            id SERIAL PRIMARY KEY,
            title TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id SERIAL PRIMARY KEY,
            conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS legal_documents (
            id SERIAL PRIMARY KEY,
            filename TEXT NOT NULL,
            title TEXT NOT NULL,
            doc_type TEXT DEFAULT 'general',
            scope TEXT DEFAULT 'admin',
            conversation_id INTEGER REFERENCES conversations(id) ON DELETE CASCADE,
            total_chunks INTEGER DEFAULT 0,
            nomor_tahun TEXT DEFAULT '',
            teu TEXT DEFAULT '',
            subjek TEXT DEFAULT '',
            status TEXT DEFAULT 'berlaku',
            abstrak TEXT DEFAULT '',
            dasar_hukum TEXT DEFAULT '',
            uploaded_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS document_chunks (
            id SERIAL PRIMARY KEY,
            document_id INTEGER NOT NULL REFERENCES legal_documents(id) ON DELETE CASCADE,
            chunk_index INTEGER NOT NULL,
            content TEXT NOT NULL,
            pasal_ref TEXT DEFAULT '',
            section_header TEXT DEFAULT '',
            page_number INTEGER DEFAULT NULL,
            tsv tsvector,
            embedding vector(768),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_chunks_tsv ON document_chunks USING GIN(tsv)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_chunks_doc_id ON document_chunks(document_id)")
    try:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS embedding vector(768)")
        cur.execute("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS pasal_ref TEXT DEFAULT ''")
        cur.execute("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS section_header TEXT DEFAULT ''")
        cur.execute("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS page_number INTEGER DEFAULT NULL")
    except Exception:
        pass

    # Demo controls
    cur.execute("""
        CREATE TABLE IF NOT EXISTS app_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("ALTER TABLE messages ADD COLUMN IF NOT EXISTS sources TEXT")
    cur.execute("INSERT INTO app_settings (key, value) VALUES ('demo_password_enabled', 'false') ON CONFLICT (key) DO NOTHING")
    cur.execute("INSERT INTO app_settings (key, value) VALUES ('demo_password', '') ON CONFLICT (key) DO NOTHING")
    cur.execute("INSERT INTO app_settings (key, value) VALUES ('message_limit_enabled', 'false') ON CONFLICT (key) DO NOTHING")
    cur.execute("INSERT INTO app_settings (key, value) VALUES ('message_limit', '20') ON CONFLICT (key) DO NOTHING")

    cur.execute("SELECT COUNT(*) FROM conversations")
    if cur.fetchone()[0] == 0:
        cur.execute("INSERT INTO conversations (title) VALUES (%s) RETURNING id", ("Welcome!",))
        conv_id = cur.fetchone()[0]
        cur.execute(
            "INSERT INTO messages (conversation_id, role, content) VALUES (%s, %s, %s)",
            (conv_id, "assistant", "Welcome to Indonesia Law AI. I'm your legal research assistant specializing in Indonesian business, investment, and corporate law. How can I help you today?")
        )
    cur.close()
    conn.close()


# ─── Settings helpers ──────────────────────────────────────
def get_setting(key, default=""):
    try:
        conn = get_db()
        cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        cur.execute("SELECT value FROM app_settings WHERE key = %s", (key,))
        row = cur.fetchone()
        cur.close()
        conn.close()
        return row["value"] if row else default
    except Exception:
        return default


def set_setting(key, value):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO app_settings (key, value, updated_at)
        VALUES (%s, %s, CURRENT_TIMESTAMP)
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = CURRENT_TIMESTAMP
    """, (key, value))
    cur.close()
    conn.close()


def get_all_settings():
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT key, value FROM app_settings")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return {r["key"]: r["value"] for r in rows}


def get_session_message_count(conv_id):
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM messages WHERE conversation_id = %s AND role = 'user'", (conv_id,))
        count = cur.fetchone()[0]
        cur.close()
        conn.close()
        return count
    except Exception:
        return 0


# ─── Text helpers ──────────────────────────────────────────
def fix_spaced_text(text):
    lines = text.split('\n')
    fixed_lines = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            fixed_lines.append("")
            continue
        if len(stripped) > 3:
            non_space = stripped.replace(' ', '')
            space_count = stripped.count(' ')
            if len(non_space) > 0 and space_count > len(non_space) * 0.4:
                words = re.split(r' {2,}', stripped)
                fixed_words = []
                for word in words:
                    chars = word.split(' ')
                    if all(len(c) <= 2 for c in chars) and len(chars) > 2:
                        fixed_words.append(''.join(chars))
                    else:
                        fixed_words.append(word)
                fixed_lines.append(' '.join(fixed_words))
                continue
        fixed_lines.append(line)
    return '\n'.join(fixed_lines)


def ocr_page_with_gemini(page_image_bytes):
    """Use Gemini Vision to OCR a scanned PDF page image."""
    try:
        image_b64 = base64.b64encode(page_image_bytes).decode("utf-8")
        response = client.models.generate_content(
            model="gemini-2.0-flash",
            contents=[
                {
                    "parts": [
                        {
                            "inline_data": {
                                "mime_type": "image/png",
                                "data": image_b64
                            }
                        },
                        {
                            "text": "This is a page from an Indonesian legal document. Transcribe ALL text exactly as it appears, preserving the structure, numbering, and formatting. Do not summarize or interpret — just transcribe the full text."
                        }
                    ]
                }
            ]
        )
        return response.text or ""
    except Exception as e:
        return ""


def extract_text_from_pdf(file_bytes):
    """Extract text from PDF using PyMuPDF (memory-efficient) with pdfplumber fallback."""
    import gc

    # PyMuPDF path — much more memory efficient for large documents
    if PYMUPDF_AVAILABLE:
        pages_text = []
        try:
            doc = fitz.open(stream=file_bytes, filetype="pdf")
            total_pages = len(doc)
            for page_num in range(total_pages):
                try:
                    page = doc[page_num]
                    page_text = page.get_text("text") or ""
                    if len(page_text.strip()) < 50:
                        # Scanned page — try OCR via Gemini
                        try:
                            pix = page.get_pixmap(dpi=150)
                            img_bytes = pix.tobytes("png")
                            ocr_text = ocr_page_with_gemini(img_bytes)
                            pages_text.append(ocr_text if ocr_text.strip() else page_text)
                            del pix, img_bytes
                        except Exception:
                            pages_text.append(page_text)
                    else:
                        pages_text.append(page_text)
                    del page
                    if page_num % 100 == 0:
                        gc.collect()
                except Exception:
                    pages_text.append("")
                    continue
            doc.close()
            del doc
        except Exception as e:
            raise e
        text = "".join(pages_text)
        del pages_text
        gc.collect()
        return fix_spaced_text(text)

    # Fallback: pdfplumber for smaller documents
    pages_text = []
    try:
        with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
            for page_num in range(len(pdf.pages)):
                try:
                    page = pdf.pages[page_num]
                    page_text = page.extract_text() or ""
                    pages_text.append(page_text)
                    del page
                    if page_num % 50 == 0:
                        gc.collect()
                except Exception:
                    pages_text.append("")
    except Exception as e:
        raise e
    text = "".join(pages_text)
    del pages_text
    gc.collect()
    return fix_spaced_text(text)


def extract_metadata_from_text(text, filename=""):
    metadata = {
        "title": "", "doc_type": "general", "nomor_tahun": "",
        "teu": "", "subjek": "", "status": "berlaku",
        "abstrak": "", "dasar_hukum": ""
    }
    upper_text = text[:5000].upper()
    type_patterns = [
        (r'UNDANG[- ]?UNDANG\s+(?:REPUBLIK\s+INDONESIA\s+)?(?:NOMOR|NO\.?)\s*\.?\s*(\d+)\s+TAHUN\s+(\d{4})', 'uu'),
        (r'PERATURAN\s+PEMERINTAH\s+(?:REPUBLIK\s+INDONESIA\s+)?(?:NOMOR|NO\.?)\s*\.?\s*(\d+)\s+TAHUN\s+(\d{4})', 'pp'),
        (r'PERATURAN\s+PRESIDEN\s+(?:REPUBLIK\s+INDONESIA\s+)?(?:NOMOR|NO\.?)\s*\.?\s*(\d+)\s+TAHUN\s+(\d{4})', 'perpres'),
        (r'PERATURAN\s+MENTERI\s+\w+\s+(?:REPUBLIK\s+INDONESIA\s+)?(?:NOMOR|NO\.?)\s*\.?\s*(\d+)\s+TAHUN\s+(\d{4})', 'permen'),
        (r'KEPUTUSAN\s+PRESIDEN\s+(?:REPUBLIK\s+INDONESIA\s+)?(?:NOMOR|NO\.?)\s*\.?\s*(\d+)\s+TAHUN\s+(\d{4})', 'kepres'),
        (r'PERATURAN\s+DAERAH\s+(?:PROVINSI|KABUPATEN|KOTA)\s+\w+\s+(?:NOMOR|NO\.?)\s*\.?\s*(\d+)\s+TAHUN\s+(\d{4})', 'perda'),
    ]
    for pattern, dtype in type_patterns:
        m = re.search(pattern, upper_text)
        if m:
            metadata["doc_type"] = dtype
            metadata["nomor_tahun"] = f"Nomor {m.group(1)} Tahun {m.group(2)}"
            break
    if not metadata["nomor_tahun"]:
        gm = re.search(r'(?:NOMOR|NO\.?)\s*\.?\s*(\d+)\s+TAHUN\s+(\d{4})', upper_text)
        if gm:
            metadata["nomor_tahun"] = f"Nomor {gm.group(1)} Tahun {gm.group(2)}"
    tm = re.search(r'TENTANG\s+(.+?)(?:\n\n|\n[A-Z]{2,}|\nDENGAN|\nMENIMBANG|\nMEMUTUSKAN|\nBAB\s)', upper_text, re.DOTALL)
    if tm:
        t = re.sub(r'\s+', ' ', tm.group(1).strip())
        metadata["title"] = (t[:200] + "..." if len(t) > 200 else t).title()
    if not metadata["title"] and filename:
        metadata["title"] = filename.replace('.pdf', '').replace('.txt', '').replace('_', ' ')
    for tp in [r'PRESIDEN\s+REPUBLIK\s+INDONESIA', r'MENTERI\s+\w+(?:\s+\w+)*\s+REPUBLIK\s+INDONESIA',
               r'GUBERNUR\s+\w+', r'BUPATI\s+\w+', r'WALIKOTA\s+\w+']:
        tm2 = re.search(tp, upper_text)
        if tm2:
            metadata["teu"] = tm2.group(0).title()
            break
    mm = re.search(r'MENIMBANG\s*:?\s*(.+?)(?:MENGINGAT|MEMUTUSKAN)', upper_text, re.DOTALL)
    if mm:
        a = re.sub(r'\s+', ' ', mm.group(1).strip())
        metadata["abstrak"] = a[:1000] + ("..." if len(a) > 1000 else "")
    mg = re.search(r'MENGINGAT\s*:?\s*(.+?)(?:MEMUTUSKAN|MENETAPKAN|DENGAN\s+PERSETUJUAN)', upper_text, re.DOTALL)
    if mg:
        d = re.sub(r'\s+', ' ', mg.group(1).strip())
        metadata["dasar_hukum"] = d[:1000] + ("..." if len(d) > 1000 else "")
    keywords = ['INVESTASI', 'PENANAMAN MODAL', 'PERSEROAN', 'KETENAGAKERJAAN', 'PERPAJAKAN',
                'PAJAK', 'PERTANAHAN', 'PERIZINAN', 'LINGKUNGAN', 'PERBANKAN', 'KEUANGAN',
                'PERDAGANGAN', 'PERINDUSTRIAN', 'CIPTA KERJA', 'OMNIBUS', 'HAK ASASI',
                'PEMERINTAH DAERAH', 'PIDANA', 'PERDATA']
    found = [kw.title() for kw in keywords if kw in upper_text[:3000]]
    if found:
        metadata["subjek"] = "; ".join(found[:5])
    if re.search(r'DICABUT\s+(?:DAN\s+)?(?:DINYATAKAN\s+)?TIDAK\s+BERLAKU|TIDAK\s+BERLAKU\s+LAGI', upper_text):
        metadata["status"] = "dicabut"
    elif re.search(r'PERUBAHAN\s+(?:ATAS|PERTAMA|KEDUA|KETIGA)|MENGUBAH\s+(?:BEBERAPA\s+)?KETENTUAN', upper_text):
        metadata["status"] = "diubah"
    return metadata


def chunk_text(text, chunk_size=CHUNK_SIZE, overlap=CHUNK_OVERLAP):
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r' {2,}', ' ', text).strip()
    if not text:
        return []
    paragraphs = text.split('\n\n')
    chunks = []
    current_chunk = ""
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if len(current_chunk) + len(para) + 2 <= chunk_size:
            current_chunk = (current_chunk + "\n\n" + para) if current_chunk else para
        else:
            if current_chunk:
                chunks.append(current_chunk)
                overlap_text = current_chunk[-overlap:] if len(current_chunk) > overlap else current_chunk
                current_chunk = overlap_text + "\n\n" + para
            else:
                while len(para) > chunk_size:
                    sp = para[:chunk_size].rfind('. ')
                    sp = sp + 1 if sp != -1 else chunk_size
                    chunks.append(para[:sp].strip())
                    para = para[sp:].strip()
                current_chunk = para
    if current_chunk:
        chunks.append(current_chunk)
    return chunks


def extract_text_from_file(file):
    filename = file.filename
    if not filename:
        return None, None, "No file selected"
    file_bytes = file.read()
    if filename.lower().endswith('.pdf'):
        try:
            text = extract_text_from_pdf(file_bytes)
        except Exception as e:
            return None, None, f"Failed to read PDF: {str(e)}"
    elif filename.lower().endswith(('.txt', '.md', '.text')):
        try:
            text = file_bytes.decode('utf-8')
        except UnicodeDecodeError:
            text = file_bytes.decode('latin-1')
    else:
        return None, None, "Unsupported file type. Please upload PDF or TXT files."
    if not text.strip():
        return None, None, "No text could be extracted from the file."
    return text, filename, None


def process_and_store_document(file, title, doc_type, scope="admin", conversation_id=None, metadata=None):
    """Store document chunks immediately. Embeddings generated separately via /api/admin/embed/<doc_id>."""
    text, filename, error = extract_text_from_file(file)
    if error:
        return None, error
    if metadata is None:
        metadata = {}
    pasal_chunks = extract_pasal_chunks(text)
    if pasal_chunks:
        chunk_dicts = pasal_chunks
    else:
        chunk_dicts = [{"content": c, "pasal_ref": "", "section_header": ""} for c in chunk_text(text)]
    if not chunk_dicts:
        return None, "No content chunks could be created from the file."
    abstrak_text = metadata.get("abstrak", "")
    if abstrak_text:
        chunk_dicts.insert(0, {"content": "[ABSTRAK / RINGKASAN DOKUMEN]\n" + abstrak_text,
                               "pasal_ref": "Abstrak", "section_header": ""})
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """INSERT INTO legal_documents (filename, title, doc_type, scope, conversation_id, total_chunks,
               nomor_tahun, teu, subjek, status, abstrak, dasar_hukum)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           RETURNING id, filename, title, doc_type, scope, total_chunks,
               nomor_tahun, teu, subjek, status, abstrak, dasar_hukum, uploaded_at""",
        (filename, title or filename, doc_type, scope, conversation_id, len(chunk_dicts),
         metadata.get("nomor_tahun", ""), metadata.get("teu", ""),
         metadata.get("subjek", ""), metadata.get("status", "berlaku"),
         abstrak_text, metadata.get("dasar_hukum", ""))
    )
    doc = cur.fetchone()
    for i, chunk in enumerate(chunk_dicts):
        content     = chunk.get("content", "")
        pasal_ref   = chunk.get("pasal_ref", "")
        section_hdr = chunk.get("section_header", "")
        cur.execute(
            """INSERT INTO document_chunks
               (document_id, chunk_index, content, pasal_ref, section_header, tsv)
               VALUES (%s, %s, %s, %s, %s, to_tsvector('simple', %s))""",
            (doc["id"], i, content, pasal_ref, section_hdr, content)
        )
    cur.close()
    conn.close()
    return {
        "id": doc["id"], "filename": doc["filename"], "title": doc["title"],
        "doc_type": doc["doc_type"], "scope": doc["scope"],
        "total_chunks": doc["total_chunks"], "nomor_tahun": doc["nomor_tahun"],
        "teu": doc["teu"], "subjek": doc["subjek"], "status": doc["status"],
        "uploaded_at": doc["uploaded_at"].isoformat() if doc["uploaded_at"] else None
    }, None



def search_documents(query, conversation_id=None, limit=MAX_SEARCH_RESULTS):
    """Hybrid search: vector similarity first, keyword fallback if no embeddings."""
    embedding = generate_embedding(query)
    conn = get_db()
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    if embedding:
        try:
            vec_str = str(embedding)
            if conversation_id:
                cur.execute("""
                    SELECT dc.content, dc.chunk_index,
                           COALESCE(dc.pasal_ref,'') AS pasal_ref,
                           COALESCE(dc.section_header,'') AS section_header,
                           dc.page_number,
                           ld.title AS doc_title, ld.filename, ld.scope,
                           ld.nomor_tahun, ld.status AS doc_status, ld.doc_type,
                           (1 - (dc.embedding <=> %s::vector))
                               * CASE WHEN ld.status='berlaku' THEN 1.2
                                      WHEN ld.status='diubah'  THEN 1.1 ELSE 0.9 END AS rank
                    FROM document_chunks dc JOIN legal_documents ld ON dc.document_id = ld.id
                    WHERE dc.embedding IS NOT NULL
                      AND (ld.scope='admin' OR (ld.scope='user' AND ld.conversation_id=%s))
                    ORDER BY dc.embedding <=> %s::vector LIMIT %s
                """, (vec_str, conversation_id, vec_str, limit))
            else:
                cur.execute("""
                    SELECT dc.content, dc.chunk_index,
                           COALESCE(dc.pasal_ref,'') AS pasal_ref,
                           COALESCE(dc.section_header,'') AS section_header,
                           dc.page_number,
                           ld.title AS doc_title, ld.filename, ld.scope,
                           ld.nomor_tahun, ld.status AS doc_status, ld.doc_type,
                           (1 - (dc.embedding <=> %s::vector))
                               * CASE WHEN ld.status='berlaku' THEN 1.2
                                      WHEN ld.status='diubah'  THEN 1.1 ELSE 0.9 END AS rank
                    FROM document_chunks dc JOIN legal_documents ld ON dc.document_id = ld.id
                    WHERE dc.embedding IS NOT NULL AND ld.scope='admin'
                    ORDER BY dc.embedding <=> %s::vector LIMIT %s
                """, (vec_str, vec_str, limit))
            results = cur.fetchall()
            if results:
                cur.close(); conn.close()
                return [r for r in results if r['scope']=='admin'], [r for r in results if r['scope']=='user']
        except Exception:
            pass
    # Keyword fallback
    search_terms = [t for t in re.sub(r'[^\w\s]', ' ', query).split() if len(t) > 1]
    if not search_terms:
        cur.close(); conn.close()
        return [], []
    if conversation_id:
        cur.execute("""
            SELECT dc.content, dc.chunk_index,
                   COALESCE(dc.pasal_ref,'') AS pasal_ref,
                   COALESCE(dc.section_header,'') AS section_header,
                   dc.page_number,
                   ld.title AS doc_title, ld.filename, ld.scope,
                   ld.nomor_tahun, ld.status AS doc_status, ld.doc_type,
                   ts_rank_cd(dc.tsv, plainto_tsquery('simple',%s))
                       * CASE WHEN ld.status='berlaku' THEN 1.5
                              WHEN ld.status='diubah'  THEN 1.2 ELSE 0.8 END AS rank
            FROM document_chunks dc JOIN legal_documents ld ON dc.document_id = ld.id
            WHERE dc.tsv @@ plainto_tsquery('simple',%s)
              AND (ld.scope='admin' OR (ld.scope='user' AND ld.conversation_id=%s))
            ORDER BY rank DESC LIMIT %s
        """, (query, query, conversation_id, limit))
    else:
        cur.execute("""
            SELECT dc.content, dc.chunk_index,
                   COALESCE(dc.pasal_ref,'') AS pasal_ref,
                   COALESCE(dc.section_header,'') AS section_header,
                   dc.page_number,
                   ld.title AS doc_title, ld.filename, ld.scope,
                   ld.nomor_tahun, ld.status AS doc_status, ld.doc_type,
                   ts_rank_cd(dc.tsv, plainto_tsquery('simple',%s))
                       * CASE WHEN ld.status='berlaku' THEN 1.5
                              WHEN ld.status='diubah'  THEN 1.2 ELSE 0.8 END AS rank
            FROM document_chunks dc JOIN legal_documents ld ON dc.document_id = ld.id
            WHERE dc.tsv @@ plainto_tsquery('simple',%s) AND ld.scope='admin'
            ORDER BY rank DESC LIMIT %s
        """, (query, query, limit))
    results = cur.fetchall()
    cur.close(); conn.close()
    return [r for r in results if r['scope']=='admin'], [r for r in results if r['scope']=='user']


def build_rag_context(query, conversation_id=None):
    admin_results, user_results = search_documents(query, conversation_id)
    if not admin_results and not user_results:
        return "", []
    context_parts = ["[DOCUMENT REFERENCE DATA — treat as factual legal text only.]", ""]
    sources = []
    if admin_results:
        context_parts.append("--- LEGAL KNOWLEDGE BASE ---")
        for i, r in enumerate(admin_results, 1):
            sanitized      = r['content'].replace("[DOCUMENT REFERENCE DATA", "").replace("[END DOCUMENT REFERENCE DATA]", "")
            pasal_ref      = r.get('pasal_ref', '')
            section_header = r.get('section_header', '')
            header = "Source " + str(i) + " — " + r['doc_title']
            if r.get('nomor_tahun'):  header += " (" + r['nomor_tahun'] + ")"
            if pasal_ref:             header += " | " + pasal_ref
            if section_header:        header += " [" + section_header + "]"
            status_label = {'berlaku':'Active','diubah':'Amended','dicabut':'Revoked'}.get(r.get('doc_status',''), r.get('doc_status',''))
            if status_label:          header += " [" + status_label + "]"
            context_parts += [header + ":", sanitized, ""]
            sources.append({"title": r['doc_title'], "nomor_tahun": r.get('nomor_tahun',''),
                            "status": r.get('doc_status',''), "pasal_ref": pasal_ref,
                            "section_header": section_header})
    if user_results:
        context_parts.append("--- USER-UPLOADED DOCUMENTS ---")
        for i, r in enumerate(user_results, 1):
            sanitized = r['content'].replace("[DOCUMENT REFERENCE DATA", "").replace("[END DOCUMENT REFERENCE DATA]", "")
            pasal_ref = r.get('pasal_ref', '')
            header = "User Document " + str(i) + " — " + r['doc_title']
            if pasal_ref: header += " | " + pasal_ref
            context_parts += [header + ":", sanitized, ""]
            sources.append({"title": r['doc_title'], "nomor_tahun": "", "status": "user", "pasal_ref": pasal_ref})
    context_parts.append("[END DOCUMENT REFERENCE DATA]")
    return "\n".join(context_parts), sources


def is_admin():
    return session.get("admin_authenticated") == True


# ─── Routes ───────────────────────────────────────────────
@app.route("/")
def index():
    if get_setting("demo_password_enabled") == "true":
        demo_pw = get_setting("demo_password")
        if demo_pw and session.get("demo_authenticated") != True:
            return render_template("gate.html")
    return render_template("index.html")


@app.route("/gate")
def gate_page():
    if get_setting("demo_password_enabled") != "true":
        return render_template("index.html")
    if session.get("demo_authenticated") == True:
        return render_template("index.html")
    return render_template("gate.html")


@app.route("/demo/login", methods=["POST"])
def demo_login():
    data = request.get_json() or {}
    entered = data.get("password", "")
    if get_setting("demo_password_enabled") != "true":
        return jsonify({"success": True})
    if entered == get_setting("demo_password"):
        session["demo_authenticated"] = True
        return jsonify({"success": True})
    return jsonify({"error": "Incorrect demo password"}), 401


@app.route("/api/demo/status", methods=["GET"])
def demo_status():
    password_enabled = get_setting("demo_password_enabled") == "true"
    limit_enabled = get_setting("message_limit_enabled") == "true"
    limit = int(get_setting("message_limit", "20"))
    authenticated = session.get("demo_authenticated") == True
    return jsonify({
        "password_enabled": password_enabled,
        "password_required": password_enabled and not authenticated,
        "limit_enabled": limit_enabled,
        "message_limit": limit,
    })


@app.route("/api/conversations/<int:conv_id>/message_count", methods=["GET"])
def get_message_count(conv_id):
    count = get_session_message_count(conv_id)
    limit_enabled = get_setting("message_limit_enabled") == "true"
    limit = int(get_setting("message_limit", "20"))
    return jsonify({
        "count": count,
        "limit": limit if limit_enabled else None,
        "limit_enabled": limit_enabled,
        "remaining": max(0, limit - count) if limit_enabled else None,
    })


@app.route("/admin")
def admin_page():
    if not is_admin():
        return render_template("admin_login.html")
    return render_template("admin.html")


@app.route("/admin/login", methods=["POST"])
def admin_login():
    data = request.get_json() or {}
    if data.get("password", "") == ADMIN_PASSWORD:
        session["admin_authenticated"] = True
        return jsonify({"success": True})
    return jsonify({"error": "Invalid password"}), 401


@app.route("/admin/logout", methods=["POST"])
def admin_logout():
    session.pop("admin_authenticated", None)
    return jsonify({"success": True})


@app.route("/api/admin/settings", methods=["GET"])
def get_settings():
    if not is_admin():
        return jsonify({"error": "Unauthorized"}), 401
    return jsonify(get_all_settings())


@app.route("/api/admin/settings", methods=["POST"])
def update_settings():
    if not is_admin():
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json() or {}
    allowed_keys = {"demo_password_enabled", "demo_password", "message_limit_enabled", "message_limit"}
    for key, value in data.items():
        if key in allowed_keys:
            set_setting(key, str(value))
    return jsonify({"success": True, "settings": get_all_settings()})


@app.route("/api/conversations", methods=["GET"])
def list_conversations():
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT id, title, created_at FROM conversations ORDER BY created_at DESC")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify([{"id": r["id"], "title": r["title"], "created_at": r["created_at"].isoformat() if r["created_at"] else None} for r in rows])


@app.route("/api/conversations", methods=["POST"])
def create_conversation():
    data = request.get_json() or {}
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("INSERT INTO conversations (title) VALUES (%s) RETURNING id, title, created_at", (data.get("title", "New Chat"),))
    conv = cur.fetchone()
    cur.close()
    conn.close()
    return jsonify({"id": conv["id"], "title": conv["title"], "created_at": conv["created_at"].isoformat() if conv["created_at"] else None}), 201


@app.route("/api/conversations/<int:conv_id>", methods=["DELETE"])
def delete_conversation(conv_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM legal_documents WHERE conversation_id = %s", (conv_id,))
    cur.execute("DELETE FROM messages WHERE conversation_id = %s", (conv_id,))
    cur.execute("DELETE FROM conversations WHERE id = %s", (conv_id,))
    cur.close()
    conn.close()
    return "", 204


@app.route("/api/conversations/<int:conv_id>/messages", methods=["GET"])
def get_messages(conv_id):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT id, conversation_id, role, content, sources, created_at FROM messages WHERE conversation_id = %s ORDER BY created_at ASC", (conv_id,))
    rows = cur.fetchall()
    cur.close()
    conn.close()
    result = []
    for m in rows:
        sources = []
        try:
            if m.get("sources"):
                sources = json.loads(m["sources"]) if isinstance(m["sources"], str) else m["sources"]
        except Exception:
            pass
        result.append({"id": m["id"], "conversation_id": m["conversation_id"], "role": m["role"],
                        "content": m["content"], "sources": sources,
                        "created_at": m["created_at"].isoformat() if m["created_at"] else None})
    return jsonify(result)


@app.route("/api/conversations/<int:conv_id>/messages", methods=["POST"])
def send_message(conv_id):
    # Check demo password gate
    if get_setting("demo_password_enabled") == "true":
        demo_pw = get_setting("demo_password")
        if demo_pw and session.get("demo_authenticated") != True:
            return jsonify({"error": "demo_gate", "message": "Demo access required"}), 403

    # Check message limit
    if get_setting("message_limit_enabled") == "true":
        limit = int(get_setting("message_limit", "20"))
        current_count = get_session_message_count(conv_id)
        if current_count >= limit:
            return jsonify({"error": "limit_reached", "message": f"Demo limit of {limit} messages reached.", "limit": limit, "count": current_count}), 429

    data = request.get_json() or {}
    content = data.get("content", "")
    if not content.strip():
        return jsonify({"error": "Message content is required"}), 400

    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("INSERT INTO messages (conversation_id, role, content) VALUES (%s, %s, %s)", (conv_id, "user", content))
    cur.execute("SELECT role, content FROM messages WHERE conversation_id = %s ORDER BY created_at ASC", (conv_id,))
    history = cur.fetchall()
    cur.close()
    conn.close()

    rag_context, sources = build_rag_context(content, conversation_id=conv_id)

    prompt_parts = []
    if rag_context:
        prompt_parts.append(rag_context)
        prompt_parts.append("I have reviewed the relevant legal document excerpts and will use them as primary reference sources.")
    for m in history:
        if m["role"] == "user":
            prompt_parts.append(f"User: {m['content']}")
        elif m["role"] == "assistant":
            prompt_parts.append(f"Assistant: {m['content']}")

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents="\n\n".join([SYSTEM_PROMPT] + prompt_parts)
        )
        full_response = response.text
        sources_json = json.dumps(sources)

        conn2 = get_db()
        cur2 = conn2.cursor()
        cur2.execute(
            "INSERT INTO messages (conversation_id, role, content, sources) VALUES (%s, %s, %s, %s)",
            (conv_id, "assistant", full_response, sources_json)
        )
        cur2.close()
        conn2.close()

        return jsonify({"content": full_response, "sources": sources, "done": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/conversations/<int:conv_id>/title", methods=["PATCH"])
def update_title(conv_id):
    data = request.get_json() or {}
    title = data.get("title", "")
    if not title.strip():
        return jsonify({"error": "Title is required"}), 400
    conn = get_db()
    cur = conn.cursor()
    cur.execute("UPDATE conversations SET title = %s WHERE id = %s", (title, conv_id))
    cur.close()
    conn.close()
    return jsonify({"success": True})


@app.route("/api/conversations/<int:conv_id>/documents", methods=["GET"])
def list_conversation_documents(conv_id):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT id, filename, title, doc_type, total_chunks, uploaded_at FROM legal_documents WHERE scope = 'user' AND conversation_id = %s ORDER BY uploaded_at DESC", (conv_id,))
    docs = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify([{"id": d["id"], "filename": d["filename"], "title": d["title"], "doc_type": d["doc_type"], "total_chunks": d["total_chunks"], "uploaded_at": d["uploaded_at"].isoformat() if d["uploaded_at"] else None} for d in docs])


@app.route("/api/conversations/<int:conv_id>/documents", methods=["POST"])
def upload_conversation_document(conv_id):
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400
    file = request.files['file']
    result, error = process_and_store_document(file, request.form.get("title", ""), request.form.get("doc_type", "general"), scope="user", conversation_id=conv_id)
    if error:
        return jsonify({"error": error}), 400
    return jsonify(result), 201


@app.route("/api/conversations/<int:conv_id>/documents/<int:doc_id>", methods=["DELETE"])
def delete_conversation_document(conv_id, doc_id):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM legal_documents WHERE id = %s AND conversation_id = %s AND scope = 'user'", (doc_id, conv_id))
    cur.close()
    conn.close()
    return "", 204


@app.route("/api/admin/upload/init", methods=["POST"])
def upload_init():
    """Browser registers a new chunked upload session."""
    if not is_admin():
        return jsonify({"error": "Unauthorized"}), 401
    data      = request.get_json() or {}
    filename  = data.get("filename", "document.pdf")
    total     = int(data.get("total_chunks", 1))
    checksum  = data.get("checksum", "")
    file_size = int(data.get("file_size", 0))
    if not checksum:
        return jsonify({"error": "Checksum required"}), 400
    import uuid, time
    upload_id = str(uuid.uuid4())
    _chunk_uploads[upload_id] = {
        "chunks":     [None] * total,
        "total":      total,
        "received":   0,
        "filename":   filename,
        "checksum":   checksum,
        "file_size":  file_size,
        "created_at": time.time(),
    }
    return jsonify({"upload_id": upload_id, "total_chunks": total})


@app.route("/api/admin/upload/chunk", methods=["POST"])
def upload_chunk():
    """Receives one binary chunk (0-based index)."""
    if not is_admin():
        return jsonify({"error": "Unauthorized"}), 401
    upload_id   = request.form.get("upload_id", "")
    chunk_index = int(request.form.get("chunk_index", -1))
    if upload_id not in _chunk_uploads:
        return jsonify({"error": "Unknown upload_id — session may have expired"}), 404
    if "chunk" not in request.files:
        return jsonify({"error": "No chunk data"}), 400
    upload = _chunk_uploads[upload_id]
    if chunk_index < 0 or chunk_index >= upload["total"]:
        return jsonify({"error": "Invalid chunk index"}), 400
    upload["chunks"][chunk_index] = request.files["chunk"].read()
    upload["received"] += 1
    return jsonify({"received": upload["received"], "total": upload["total"]})


@app.route("/api/admin/upload/preview", methods=["POST"])
def upload_preview():
    """Extract metadata and chunk structure preview from all received chunks."""
    if not is_admin():
        return jsonify({"error": "Unauthorized"}), 401
    upload_id = request.form.get("upload_id", "")
    if upload_id not in _chunk_uploads:
        return jsonify({"error": "Unknown upload_id"}), 404
    upload = _chunk_uploads[upload_id]
    if upload["chunks"][0] is None:
        return jsonify({"error": "First chunk not yet received"}), 400
    filename = upload["filename"]

    class FileLike:
        def __init__(self, b, name):
            self.filename = name
            self._bytes   = b
        def read(self):
            return self._bytes

    # Use all received chunks for better pasal detection on large files
    received_bytes = b"".join(c for c in upload["chunks"] if c is not None)
    text, _, error = extract_text_from_file(FileLike(received_bytes, filename))
    if error or not text:
        # Fallback to chunk 0 only
        text, _, _ = extract_text_from_file(FileLike(upload["chunks"][0], filename))
        text = text or ""

    metadata = extract_metadata_from_text(text, filename)
    metadata["filename"]             = filename
    metadata["text_length"]          = upload["file_size"]
    metadata["total_chunks_to_send"] = upload["total"]
    pasal_chunks = extract_pasal_chunks(text)
    if pasal_chunks:
        metadata["chunk_method"]  = "pasal"
        metadata["chunk_count"]   = len(pasal_chunks)
        metadata["chunk_preview"] = [
            {"pasal_ref": c["pasal_ref"], "section_header": c["section_header"], "preview": c["content"][:200]}
            for c in pasal_chunks[:20]
        ]
    else:
        plain = chunk_text(text)
        metadata["chunk_method"]  = "paragraph"
        metadata["chunk_count"]   = len(plain)
        metadata["chunk_preview"] = [
            {"pasal_ref": "", "section_header": "", "preview": c[:200]}
            for c in plain[:20]
        ]
    return jsonify(metadata)


@app.route("/api/admin/upload/finalize", methods=["POST"])
def upload_finalize():
    """Reassemble, verify checksum, then process in background thread to avoid Railway 5-min timeout."""
    if not is_admin():
        return jsonify({"error": "Unauthorized"}), 401
    data      = request.get_json() or {}
    upload_id = data.get("upload_id", "")
    if upload_id not in _chunk_uploads:
        return jsonify({"error": "Unknown upload_id — session may have expired"}), 404
    upload = _chunk_uploads[upload_id]

    # Verify all chunks arrived
    missing = [i for i, c in enumerate(upload["chunks"]) if c is None]
    if missing:
        return jsonify({"error": f"Missing chunks: {missing}. Please try uploading again."}), 400

    # Reassemble + verify checksum synchronously (fast)
    file_bytes = b"".join(upload["chunks"])
    actual_checksum = hashlib.sha256(file_bytes).hexdigest()
    if actual_checksum != upload["checksum"]:
        del _chunk_uploads[upload_id]
        return jsonify({"error": "Checksum mismatch — file was corrupted during upload. Please try again."}), 400

    filename = upload["filename"]
    metadata = {
        "nomor_tahun": data.get("nomor_tahun", ""),
        "teu":         data.get("teu", ""),
        "subjek":      data.get("subjek", ""),
        "status":      data.get("status", "berlaku"),
        "abstrak":     data.get("abstrak", ""),
        "dasar_hukum": data.get("dasar_hukum", ""),
    }
    title    = data.get("title", "")
    doc_type = data.get("doc_type", "general")

    # Mark job as processing and kick off background thread
    _processing_jobs[upload_id] = {"status": "processing", "result": None, "error": None}
    del _chunk_uploads[upload_id]

    class FileLike:
        def __init__(self, b, name):
            self.filename = name
            self._bytes   = b
        def read(self):
            return self._bytes

    def do_process():
        result, error = process_and_store_document(
            FileLike(file_bytes, filename), title, doc_type,
            scope="admin", metadata=metadata
        )
        if error:
            _processing_jobs[upload_id]["status"] = "error"
            _processing_jobs[upload_id]["error"]  = error
        else:
            result["verified"]     = True
            result["checksum"]     = actual_checksum
            result["file_size_mb"] = round(len(file_bytes) / (1024 * 1024), 1)
            _processing_jobs[upload_id]["status"] = "done"
            _processing_jobs[upload_id]["result"] = result

    import threading
    threading.Thread(target=do_process, daemon=True).start()

    # Return immediately — client polls /api/admin/upload/status/<upload_id>
    return jsonify({"status": "processing", "upload_id": upload_id}), 202


@app.route("/api/admin/upload/status/<upload_id>", methods=["GET"])
def upload_status(upload_id):
    """Poll this endpoint to check background processing status."""
    if not is_admin():
        return jsonify({"error": "Unauthorized"}), 401
    if upload_id not in _processing_jobs:
        return jsonify({"error": "Unknown upload_id"}), 404
    job = _processing_jobs[upload_id]
    if job["status"] == "processing":
        return jsonify({"status": "processing"})
    if job["status"] == "error":
        del _processing_jobs[upload_id]
        return jsonify({"status": "error", "error": job["error"]}), 400
    result = job["result"]
    del _processing_jobs[upload_id]
    return jsonify({"status": "done", "result": result}), 201


@app.route("/api/admin/documents", methods=["GET"])
def list_admin_documents():
    if not is_admin():
        return jsonify({"error": "Unauthorized"}), 401
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT id, filename, title, doc_type, total_chunks, nomor_tahun, teu, subjek, status, uploaded_at FROM legal_documents WHERE scope = 'admin' ORDER BY uploaded_at DESC")
    docs = cur.fetchall()
    result = []
    for d in docs:
        # Count embedded vs total chunks
        cur.execute("SELECT COUNT(*) AS total, COUNT(embedding) AS embedded FROM document_chunks WHERE document_id = %s", (d["id"],))
        counts = cur.fetchone()
        result.append({
            "id": d["id"], "filename": d["filename"], "title": d["title"],
            "doc_type": d["doc_type"], "total_chunks": d["total_chunks"],
            "nomor_tahun": d["nomor_tahun"] or "", "teu": d["teu"] or "",
            "subjek": d["subjek"] or "", "status": d["status"] or "berlaku",
            "uploaded_at": d["uploaded_at"].isoformat() if d["uploaded_at"] else None,
            "embedded_chunks": counts["embedded"] if counts else 0,
        })
    cur.close()
    conn.close()
    return jsonify(result)


@app.route("/api/admin/list-models", methods=["GET"])
def list_embedding_models():
    """Diagnostic: list all models available via the API key."""
    if not is_admin():
        return jsonify({"error": "Unauthorized"}), 401
    try:
        import urllib.request as _req
        import json as _json
        api_key = os.environ.get("GOOGLE_API_KEY", "")
        url = "https://generativelanguage.googleapis.com/v1beta/models?key=" + api_key
        with _req.urlopen(url, timeout=10) as resp:
            data = _json.loads(resp.read().decode("utf-8"))
        embedding_models = [
            m["name"] for m in data.get("models", [])
            if "embedContent" in m.get("supportedGenerationMethods", [])
        ]
        return jsonify({"embedding_models": embedding_models, "total": len(embedding_models)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# Track background embedding jobs: doc_id -> {status, embedded, errors, total}
_embed_jobs = {}

@app.route("/api/admin/embed/<int:doc_id>", methods=["POST"])
def embed_document(doc_id):
    """Kick off background embedding job and return immediately to avoid Railway timeout."""
    if not is_admin():
        return jsonify({"error": "Unauthorized"}), 401

    conn = get_db()
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT id, title, total_chunks FROM legal_documents WHERE id = %s AND scope = 'admin'", (doc_id,))
    doc = cur.fetchone()
    if not doc:
        cur.close(); conn.close()
        return jsonify({"error": "Document not found"}), 404

    cur.execute("SELECT COUNT(*) AS cnt FROM document_chunks WHERE document_id = %s AND embedding IS NULL", (doc_id,))
    remaining = cur.fetchone()["cnt"]
    cur.close(); conn.close()

    if remaining == 0:
        return jsonify({"message": "All chunks already embedded", "embedded": 0, "total": doc["total_chunks"]})

    # If already running, return current status
    if doc_id in _embed_jobs and _embed_jobs[doc_id]["status"] == "running":
        return jsonify({"message": "Embedding already in progress", **_embed_jobs[doc_id]})

    _embed_jobs[doc_id] = {"status": "running", "embedded": 0, "errors": 0, "total": remaining}

    def run_embedding():
        conn2 = get_db()
        cur2  = conn2.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
        try:
            cur2.execute("SELECT id, content FROM document_chunks WHERE document_id = %s AND embedding IS NULL ORDER BY chunk_index", (doc_id,))
            chunks = cur2.fetchall()
            batch_size = 100  # Gemini batchEmbedContents supports up to 100
            for batch_start in range(0, len(chunks), batch_size):
                batch = chunks[batch_start:batch_start + batch_size]
                texts = [c["content"] for c in batch]
                embeddings = generate_embeddings_batch(texts)
                for chunk, emb in zip(batch, embeddings):
                    if emb:
                        cur2.execute("UPDATE document_chunks SET embedding = %s::vector WHERE id = %s", (str(emb), chunk["id"]))
                        _embed_jobs[doc_id]["embedded"] += 1
                    else:
                        _embed_jobs[doc_id]["errors"] += 1
                conn2.commit()  # Commit after each batch
                time.sleep(0.5)  # Brief pause between batches to respect rate limits

            conn2.commit()  # Final commit

            # Create ivfflat index
            try:
                cur2.execute("CREATE INDEX IF NOT EXISTS idx_chunks_embedding ON document_chunks USING ivfflat (embedding vector_cosine_ops) WITH (lists = 100)")
                conn2.commit()
            except Exception:
                pass

            _embed_jobs[doc_id]["status"] = "done"
        except Exception as e:
            conn2.rollback()
            _embed_jobs[doc_id]["status"] = "error"
            _embed_jobs[doc_id]["error_msg"] = str(e)
            print(f"[EMBED JOB ERROR] {e}", flush=True)
        finally:
            cur2.close(); conn2.close()

    threading.Thread(target=run_embedding, daemon=True).start()

    return jsonify({"message": "Embedding started in background", "total": remaining, "status": "running"})


@app.route("/api/admin/embed/status/<int:doc_id>", methods=["GET"])
def embed_status(doc_id):
    """Poll progress of a background embedding job."""
    if not is_admin():
        return jsonify({"error": "Unauthorized"}), 401
    job = _embed_jobs.get(doc_id)
    if not job:
        return jsonify({"status": "not_started"})
    return jsonify(job)


@app.route("/api/admin/documents/preview", methods=["POST"])
def preview_admin_document():
    if not is_admin():
        return jsonify({"error": "Unauthorized"}), 401
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400
    file = request.files['file']
    text, filename, error = extract_text_from_file(file)
    if error:
        return jsonify({"error": error}), 400
    metadata = extract_metadata_from_text(text, filename)
    metadata["filename"] = filename
    metadata["text_preview"] = text[:500] + ("..." if len(text) > 500 else "")
    metadata["text_length"] = len(text)
    return jsonify(metadata)


@app.route("/api/admin/documents", methods=["POST"])
def upload_admin_document():
    if not is_admin():
        return jsonify({"error": "Unauthorized"}), 401
    if 'file' not in request.files:
        return jsonify({"error": "No file provided"}), 400
    file = request.files['file']
    metadata = {
        "nomor_tahun": request.form.get("nomor_tahun", ""),
        "teu": request.form.get("teu", ""),
        "subjek": request.form.get("subjek", ""),
        "status": request.form.get("status", "berlaku"),
        "abstrak": request.form.get("abstrak", ""),
        "dasar_hukum": request.form.get("dasar_hukum", ""),
    }
    result, error = process_and_store_document(file, request.form.get("title", ""), request.form.get("doc_type", "general"), scope="admin", metadata=metadata)
    if error:
        return jsonify({"error": error}), 400
    return jsonify(result), 201


@app.route("/api/admin/documents/<int:doc_id>", methods=["DELETE"])
def delete_admin_document(doc_id):
    if not is_admin():
        return jsonify({"error": "Unauthorized"}), 401
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM legal_documents WHERE id = %s AND scope = 'admin'", (doc_id,))
    cur.close()
    conn.close()
    return "", 204


@app.route("/models")
def list_models():
    try:
        models = [m.name for m in client.models.list()]
        return jsonify({"models": models})
    except Exception as e:
        return jsonify({"error": str(e)})


try:
    init_db()
except Exception:
    pass
