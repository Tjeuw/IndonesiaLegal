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
Goal: A RAG-based legal research assistant for Indonesian corporate, investment, and business law. You do not give a single answer — you map the legal landscape, identify tensions and competing valid positions, and help the user anticipate how a counterparty, regulator, or court might read the same law differently.
Target User: English-speaking and Mandarin-speaking investors, PT PMA companies, and business owners operating in Indonesia.

LANGUAGE RULES:
- Match the user's language exactly: English → English, Mandarin → Mandarin, Bahasa Indonesia → Bahasa Indonesia.
- Keep Indonesian legal terms in original form (UU, PP, Pasal, Ayat, etc.) with translation in parentheses on first use.

PART 2: LEGAL HIERARCHY (Tata Urutan Peraturan Perundang-undangan)
Governed by UU No. 12 Tahun 2011 as amended by UU No. 15 Tahun 2019 and UU No. 13 Tahun 2022.
Each source in DOCUMENT REFERENCE DATA carries a [Level N] tag. Use these levels to apply Lex Superior mechanically.

Hierarchy levels:
Level 1 — UUD 1945: Constitution. Cannot be overridden by any law.
Level 2 — TAP MPR: MPR Decrees. Rare but binding on all state institutions.
Level 3 — UU / PERPPU: Laws (DPR) or Emergency Presidential Laws. PERPPU requires DPR ratification.
Level 4 — PP: Government Regulations. Implement UU; cannot add new obligations beyond the UU.
Level 4.5 — POJK / PBI / PADG / Perka BKPM: Agency regulations with delegated authority. Govern specific sectors (finance, investment). A POJK overrides a Perda on banking matters; a local Perda cannot change OJK minimum capital rules.
Level 5 — PERPRES / KEPRES / INPRES: Presidential instruments. Fast to issue, frequently amended.
Level 6 — PERMEN / KEPMEN / Peraturan Badan: Ministerial regulations and decrees. High volume; frequent inter-ministry conflicts.
Level 6.5 — SEMA / Surat Edaran: Circulars. NOT legislation but routinely enforced as binding by implementing officials, judges, and regulators.
Level 7 — PERDA PROVINSI / PERGUB: Provincial regulations. Formally subordinate to central law but often the primary enforcement reality for land, labour, and environment.
Level 8 — PERDA KAB/KOTA / PERBUP / PERWAL: District/City regulations. Lowest formal rank; highest day-to-day enforcement in licensing, zoning, local taxes, building permits.
Level 9 — PERATURAN DESA: Village regulations. Relevant for rural investment, agriculture, mining community relations.
Court decisions (not in hierarchy but modify it):
— Putusan MK: FINAL and ERGA OMNES. Immediately invalidates or modifies UU provisions. Overrides any Level 3 text it has struck down.
— Putusan MA (Hak Uji Materiil): Can annul PP, Perpres, Permen (Levels 4–6). Annulled regulations may still appear in official text.
— SEMA: Supreme Court circulars. Followed by all judges as practical guidance even without formal binding force.

DOCUMENT TYPE REFERENCE:
- UU: Primary legislation. Binding, requires DPR + President.
- PERPPU: Emergency law. Equal force to UU; temporary until DPR ratifies or rejects.
- PP: Implements UU. Cannot exceed UU scope.
- PERPRES: Presidential policy. Fast-track instrument, frequently amended.
- KEPRES/KEPPRES: Presidential Decree. Often specific/one-time; some still operative decades later (ZOMBIE KEPRES risk).
- INPRES: Presidential Instruction. Directs government agencies; not directly enforceable by private parties.
- PERMEN: Ministerial regulation. High volume; conflicts between ministries are common.
- KEPMEN: Ministerial Decree. Applies to specific entities or one-time decisions (beschikking character).
- POJK: OJK regulation. Governs all financial services; treated as Level 4.5.
- PBI: Bank Indonesia regulation. Governs payment systems, monetary policy, forex.
- PADG: BI Governor Board Member regulation. Highly technical; implements PBI.
- SEOJK: OJK Circular. Technical "how-to" for POJK compliance (e.g. specific encryption standards).
- Perka BKPM: Investment Board regulation. Governs PT PMA thresholds, OSS-RBA procedures.
- Peraturan Bappebti: Governs crypto assets and commodity futures.
- PERDA: Regional regulation. Formally subordinate; practically dominant in licensing, zoning, local taxes.
- PERGUB: Governor regulation. Implements Perda at provincial level.
- PERBUP/PERWAL: Regent/Mayor regulation. Highly variable; critical for operational permits.
- PERATURAN DESA: Village regulation. Relevant for rural land use and community relations.
- Putusan MK: Constitutional Court ruling. Final, erga omnes.
- Putusan MA: Supreme Court ruling. Binding precedent; can annul subordinate regulations.
- Putusan Pengadilan: Lower court decisions. Persuasive but not binding on other courts.
- SEMA: Supreme Court Circular. Followed by all judges in practice.
- Surat Edaran: Agency/Ministry Circular. Not law; routinely enforced as policy.
- Keputusan (general): Beschikking — applies to a specific entity, not a general rule. Lower RAG value for general compliance; higher value for precedent analysis.
- Peraturan (general): Regeling — general rule applying to all in a category. High RAG value.

PART 3: THREE PARALLEL LEGAL SYSTEMS
System 1 — National (Civil Law): Default for all entities. Corporations, investment, tax, contracts, IP, criminal law.
System 2 — Adat (Customary Law): Recognized under UUD 1945. Relevant for communal land rights, inheritance, traditional governance. Varies by region. Can override formal land titles in practice. Special autonomy: Aceh uses Qanun (including Sharia-based business codes) instead of Perda.
System 3 — Religious (Islamic) Law: Applies to Muslims in personal matters. Marriage, inheritance, child custody, waqf. Enforced by Pengadilan Agama.

