import os
import re
import csv
import glob
import spacy
import pickle
import networkx as nx
from datetime import datetime
from rank_bm25 import BM25Okapi
from langchain_community.vectorstores import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_core.documents import Document

nlp = spacy.load("en_core_web_sm")

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
INDEX_DIR = os.path.join(os.path.dirname(__file__), "..", ".index")

EMAIL_RE = re.compile(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+')
PHONE_RE = re.compile(r'\+?\d[\d\s\-()]{7,}\d')

# Known system names for metadata tagging
SYSTEMS = ["AUTH-GATEWAY", "NEXACORE-DB", "NEXAVPN", "CLOUDSYNC-S3", "NEXAMAIL",
           "BUILDPIPE-CI", "NEXASEC-FW", "HRPORTAL", "MONITORX", "NEXABACKUP",
           "APIGATEWAY-V2", "TICKETSYS"]


def redact(text):
    text = EMAIL_RE.sub("[REDACTED_EMAIL]", text)
    text = PHONE_RE.sub("[REDACTED_PHONE]", text)
    doc = nlp(text)
    persons = sorted(set(e.text for e in doc.ents if e.label_ == "PERSON"), key=len, reverse=True)
    for p in persons:
        text = text.replace(p, "[REDACTED_PERSON]")
    return text


def detect_systems(text):
    """Extract system names mentioned in text for metadata tagging."""
    upper = text.upper()
    return [s for s in SYSTEMS if s in upper]


def load_text_files(data_dir):
    docs = []
    for path in glob.glob(os.path.join(data_dir, "*.txt")):
        with open(path) as f:
            raw = f.read()
        name = os.path.basename(path)
        chunks = chunk_text(raw, 800, 150)
        for i, chunk in enumerate(chunks):
            systems = detect_systems(chunk)
            meta = {"source": name, "chunk": i, "type": "handbook"}
            if systems:
                meta["system"] = systems[0]  # Primary system reference
            docs.append(Document(
                page_content=redact(chunk),
                metadata=meta
            ))
    return docs


def load_csv_files(data_dir):
    docs = []
    for path in glob.glob(os.path.join(data_dir, "*.csv")):
        name = os.path.basename(path)
        if "original" in name:  # Skip backup files
            continue
        with open(path) as f:
            reader = csv.DictReader(f)
            for row in reader:
                parts = []
                for k, v in row.items():
                    if v is None:
                        continue
                    val = " ".join(v) if isinstance(v, list) else str(v)
                    if val.strip():
                        parts.append(f"{k}: {val}")
                text = " | ".join(parts)

                # Build rich metadata
                meta = {"source": name, "type": "structured"}
                systems = detect_systems(text)
                if systems:
                    meta["system"] = systems[0]

                # Extract ticket-specific metadata
                if "ticket" in name.lower():
                    if row.get("priority"):
                        meta["priority"] = row["priority"].strip().strip('"')
                    if row.get("created_at"):
                        meta["created_at"] = row["created_at"].strip().strip('"')
                    if row.get("status"):
                        meta["status"] = row["status"].strip().strip('"')
                    if row.get("exact_error_code"):
                        meta["error_code"] = row["exact_error_code"].strip().strip('"')
                    if row.get("ticket_id"):
                        meta["ticket_id"] = row["ticket_id"].strip().strip('"')

                docs.append(Document(
                    page_content=redact(text),
                    metadata=meta
                ))
    return docs


def load_pdf_files(data_dir):
    try:
        import fitz
    except ImportError:
        return []
    docs = []
    for path in glob.glob(os.path.join(data_dir, "*.pdf")):
        name = os.path.basename(path)
        pdf = fitz.open(path)
        for page_num, page in enumerate(pdf):
            raw = page.get_text()
            if not raw.strip():
                continue
            chunks = chunk_text(raw, 800, 150)
            for i, chunk in enumerate(chunks):
                systems = detect_systems(chunk)
                meta = {"source": name, "page": page_num, "chunk": i, "type": "pdf"}
                if systems:
                    meta["system"] = systems[0]
                docs.append(Document(
                    page_content=redact(chunk),
                    metadata=meta
                ))
    return docs


def chunk_text(text, size=800, overlap=150):
    chunks, start = [], 0
    while start < len(text):
        end = start + size
        chunks.append(text[start:end])
        start = end - overlap
    return chunks


def build_graph_from_csv(data_dir):
    G = nx.DiGraph()
    org_path = os.path.join(data_dir, "nexacorp_org_chart.csv")
    if os.path.exists(org_path):
        with open(org_path) as f:
            for row in csv.DictReader(f):
                e, r, t = row.get("entity","").strip(), row.get("relationship","").strip(), row.get("target","").strip()
                if e and r and t:
                    G.add_edge(e, t, relation=r)

    for path in glob.glob(os.path.join(data_dir, "*.csv")):
        if "org_chart" in path or "original" in path:
            continue
        with open(path) as f:
            reader = csv.DictReader(f)
            fields = reader.fieldnames or []
            for row in reader:
                for field in fields:
                    raw = row.get(field)
                    if raw is None:
                        continue
                    val = " ".join(raw) if isinstance(raw, list) else str(raw)
                    val = val.strip()
                    if not val:
                        continue
                    doc = nlp(val)
                    ents = [e.text for e in doc.ents if e.label_ in ("ORG", "PERSON", "PRODUCT", "GPE")]
                    for i in range(len(ents)):
                        for j in range(i+1, len(ents)):
                            if G.has_edge(ents[i], ents[j]):
                                G[ents[i]][ents[j]]["weight"] = G[ents[i]][ents[j]].get("weight", 1) + 1
                            else:
                                G.add_edge(ents[i], ents[j], relation="CO_MENTIONED", weight=1)
    return G


def build_index():
    os.makedirs(INDEX_DIR, exist_ok=True)

    # Record ingest timestamp
    with open(os.path.join(INDEX_DIR, "ingest_meta.pkl"), "wb") as f:
        pickle.dump({"timestamp": datetime.now().isoformat()}, f)

    print("loading documents...")
    docs = load_text_files(DATA_DIR) + load_csv_files(DATA_DIR) + load_pdf_files(DATA_DIR)
    print(f"  {len(docs)} chunks ready")

    print("building chroma index...")
    embeddings = HuggingFaceEmbeddings(model_name="BAAI/bge-small-en-v1.5")
    chroma_path = os.path.join(INDEX_DIR, "chroma")
    if os.path.exists(chroma_path):
        import shutil
        shutil.rmtree(chroma_path)
    Chroma.from_documents(docs, embeddings, persist_directory=chroma_path)

    print("building bm25 index...")
    corpus = [d.page_content.lower().split() for d in docs]
    bm25 = BM25Okapi(corpus)
    with open(os.path.join(INDEX_DIR, "bm25.pkl"), "wb") as f:
        pickle.dump((bm25, docs), f)

    print("building graph...")
    G = build_graph_from_csv(DATA_DIR)
    with open(os.path.join(INDEX_DIR, "graph.pkl"), "wb") as f:
        pickle.dump(G, f)

    # Count ticket-specific stats
    ticket_count = sum(1 for d in docs if d.metadata.get("source", "").endswith("tickets.csv"))

    print(f"done. {len(docs)} docs indexed, {G.number_of_nodes()} graph nodes, "
          f"{G.number_of_edges()} edges, {ticket_count} ticket records")


if __name__ == "__main__":
    build_index()
