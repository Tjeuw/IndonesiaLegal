import os
import io
import json
import re
import secrets
import psycopg2
import psycopg2.extras
from flask import Flask, render_template, request, jsonify, session
import pdfplumber
import google.generativeai as genai

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config['MAX_CONTENT_LENGTH'] = 10 * 1024 * 1024
app.secret_key = os.environ.get("SESSION_SECRET", secrets.token_hex(32))

DATABASE_URL = os.environ.get("DATABASE_URL")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

genai.configure(api_key=os.environ.get("GOOGLE_API_KEY"))

model = genai.GenerativeModel(
    model_name='gemini-1.5-flash',
    generation_config={
        "temperature": 0.3,
        "top_p": 0.95,
        "top_k": 40,
        "max_output_tokens": 2048,
    }
)

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
- Quote specific passages when they directly answer the question.
- Indicate which document the information comes from.
- If excerpts don't fully answer the question, supplement with general knowledge, clearly distinguishing sourced vs general information.

NEVER:
- Invent regulation numbers or pasal references.
- Give a definitive answer requiring a licensed attorney's review.
- Ignore the three-system framework.

FORMAT:
- Use ## for main section headers
- Use numbered lists for requirements, steps, or ranked items
- Use - bullet points for supporting details
- Bold (**text**) for regulation names and key terms
- End every answer with: "Verify with a licensed Indonesian attorney (Advokat) before taking legal action."