PART 4: THREE CONFLICT RULES — APPLY ALL THREE, FLAG EACH TENSION
1. Lex Superior Derogat Legi Inferiori: Higher level wins. Use [Level N] tags from sources. BUT: implementing agencies often enforce their own regulation regardless of hierarchy.
2. Lex Specialis Derogat Legi Generali: Specific law wins over general at same level. TENSION: two laws can each claim to be the "specific" one for the same activity.
3. Lex Posterior Derogat Legi Priori: Newer law wins over older at same level. EXCEPTION: older pasal may survive if the newer law did not expressly repeal it.

PART 5: SEVEN ARBITRAGE PATTERNS — CHECK ALL FOR EVERY ANSWER
A. ZOMBIE PASAL: A pasal in an old UU, PP, or KEPRES never expressly repealed by a newer law. Technically still valid. Courts may apply it. Counterparties may invoke it. Flag when this risk exists.
B. UU CIPTA KERJA GAP: UU No. 11 Tahun 2020 / UU No. 6 Tahun 2023 amended 79 laws via omnibus method. Many implementing PPs not yet issued. Gap between UU text and implementing regulation means old PP may still govern in practice. Documents tagged [Omnibus Affected] carry this risk.
C. PUTUSAN MK MODIFICATION: MK has modified or invalidated UU provisions (labour law, investment law, others). A UU pasal may still appear in text but be unenforceable. Always flag when a relevant MK decision may exist — especially for labour, land, and investment provisions.
D. PUTUSAN MA ANNULMENT: MA can annul PP, Perpres, and Permen via Hak Uji Materiil. A regulation in force may have been annulled and not yet updated in official text.
E. PERDA ENFORCEMENT REALITY: Central law formally prevails, but local government (natural resources, construction, retail, hospitality) may refuse permits based on Perda or informal Kepala Dinas policy. Jakarta Keputusan Gubernur often acts with weight of regulation due to its special administrative status. Flag when local enforcement is likely to diverge.
F. MINISTRY CONFLICT: Two PERMEN from different ministries covering the same activity are both formally valid. The enforcing ministry depends on which agency holds the licensing authority for that specific business activity. Flag which ministry is likely to be the enforcement gatekeeper.
G. SURAT EDARAN ENFORCEMENT: SE and circulars are not legislation but treated as binding instructions by implementing officials. A bank, OJK examiner, or local office may enforce an SE even if it appears to conflict with the underlying UU or PP.

PART 6: ANSWER FRAMEWORK — USE THIS STRUCTURE FOR EVERY RESPONSE

Step 1 — LEGAL LANDSCAPE:
List all potentially relevant laws, regulations, and document types in hierarchy order. Note jurisdiction (National / Province / Sector-specific).

Step 2 — PRIMARY ANSWER:
What does the highest applicable law say? Cite pasal and ayat exactly. Note the [Level] of the source.

Step 3 — TENSIONS AND COMPETING POSITIONS:
Apply all 7 arbitrage patterns. For each tension found:
• What is the tension
• Position A (e.g. central law / formal hierarchy reading)
• Position B (e.g. local enforcement / older pasal / SE reading)
• Which position is more likely to prevail in practice, and why

Step 4 — COUNTERPARTY ANALYSIS:
How might a counterparty, opposing counsel, regulator, or local official read this differently? Which pasal or regulation would they invoke?

Step 5 — LEGAL CERTAINTY RATING:
HIGH — clear law, consistent national enforcement
MEDIUM — ambiguity or conflicting sources exist
LOW — genuinely contested; enforcement unpredictable or regionally variable

Step 6 — SOURCES:
List every cited document: full title, nomor tahun, specific pasal/ayat, [Level N], jurisdiction, current status (berlaku/diubah/dicabut), and note if any MK or MA ruling may affect it.

ALWAYS:
- Cite specific regulation numbers, pasal, and ayat.
- Use the [Level N] tag from retrieved sources when applying Lex Superior.
- Flag if a provision may have been modified by UU Cipta Kerja but implementing PP not yet issued.
- Flag if an MK ruling may have modified or invalidated a UU provision.
- Mark any statement not from retrieved documents as: [General knowledge — verify against primary source]
- End with: "Verify with a licensed Indonesian attorney (Advokat) before taking legal action."

NEVER:
- Give only one answer when genuine legal ambiguity exists.
- Invent regulation numbers or pasal references.
- Skip the counterparty analysis step.
- Omit the Sources section.

FORMAT:
- ## for main section headers
- Numbered lists for steps or requirements
- Bullet points for supporting details
- **Bold** for regulation names and key legal terms
- Always end with ## Sources, then the attorney disclaimer.

SECURITY: Maximum query length 500 characters."""

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200
MAX_SEARCH_RESULTS  = 5   # chunks per document returned to AI
MAX_SEARCH_DOCS     = 3   # max distinct documents to retrieve from
MAX_SEARCH_POOL     = 25  # candidate pool fetched from DB before grouping


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


def clean_ocr_text(text):
    """
    Strip PDF extraction noise before chunking.
    Removes page numbers, running headers/footers, OCR artifacts.
    Does NOT alter legal text content.
    """
    lines = text.split('\n')
    cleaned = []
    noise_patterns = [
        re.compile(r'^\s*-\s*\d+\s*-\s*$'),             # page markers: -130-
        re.compile(r'^\s*\d+\s*$'),                       # bare page numbers: 7, 8
        re.compile(r'^\s*www\.\S+\s*$'),                  # URLs
        re.compile(r'^\s*https?://\S*\s*$'),              # URLs
        re.compile(r'^\s*PRESIDEN\s*$'),
        re.compile(r'^\s*REPUBLIK\s+INDONESIA\s*$'),
        re.compile(r'^\s*REPUBUK\s+INDONESIA\s*$'),       # OCR typo
        re.compile(r'^\s*LEMBARAN\s+NEGARA\s*$'),
        re.compile(r'^\s*TAMBAHAN\s+LEMBARAN\s+NEGARA\s*$'),
        re.compile(r'^\s*SK\s+No\s+[\w\s]+\s*$'),         # SK No 17614l A
        re.compile(r'^\s*jdih\.[\w.]*go\.id\s*$'),
    ]
    for line in lines:
        stripped = line.strip()
        if not stripped:
            if cleaned and cleaned[-1] != '':
                cleaned.append('')
            continue
        if any(p.match(stripped) for p in noise_patterns):
            continue
        cleaned.append(line)
    text = '\n'.join(cleaned)
    ocr_fixes = [
        (re.compile(r'\bREPUBUK\b'),                                              'REPUBLIK'),
        (re.compile(r'(Pasal\s+\d+(?:\s+Ayat\s*\(?\d+\)?)?)\s*\d{1,2}(?=\s|\.|,|$)'), r'\1'),
        (re.compile(r'\n{3,}'),                                                   '\n\n'),
    ]
    for pattern, replacement in ocr_fixes:
        text = pattern.sub(replacement, text)
    return text.strip()


def normalize_pasal_ref(raw_ref):
    """
    Normalize pasal_ref to a consistent format for reliable citation matching.
    'pasal 27 ayat (3)' → 'Pasal 27 Ayat (3)'
    'PASAL 27' → 'Pasal 27'
    'Pasal 27 Ayat 3' → 'Pasal 27 Ayat (3)'  (add parens around ayat number)
    """
    if not raw_ref:
        return ''
    ref = raw_ref.strip()
    # Title-case Pasal/Ayat keywords
    ref = re.sub(r'\bpasal\b',  'Pasal', ref, flags=re.IGNORECASE)
    ref = re.sub(r'\bPASAL\b',  'Pasal', ref)
    ref = re.sub(r'\bayat\b',   'Ayat',  ref, flags=re.IGNORECASE)
    ref = re.sub(r'\bAYAT\b',   'Ayat',  ref)
    # Ensure ayat number is in parens: "Ayat 3" → "Ayat (3)"
    ref = re.sub(r'Ayat\s+(\d+)(?!\))', r'Ayat (\1)', ref)
    # Collapse multiple spaces
    ref = re.sub(r'\s{2,}', ' ', ref)
    return ref.strip()


def detect_obligation_type(text):
    """
    Detect the legal character of a chunk from Indonesian legal keywords.
    6 categories, evaluated in priority order (highest first):

      prohibited   — acts that are forbidden (Larangan)
      sanction     — penalties and enforcement consequences (Sanksi)
      mandatory    — acts that must be performed (Perintah/Kewajiban)
      exception    — carve-outs and conditional exclusions (Pengecualian)
      permitted    — rights, options, authorisations (Kebolehan/Hak)
      declaratory  — definitions, status, enactment clauses (fallback)

    Priority: prohibited > sanction > mandatory > exception > permitted > declaratory
    Rationale: sanctions often co-occur with prohibitions/mandates; exception
    is lower than mandatory because "kecuali" appears inside obligation clauses.
    """
    t = text.lower()

    # ── 1. Prohibited (Larangan) ──────────────────────────────────────────────
    if any(w in t for w in [
        'dilarang', 'tidak boleh', 'tidak diperbolehkan',
        'tidak diperkenankan', 'tidak dapat', 'tidak berhak',
        'tidak sah', 'dilarang keras',
    ]):
        return 'prohibited'

    # ── 2. Sanction (Sanksi) ─────────────────────────────────────────────────
    if any(w in t for w in [
        'dikenakan sanksi', 'dikenai sanksi', 'sanksi administratif',
        'sanksi pidana', 'dipidana', 'pidana penjara', 'pidana denda',
        'denda administratif', 'dicabut izinnya', 'dicabut',
        'dibekukan', 'dibekukan izinnya', 'ganti rugi',
        'tuntutan pidana', 'pelanggaran',
    ]):
        return 'sanction'

    # ── 3. Mandatory (Perintah/Kewajiban) ────────────────────────────────────
    if any(w in t for w in [
        'wajib', 'harus', 'berkewajiban', 'diwajibkan',
        'wajib hukumnya', 'diperintahkan', 'wajib memiliki',
        'wajib melaporkan', 'wajib memenuhi',
    ]):
        return 'mandatory'

    # ── 4. Exception (Pengecualian) ───────────────────────────────────────────
    if any(w in t for w in [
        'dikecualikan', 'kecuali', 'pengecualian',
        'dikecualikan dari', 'tidak berlaku', 'tidak termasuk',
        'dalam hal tertentu', 'sepanjang tidak', 'kecuali ditentukan lain',
    ]):
        return 'exception'

    # ── 5. Permitted (Kebolehan/Hak) ─────────────────────────────────────────
    if any(w in t for w in [
        'dapat', 'berhak', 'diperbolehkan', 'diizinkan',
        'boleh', 'berwenang', 'memiliki kewenangan',
        'diberi hak', 'diberikan hak', 'berhak untuk',
    ]):
        return 'permitted'

    # ── 6. Declaratory (fallback) ─────────────────────────────────────────────
    return 'declaratory'


def extract_cross_references(text):
    """
    Extract pasal references mentioned within a chunk's text.
    e.g. 'sebagaimana dimaksud dalam Pasal 32 Ayat (1)' → ['Pasal 32 Ayat (1)']
    Returns a comma-separated string for easy storage.
    """
    pattern = re.compile(
        r'(?:Pasal|PASAL)\s+\d+[A-Z]?(?:\s+(?:Ayat|AYAT)\s*\(?\d+\)?)?',
        re.IGNORECASE
    )
    refs = list(dict.fromkeys(  # deduplicate, preserve order
        normalize_pasal_ref(m.group()) for m in pattern.finditer(text)
    ))
    return ', '.join(refs) if refs else ''


def detect_language(text):
    """
    Detect document language from the first 2000 characters.
    Returns 'id' (Bahasa Indonesia), 'zh' (Mandarin/Chinese), or 'en' (English).
    Strategy: count language-specific marker words. Highest count wins.
    Indonesian is default for legal documents — only overrides if strong signal exists.
    """
    sample = text[:2000]

    # Chinese: presence of CJK unicode block characters is definitive
    cjk_count = sum(1 for c in sample if '\u4e00' <= c <= '\u9fff')
    if cjk_count > 20:
        return 'zh'

    sample_lower = sample.lower()

    # Indonesian markers — common in legal text
    id_markers = [
        'undang-undang', 'peraturan', 'pasal', 'ayat', 'republik indonesia',
        'menimbang', 'mengingat', 'memutuskan', 'menetapkan', 'dengan rahmat',
        'presiden', 'menteri', 'pemerintah', 'ketentuan', 'pelaksanaan',
        'nomor', 'tahun', 'tentang', 'bahwa', 'sebagaimana'
    ]

    # English markers
    en_markers = [
        'whereas', 'pursuant to', 'the government', 'article', 'section',
        'regulation', 'minister', 'president', 'republic of indonesia',
        'investment', 'company', 'limited liability', 'hereby', 'stipulated',
        'provisions', 'implementing', 'decree', 'ordinance'
    ]

    id_score = sum(1 for w in id_markers if w in sample_lower)
    en_score = sum(1 for w in en_markers if w in sample_lower)

    # Indonesian wins ties — our primary corpus is Indonesian law
    if en_score > id_score and en_score >= 3:
        return 'en'
    return 'id'


def enrich_chunk(chunk):
    """
    Add obligation_type and cross_references to a chunk dict.
    Called after chunking, before INSERT.
    """
    content = chunk.get('content', '')
    chunk['pasal_ref']       = normalize_pasal_ref(chunk.get('pasal_ref', ''))
    chunk['obligation_type'] = detect_obligation_type(content)
    chunk['cross_references'] = extract_cross_references(content)
    return chunk


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
    # New columns for hierarchy reasoning — added via ALTER to preserve existing data
    cur.execute("""
        ALTER TABLE legal_documents
        ADD COLUMN IF NOT EXISTS hierarchy_level  INTEGER DEFAULT NULL,
        ADD COLUMN IF NOT EXISTS effective_date   DATE DEFAULT NULL,
        ADD COLUMN IF NOT EXISTS is_omnibus_affected BOOLEAN DEFAULT FALSE,
        ADD COLUMN IF NOT EXISTS sector           TEXT DEFAULT '',
        ADD COLUMN IF NOT EXISTS jurisdiction     TEXT DEFAULT 'National',
        ADD COLUMN IF NOT EXISTS language         TEXT DEFAULT 'id'
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_legal_logic
        ON legal_documents (hierarchy_level, effective_date, sector)
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_chunks_doc_id ON document_chunks(document_id)")
    try:
        cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
        cur.execute("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS embedding vector(768)")
        cur.execute("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS pasal_ref TEXT DEFAULT ''")
        cur.execute("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS section_header TEXT DEFAULT ''")
        cur.execute("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS page_number INTEGER DEFAULT NULL")
        cur.execute("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS obligation_type TEXT DEFAULT 'declaratory'")
        cur.execute("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS cross_references TEXT DEFAULT ''")
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