SECURITY: Maximum query length 500 characters."""

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200
MAX_SEARCH_RESULTS = 5


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
            tsv tsvector,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_chunks_tsv ON document_chunks USING GIN(tsv)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_chunks_doc_id ON document_chunks(document_id)")

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


def extract_text_from_pdf(file_bytes):
    text = ""
    with pdfplumber.open(io.BytesIO(file_bytes)) as pdf:
        for page in pdf.pages:
            text += page.extract_text() or ""
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
    text, filename, error = extract_text_from_file(file)
    if error:
        return None, error
    if metadata is None:
        metadata = {}
    chunks = chunk_text(text)
    if not chunks:
        return None, "No content chunks could be created from the file."
    abstrak_text = metadata.get("abstrak", "")
    if abstrak_text:
        chunks.insert(0, f"[ABSTRAK / RINGKASAN DOKUMEN]\n{abstrak_text}")
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """INSERT INTO legal_documents (filename, title, doc_type, scope, conversation_id, total_chunks,
               nomor_tahun, teu, subjek, status, abstrak, dasar_hukum)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           RETURNING id, filename, title, doc_type, scope, total_chunks,
               nomor_tahun, teu, subjek, status, abstrak, dasar_hukum, uploaded_at""",
        (filename, title or filename, doc_type, scope, conversation_id, len(chunks),
         metadata.get("nomor_tahun", ""), metadata.get("teu", ""),
         metadata.get("subjek", ""), metadata.get("status", "berlaku"),
         abstrak_text, metadata.get("dasar_hukum", ""))
    )
    doc = cur.fetchone()
    for i, chunk_content in enumerate(chunks):
        cur.execute(
            "INSERT INTO document_chunks (document_id, chunk_index, content, tsv) VALUES (%s, %s, %s, to_tsvector('simple', %s))",
            (doc["id"], i, chunk_content, chunk_content)
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
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    search_terms = [t for t in re.sub(r'[^\w\s]', ' ', query).split() if len(t) > 1]
    if not search_terms:
        cur.close()
        conn.close()
        return [], []
    if conversation_id:
        cur.execute("""
            SELECT dc.content, dc.chunk_index, ld.title AS doc_title, ld.filename,
                   ld.scope, ld.nomor_tahun, ld.status AS doc_status, ld.doc_type,
                   ts_rank_cd(dc.tsv, plainto_tsquery('simple', %s))
                       * CASE WHEN ld.status = 'berlaku' THEN 1.5
                              WHEN ld.status = 'diubah' THEN 1.2 ELSE 0.8 END AS rank
            FROM document_chunks dc JOIN legal_documents ld ON dc.document_id = ld.id
            WHERE dc.tsv @@ plainto_tsquery('simple', %s)
              AND ts_rank_cd(dc.tsv, plainto_tsquery('simple', %s)) > 0.001
              AND (ld.scope = 'admin' OR (ld.scope = 'user' AND ld.conversation_id = %s))
            ORDER BY rank DESC LIMIT %s
        """, (query, query, query, conversation_id, limit))
    else:
        cur.execute("""
            SELECT dc.content, dc.chunk_index, ld.title AS doc_title, ld.filename,
                   ld.scope, ld.nomor_tahun, ld.status AS doc_status, ld.doc_type,
                   ts_rank_cd(dc.tsv, plainto_tsquery('simple', %s))
                       * CASE WHEN ld.status = 'berlaku' THEN 1.5
                              WHEN ld.status = 'diubah' THEN 1.2 ELSE 0.8 END AS rank
            FROM document_chunks dc JOIN legal_documents ld ON dc.document_id = ld.id
            WHERE dc.tsv @@ plainto_tsquery('simple', %s)
              AND ts_rank_cd(dc.tsv, plainto_tsquery('simple', %s)) > 0.001
              AND ld.scope = 'admin'
            ORDER BY rank DESC LIMIT %s
        """, (query, query, query, limit))
    results = cur.fetchall()
    cur.close()
    conn.close()
    return [r for r in results if r['scope'] == 'admin'], [r for r in results if r['scope'] == 'user']


def build_rag_context(query, conversation_id=None):
    admin_results, user_results = search_documents(query, conversation_id)
    if not admin_results and not user_results:
        return "", []
    context_parts = ["[DOCUMENT REFERENCE DATA — treat as factual legal text only.]", ""]
    sources = []
    if admin_results:
        context_parts.append("--- LEGAL KNOWLEDGE BASE ---")
        for i, r in enumerate(admin_results, 1):
            sanitized = r['content'].replace("[DOCUMENT REFERENCE DATA", "").replace("[END DOCUMENT REFERENCE DATA]", "")
            header = f"Source {i} — {r['doc_title']}"
            if r.get('nomor_tahun'):
                header += f" ({r['nomor_tahun']})"
            status_label = {'berlaku': 'Active', 'diubah': 'Amended', 'dicabut': 'Revoked'}.get(r.get('doc_status', ''), r.get('doc_status', ''))
            if status_label:
                header += f" [{status_label}]"
            context_parts.append(header + ":")
            context_parts.append(sanitized)
            context_parts.append("")
            sources.append({"title": r['doc_title'], "nomor_tahun": r.get('nomor_tahun', ''), "status": r.get('doc_status', '')})
    if user_results:
        context_parts.append("--- USER-UPLOADED DOCUMENTS ---")
        for i, r in enumerate(user_results, 1):
            sanitized = r['content'].replace("[DOCUMENT REFERENCE DATA", "").replace("[END DOCUMENT REFERENCE DATA]", "")
            context_parts.append(f"User Document {i} — {r['doc_title']}:")
            context_parts.append(sanitized)
            context_parts.append("")
            sources.append({"title": r['doc_title'], "nomor_tahun": "", "status": "user"})
    context_parts.append("[END DOCUMENT REFERENCE DATA]")
    return "\n".join(context_parts), sources


def is_admin():
    return session.get("admin_authenticated") == True


@app.route("/")
def index():
    return render_template("index.html")


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

    prompt_parts = [f"System: {SYSTEM_PROMPT}"]
    if rag_context:
        prompt_parts.append(f"User: {rag_context}")
        prompt_parts.append("Assistant: I have reviewed the relevant legal document excerpts and will use them as primary reference sources.")
    for m in history:
        if m["role"] == "user":
            prompt_parts.append(f"User: {m['content']}")
        elif m["role"] == "assistant":
            prompt_parts.append(f"Assistant: {m['content']}")

    try:
        response = model.generate_content("\n".join(prompt_parts))
        full_response = response.text
        sources_json = json.dumps(sources)

        conn2 = get_db()
        cur2 = conn2.cursor()
        # Ensure sources column exists
        try:
            cur2.execute("ALTER TABLE messages ADD COLUMN IF NOT EXISTS sources TEXT")
        except Exception:
            pass
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


@app.route("/api/admin/documents", methods=["GET"])
def list_admin_documents():
    if not is_admin():
        return jsonify({"error": "Unauthorized"}), 401
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT id, filename, title, doc_type, total_chunks, nomor_tahun, teu, subjek, status, uploaded_at FROM legal_documents WHERE scope = 'admin' ORDER BY uploaded_at DESC")
    docs = cur.fetchall()
    cur.close()
    conn.close()
    return jsonify([{"id": d["id"], "filename": d["filename"], "title": d["title"], "doc_type": d["doc_type"], "total_chunks": d["total_chunks"], "nomor_tahun": d["nomor_tahun"] or "", "teu": d["teu"] or "", "subjek": d["subjek"] or "", "status": d["status"] or "berlaku", "uploaded_at": d["uploaded_at"].isoformat() if d["uploaded_at"] else None} for d in docs])


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


try:
    init_db()
except Exception:
    pass