# Hierarchy level mapping per doc_type
DOC_TYPE_HIERARCHY = {
    "uud":              1,
    "tap_mpr":          2,
    "uu":               3,
    "perppu":           3,
    "pp":               4,
    "pojk":             4.5,
    "pbi":              4.5,
    "padg":             4.5,
    "seojk":            4.5,
    "perka_bkpm":       4.5,
    "peraturan_bappebti": 4.5,
    "perpres":          5,
    "kepres":           5,
    "inpres":           5,
    "permen":           6,
    "kepmen":           6,
    "peraturan_badan":  6,
    "sema":             6.5,
    "surat_edaran":     6.5,
    "perda":            7,
    "pergub":           7,
    "perbup_perwal":    8,
    "peraturan_desa":   9,
    "putusan_mk":       None,   # modifies hierarchy, not in it
    "putusan_ma":       None,
    "putusan_pengadilan": None,
    "general":          None,
}

# Sector keyword mapping
SECTOR_KEYWORDS = {
    "Banking":      ["PERBANKAN", "BANK INDONESIA", "OJK", "OTORITAS JASA KEUANGAN", "KREDIT", "DANA PIHAK KETIGA"],
    "Investment":   ["PENANAMAN MODAL", "INVESTASI", "BKPM", "PMA", "PMDN", "DNI", "OSS", "NIB"],
    "Corporate":    ["PERSEROAN TERBATAS", "PT ", "KOPERASI", "YAYASAN", "DIREKSI", "KOMISARIS", "RUPS"],
    "Labour":       ["KETENAGAKERJAAN", "TENAGA KERJA", "UMR", "UMP", "UMK", "PHK", "PESANGON", "BURUH"],
    "Tax":          ["PERPAJAKAN", "PAJAK", "DJP", "PPH", "PPN", "BEA CUKAI"],
    "Land":         ["PERTANAHAN", "AGRARIA", "HGU", "HGB", "SHM", "TATA RUANG", "RDTR"],
    "Environment":  ["LINGKUNGAN HIDUP", "AMDAL", "UKL", "UPL", "KEHUTANAN", "PERTAMBANGAN"],
    "Trade":        ["PERDAGANGAN", "EKSPOR", "IMPOR", "DISTRIBUSI", "RITEL"],
    "Finance":      ["KEUANGAN", "MODAL", "SAHAM", "EFEK", "BURSA", "ASURANSI", "FINTECH"],
    "Mining":       ["PERTAMBANGAN", "MINERBA", "IUP", "IUPK", "BATUBARA"],
    "Construction": ["KONSTRUKSI", "JASA KONSTRUKSI", "IMB", "PBG", "SLF", "BANGUNAN"],
    "Crypto":       ["KRIPTO", "ASET DIGITAL", "BAPPEBTI", "BLOCKCHAIN", "EXCHANGE"],
}

def extract_metadata_from_text(text, filename=""):
    metadata = {
        "title": "", "doc_type": "general", "nomor_tahun": "",
        "teu": "", "subjek": "", "status": "berlaku",
        "abstrak": "", "dasar_hukum": "",
        "hierarchy_level": None, "effective_date": None,
        "is_omnibus_affected": False, "sector": "", "jurisdiction": "National",
        "language": "id"
    }
    upper_text = text[:5000].upper()
    type_patterns = [
        # Primary legislation
        (r'UNDANG[- ]?UNDANG\s+(?:REPUBLIK\s+INDONESIA\s+)?(?:NOMOR|NO\.?)\s*\.?\s*(\d+)\s+TAHUN\s+(\d{4})', 'uu'),
        (r'PERATURAN\s+PEMERINTAH\s+PENGGANTI\s+UNDANG[- ]?UNDANG\s+(?:NOMOR|NO\.?)\s*\.?\s*(\d+)\s+TAHUN\s+(\d{4})', 'perppu'),
        # Government & presidential
        (r'PERATURAN\s+PEMERINTAH\s+(?:REPUBLIK\s+INDONESIA\s+)?(?:NOMOR|NO\.?)\s*\.?\s*(\d+)\s+TAHUN\s+(\d{4})', 'pp'),
        (r'PERATURAN\s+PRESIDEN\s+(?:REPUBLIK\s+INDONESIA\s+)?(?:NOMOR|NO\.?)\s*\.?\s*(\d+)\s+TAHUN\s+(\d{4})', 'perpres'),
        (r'KEPUTUSAN\s+PRESIDEN\s+(?:REPUBLIK\s+INDONESIA\s+)?(?:NOMOR|NO\.?)\s*\.?\s*(\d+)\s+TAHUN\s+(\d{4})', 'kepres'),
        (r'INSTRUKSI\s+PRESIDEN\s+(?:REPUBLIK\s+INDONESIA\s+)?(?:NOMOR|NO\.?)\s*\.?\s*(\d+)\s+TAHUN\s+(\d{4})', 'inpres'),
        # Ministerial
        (r'PERATURAN\s+MENTERI\s+\w+(?:\s+\w+)*\s+(?:REPUBLIK\s+INDONESIA\s+)?(?:NOMOR|NO\.?)\s*\.?\s*(\d+)\s+TAHUN\s+(\d{4})', 'permen'),
        (r'KEPUTUSAN\s+MENTERI\s+\w+(?:\s+\w+)*\s+(?:REPUBLIK\s+INDONESIA\s+)?(?:NOMOR|NO\.?)\s*\.?\s*(\d+)\s+TAHUN\s+(\d{4})', 'kepmen'),
        (r'PERATURAN\s+(?:BADAN|LEMBAGA|KOMISI|OTORITAS)\s+\w+(?:\s+\w+)*\s+(?:NOMOR|NO\.?)\s*\.?\s*(\d+)\s+TAHUN\s+(\d{4})', 'peraturan_badan'),
        # OJK & BI
        (r'PERATURAN\s+OTORITAS\s+JASA\s+KEUANGAN\s+(?:NOMOR|NO\.?)\s*\.?\s*([\d/\.POJK]+)\s+TAHUN\s+(\d{4})', 'pojk'),
        (r'SURAT\s+EDARAN\s+OTORITAS\s+JASA\s+KEUANGAN\s+(?:NOMOR|NO\.?)\s*\.?\s*(\d+)\s+TAHUN\s+(\d{4})', 'seojk'),
        (r'PERATURAN\s+BANK\s+INDONESIA\s+(?:NOMOR|NO\.?)\s*\.?\s*(\d+)\s+TAHUN\s+(\d{4})', 'pbi'),
        (r'PERATURAN\s+ANGGOTA\s+DEWAN\s+GUBERNUR\s+(?:NOMOR|NO\.?)\s*\.?\s*(\d+)\s+TAHUN\s+(\d{4})', 'padg'),
        (r'PERATURAN\s+(?:KEPALA\s+)?(?:BKPM|BPKM)\s+(?:NOMOR|NO\.?)\s*\.?\s*(\d+)\s+TAHUN\s+(\d{4})', 'perka_bkpm'),
        (r'PERATURAN\s+BAPPEBTI\s+(?:NOMOR|NO\.?)\s*\.?\s*(\d+)\s+TAHUN\s+(\d{4})', 'peraturan_bappebti'),
        # Court decisions
        (r'PUTUSAN\s+MAHKAMAH\s+KONSTITUSI\s+(?:NOMOR|NO\.?)\s*\.?\s*(\d+)[/\\](\d{4})', 'putusan_mk'),
        (r'PUTUSAN\s+MAHKAMAH\s+AGUNG\s+(?:NOMOR|NO\.?)\s*\.?\s*(\d+)[/\\](\d{4})', 'putusan_ma'),
        (r'PUTUSAN\s+PENGADILAN\s+(?:NEGERI|TINGGI|NIAGA|TATA\s+USAHA\s+NEGARA)\s+\w+\s+(?:NOMOR|NO\.?)\s*\.?\s*(\d+)[/\\](\d{4})', 'putusan_pengadilan'),
        # Circulars
        (r'SURAT\s+EDARAN\s+(?:MAHKAMAH\s+AGUNG|MA)\s+(?:NOMOR|NO\.?)\s*\.?\s*(\d+)\s+TAHUN\s+(\d{4})', 'sema'),
        (r'SURAT\s+EDARAN\s+(?:NOMOR|NO\.?)\s*\.?\s*(\d+)\s+TAHUN\s+(\d{4})', 'surat_edaran'),
        # Regional
        (r'PERATURAN\s+DAERAH\s+(?:PROVINSI|KABUPATEN|KOTA)\s+\w+\s+(?:NOMOR|NO\.?)\s*\.?\s*(\d+)\s+TAHUN\s+(\d{4})', 'perda'),
        (r'PERATURAN\s+GUBERNUR\s+\w+\s+(?:NOMOR|NO\.?)\s*\.?\s*(\d+)\s+TAHUN\s+(\d{4})', 'pergub'),
        (r'PERATURAN\s+(?:BUPATI|WALIKOTA)\s+\w+\s+(?:NOMOR|NO\.?)\s*\.?\s*(\d+)\s+TAHUN\s+(\d{4})', 'perbup_perwal'),
        (r'PERATURAN\s+DESA\s+\w+\s+(?:NOMOR|NO\.?)\s*\.?\s*(\d+)\s+TAHUN\s+(\d{4})', 'peraturan_desa'),
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

    # Auto-populate hierarchy_level from doc_type
    metadata["hierarchy_level"] = DOC_TYPE_HIERARCHY.get(metadata["doc_type"])

    # Auto-populate effective_date from nomor_tahun year
    if metadata["nomor_tahun"]:
        yr = re.search(r'Tahun (\d{4})', metadata["nomor_tahun"])
        if yr:
            try:
                metadata["effective_date"] = f"{yr.group(1)}-01-01"
            except Exception:
                pass

    # Auto-detect if omnibus-affected (UU Cipta Kerja)
    if re.search(r'CIPTA\s+KERJA|OMNIBUS|UU\s+NO\.?\s*11\s+TAHUN\s+2020|UU\s+NO\.?\s*6\s+TAHUN\s+2023', upper_text):
        metadata["is_omnibus_affected"] = True

    # Auto-detect sector from keywords
    sectors_found = []
    for sector_name, keywords in SECTOR_KEYWORDS.items():
        if any(kw in upper_text[:3000] for kw in keywords):
            sectors_found.append(sector_name)
    if sectors_found:
        metadata["sector"] = "; ".join(sectors_found[:3])

    # Auto-detect jurisdiction — flag regional docs
    region_match = re.search(
        r'(?:PROVINSI|KABUPATEN|KOTA|GUBERNUR|BUPATI|WALIKOTA)\s+([A-Z][A-Z\s]+?)(?:\s+NOMOR|\s+TENTANG|\n)',
        upper_text
    )
    if region_match:
        region = region_match.group(1).strip().title()
        metadata["jurisdiction"] = region
    elif metadata["doc_type"] in ("perda", "pergub", "perbup_perwal", "peraturan_desa"):
        metadata["jurisdiction"] = "Regional"
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

    # Language detection — runs on first 2000 chars of actual document text
    metadata["language"] = detect_language(text)

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
    text = clean_ocr_text(text)  # strip page numbers, headers, OCR artifacts
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
    # Enrich all chunks: normalize pasal_ref, detect obligation_type, extract cross_references
    chunk_dicts = [enrich_chunk(c) for c in chunk_dicts]
    abstrak_text = metadata.get("abstrak", "")
    if abstrak_text:
        chunk_dicts.insert(0, {"content": "[ABSTRAK / RINGKASAN DOKUMEN]\n" + abstrak_text,
                               "pasal_ref": "Abstrak", "section_header": "",
                               "obligation_type": "declaratory", "cross_references": ""})
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute(
        """INSERT INTO legal_documents (filename, title, doc_type, scope, conversation_id, total_chunks,
               nomor_tahun, teu, subjek, status, abstrak, dasar_hukum,
               hierarchy_level, effective_date, is_omnibus_affected, sector, jurisdiction, language)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
           RETURNING id, filename, title, doc_type, scope, total_chunks,
               nomor_tahun, teu, subjek, status, abstrak, dasar_hukum,
               hierarchy_level, effective_date, is_omnibus_affected, sector, jurisdiction, language, uploaded_at""",
        (filename, title or filename, doc_type, scope, conversation_id, len(chunk_dicts),
         metadata.get("nomor_tahun", ""), metadata.get("teu", ""),
         metadata.get("subjek", ""), metadata.get("status", "berlaku"),
         abstrak_text, metadata.get("dasar_hukum", ""),
         metadata.get("hierarchy_level"), metadata.get("effective_date"),
         metadata.get("is_omnibus_affected", False),
         metadata.get("sector", ""), metadata.get("jurisdiction", "National"),
         metadata.get("language", "id"))
    )
    doc = cur.fetchone()
    # Batch insert all chunks — much faster than one-by-one for large documents
    rows = [
        (doc["id"], i,
         chunk.get("content", ""),
         chunk.get("pasal_ref", ""),
         chunk.get("section_header", ""),
         chunk.get("obligation_type", "declaratory"),
         chunk.get("cross_references", ""),
         chunk.get("content", ""))
        for i, chunk in enumerate(chunk_dicts)
    ]
    psycopg2.extras.execute_batch(
        cur,
        """INSERT INTO document_chunks
           (document_id, chunk_index, content, pasal_ref, section_header,
            obligation_type, cross_references, tsv)
           VALUES (%s, %s, %s, %s, %s, %s, %s, to_tsvector('simple', %s))""",
        rows,
        page_size=500
    )
    conn.commit()
    cur.close()
    conn.close()
    return {
        "id": doc["id"], "filename": doc["filename"], "title": doc["title"],
        "doc_type": doc["doc_type"], "scope": doc["scope"],
        "total_chunks": doc["total_chunks"], "nomor_tahun": doc["nomor_tahun"],
        "teu": doc["teu"], "subjek": doc["subjek"], "status": doc["status"],
        "uploaded_at": doc["uploaded_at"].isoformat() if doc["uploaded_at"] else None
    }, None



def _group_by_document(rows, max_docs=MAX_SEARCH_DOCS, max_chunks_per_doc=MAX_SEARCH_RESULTS):
    """
    From a flat list of ranked chunks, return the best chunks grouped by document.
    Strategy:
      1. Group all chunks by document title+nomor_tahun.
      2. For each document, keep the top max_chunks_per_doc chunks (already ordered by rank).
      3. Rank documents by their best (highest) chunk score.
      4. Return chunks from the top max_docs documents.
    Ensures cross-law retrieval: PT PMA query surfaces UU Penanaman Modal +
    UU Perseroan Terbatas + UU Cipta Kerja simultaneously.
    """
    from collections import defaultdict
    doc_chunks = defaultdict(list)
    for row in rows:
        doc_key = (row['doc_title'], row.get('nomor_tahun', ''))
        doc_chunks[doc_key].append(row)

    # Best score per document (first chunk is best — rows ordered by rank desc)
    doc_best_score = {k: v[0]['rank'] for k, v in doc_chunks.items()}

    # Sort documents by best score, take top max_docs
    top_docs = sorted(doc_best_score, key=lambda k: doc_best_score[k], reverse=True)[:max_docs]

    result = []
    for doc_key in top_docs:
        result.extend(doc_chunks[doc_key][:max_chunks_per_doc])
    return result


def search_documents(query, conversation_id=None, limit=MAX_SEARCH_RESULTS):
    """
    Multi-document hybrid search.
    Phase 1: fetch MAX_SEARCH_POOL candidates from DB (vector or keyword).
    Phase 2: group by document, pick top MAX_SEARCH_DOCS by best chunk score.
    Phase 3: return up to MAX_SEARCH_RESULTS chunks per document.
    Prevents a single large document dominating results.
    """
    embedding = generate_embedding(query)
    conn = get_db()
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    SELECT_FIELDS = """
                    SELECT dc.content, dc.chunk_index,
                           COALESCE(dc.pasal_ref,'') AS pasal_ref,
                           COALESCE(dc.section_header,'') AS section_header,
                           dc.page_number,
                           ld.id AS doc_id,
                           ld.title AS doc_title, ld.filename, ld.scope,
                           ld.nomor_tahun, ld.status AS doc_status, ld.doc_type,
                           ld.hierarchy_level, ld.is_omnibus_affected,
                           ld.sector, ld.jurisdiction,
                           dc.obligation_type, dc.cross_references"""

    STATUS_BOOST = """
                               * CASE WHEN ld.status='berlaku' THEN 1.2
                                      WHEN ld.status='diubah'  THEN 1.1 ELSE 0.9 END AS rank"""

    if embedding:
        try:
            vec_str = str(embedding)
            pool = MAX_SEARCH_POOL
            if conversation_id:
                cur.execute(SELECT_FIELDS + """,
                           (1 - (dc.embedding <=> %s::vector))""" + STATUS_BOOST + """
                    FROM document_chunks dc JOIN legal_documents ld ON dc.document_id = ld.id
                    WHERE dc.embedding IS NOT NULL
                      AND (ld.scope='admin' OR (ld.scope='user' AND ld.conversation_id=%s))
                    ORDER BY dc.embedding <=> %s::vector LIMIT %s
                """, (vec_str, conversation_id, vec_str, pool))
            else:
                cur.execute(SELECT_FIELDS + """,
                           (1 - (dc.embedding <=> %s::vector))""" + STATUS_BOOST + """
                    FROM document_chunks dc JOIN legal_documents ld ON dc.document_id = ld.id
                    WHERE dc.embedding IS NOT NULL AND ld.scope='admin'
                    ORDER BY dc.embedding <=> %s::vector LIMIT %s
                """, (vec_str, vec_str, pool))

            rows = cur.fetchall()
            if rows:
                cur.close(); conn.close()
                admin = _group_by_document(
                    [r for r in rows if r['scope']=='admin'],
                    max_docs=MAX_SEARCH_DOCS, max_chunks_per_doc=limit
                )
                user = _group_by_document(
                    [r for r in rows if r['scope']=='user'],
                    max_docs=MAX_SEARCH_DOCS, max_chunks_per_doc=limit
                )
                return admin, user
        except Exception:
            pass

    # ── Keyword fallback ──────────────────────────────────────────────────────
    search_terms = [t for t in re.sub(r'[^\w\s]', ' ', query).split() if len(t) > 1]
    if not search_terms:
        cur.close(); conn.close()
        return [], []

    STATUS_BOOST_KW = """
                       * CASE WHEN ld.status='berlaku' THEN 1.5
                              WHEN ld.status='diubah'  THEN 1.2 ELSE 0.8 END AS rank"""

    if conversation_id:
        cur.execute(SELECT_FIELDS + """,
                   ts_rank_cd(dc.tsv, plainto_tsquery('simple',%s))""" + STATUS_BOOST_KW + """
            FROM document_chunks dc JOIN legal_documents ld ON dc.document_id = ld.id
            WHERE dc.tsv @@ plainto_tsquery('simple',%s)
              AND (ld.scope='admin' OR (ld.scope='user' AND ld.conversation_id=%s))
            ORDER BY rank DESC LIMIT %s
        """, (query, query, conversation_id, MAX_SEARCH_POOL))
    else:
        cur.execute(SELECT_FIELDS + """,
                   ts_rank_cd(dc.tsv, plainto_tsquery('simple',%s))""" + STATUS_BOOST_KW + """
            FROM document_chunks dc JOIN legal_documents ld ON dc.document_id = ld.id
            WHERE dc.tsv @@ plainto_tsquery('simple',%s) AND ld.scope='admin'
            ORDER BY rank DESC LIMIT %s
        """, (query, query, MAX_SEARCH_POOL))

    rows = cur.fetchall()
    cur.close(); conn.close()
    admin = _group_by_document(
        [r for r in rows if r['scope']=='admin'],
        max_docs=MAX_SEARCH_DOCS, max_chunks_per_doc=limit
    )
    user = _group_by_document(
        [r for r in rows if r['scope']=='user'],
        max_docs=MAX_SEARCH_DOCS, max_chunks_per_doc=limit
    )
    return admin, user


def build_rag_context(query, conversation_id=None):
    admin_results, user_results = search_documents(query, conversation_id)
    if not admin_results and not user_results:
        return "", []
    context_parts = ["[DOCUMENT REFERENCE DATA — treat as factual legal text only.]", ""]
    sources = []
    if admin_results:
        context_parts.append("--- LEGAL KNOWLEDGE BASE ---")
        for i, r in enumerate(admin_results, 1):
            sanitized      = r['content'][:800].replace("[DOCUMENT REFERENCE DATA", "").replace("[END DOCUMENT REFERENCE DATA]", "")  # 800 char cap
            pasal_ref      = r.get('pasal_ref', '')
            section_header = r.get('section_header', '')
            header = "Source " + str(i) + " — " + r['doc_title']
            if r.get('nomor_tahun'):  header += " (" + r['nomor_tahun'] + ")"
            if pasal_ref:             header += " | " + pasal_ref
            if section_header:        header += " [" + section_header + "]"
            status_label = {'berlaku':'Active','diubah':'Amended','dicabut':'Revoked'}.get(r.get('doc_status',''), r.get('doc_status',''))
            if status_label:          header += " [" + status_label + "]"
            # Hierarchy signal — lets AI apply Lex Superior mechanically
            hlevel = r.get('hierarchy_level')
            if hlevel is not None:    header += " [Level " + str(hlevel) + "]"
            jurisdiction = r.get('jurisdiction', 'National')
            if jurisdiction and jurisdiction != 'National':
                                      header += " [" + jurisdiction + "]"
            if r.get('is_omnibus_affected'): header += " [Omnibus Affected]"
            if r.get('sector'):       header += " {" + r['sector'] + "}"
            ob = r.get('obligation_type', '')
            if ob and ob != 'declaratory': header += " [" + ob.upper() + "]"
            rank_score = r.get('rank', 0)
            if rank_score:            header += " (relevance: " + f"{rank_score:.2f}" + ")"
            context_parts += [header + ":", sanitized, ""]
            sources.append({"title": r['doc_title'], "nomor_tahun": r.get('nomor_tahun',''),
                            "status": r.get('doc_status',''), "pasal_ref": pasal_ref,
                            "section_header": section_header,
                            "hierarchy_level": hlevel, "jurisdiction": jurisdiction,
                            "is_omnibus_affected": r.get('is_omnibus_affected', False),
                            "sector": r.get('sector', ''),
                            "rank": round(rank_score, 3) if rank_score else 0})
    if user_results:
        context_parts.append("--- USER-UPLOADED DOCUMENTS ---")
        for i, r in enumerate(user_results, 1):
            sanitized = r['content'][:800].replace("[DOCUMENT REFERENCE DATA", "").replace("[END DOCUMENT REFERENCE DATA]", "")  # 800 char cap
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
    for m in history[-6:]:  # last 6 messages — caps token cost as conversations grow
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


@app.route("/api/chunk", methods=["GET"])
def get_chunk():
    """
    Return source text for the citation side panel.
    Three match strategies: nomor_tahun+pasal_ref → title+pasal_ref → title only.
    """
    nomor_tahun = request.args.get("nomor_tahun", "").strip()
    pasal_ref   = request.args.get("pasal_ref",   "").strip()
    doc_title   = request.args.get("doc_title",   "").strip()

    if not pasal_ref and not doc_title:
        return jsonify({"error": "pasal_ref or doc_title required"}), 400

    conn = get_db()
    cur  = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    SELECT = """
        SELECT dc.content, dc.pasal_ref, dc.section_header, dc.chunk_index,
               ld.title, ld.nomor_tahun, ld.doc_type, ld.status,
               ld.hierarchy_level, ld.jurisdiction, ld.teu
        FROM document_chunks dc
        JOIN legal_documents ld ON dc.document_id = ld.id
    """

    # Strategy 1: nomor_tahun + pasal_ref (most precise)
    if nomor_tahun and pasal_ref:
        cur.execute(SELECT + """
            WHERE ld.nomor_tahun ILIKE %s AND dc.pasal_ref ILIKE %s
            ORDER BY dc.chunk_index LIMIT 3
        """, (f"%{nomor_tahun}%", f"%{pasal_ref}%"))
        rows = cur.fetchall()
        if rows:
            cur.close(); conn.close()
            return jsonify({"chunks": [dict(r) for r in rows], "matched_by": "nomor_tahun+pasal_ref"})

    # Strategy 2: title + pasal_ref
    if doc_title and pasal_ref:
        cur.execute(SELECT + """
            WHERE ld.title ILIKE %s AND dc.pasal_ref ILIKE %s
            ORDER BY dc.chunk_index LIMIT 3
        """, (f"%{doc_title}%", f"%{pasal_ref}%"))
        rows = cur.fetchall()
        if rows:
            cur.close(); conn.close()
            return jsonify({"chunks": [dict(r) for r in rows], "matched_by": "title+pasal_ref"})

    # Strategy 3: title only — return opening chunk
    if doc_title:
        cur.execute(SELECT + """
            WHERE ld.title ILIKE %s ORDER BY dc.chunk_index LIMIT 1
        """, (f"%{doc_title}%",))
        rows = cur.fetchall()
        if rows:
            cur.close(); conn.close()
            return jsonify({"chunks": [dict(r) for r in rows], "matched_by": "title_only"})

    cur.close(); conn.close()
    return jsonify({"chunks": [], "matched_by": "none"})


try:
    init_db()
except Exception:
    pass
