# Enterprise Knowledge Copilot — Data Sources & Data Engineering

> **Project:** Enterprise Knowledge Copilot  
> **Total Records Ingested:** 645 chunks | **PII Leaks:** 0 (verified)

---

## 1. Data Sources Overview

| # | File | Format | Description | Records |
|---|---|---|---|---|
| 1 | `nexacorp_handbook.txt` | TXT | HR policies, IT guidelines, SLA definitions, data classification rules, error code references | ~40 chunks |
| 2 | `nexacorp_vpn_auth_runbook.txt` | TXT | IT troubleshooting procedures mapped to specific error codes (ERR-AUTH-*, VPN-CERT-*) | ~25 chunks |
| 3 | `nexacorp_tickets.csv` | CSV | Historical IT incident reports with ticket IDs, priorities (P1-P4), error codes, resolutions | 236 rows |
| 4 | `nexacorp_org_chart.csv` | CSV | Entity-relationship triples mapping employees to systems (e.g., Marcus Thompson → AUTH-GATEWAY) | ~50 triples |

These four sources cover the three pillars of enterprise knowledge: **Policy** (Handbook/Runbook), **History** (Tickets), and **Ownership** (Org Chart).

---

## 2. Data Engineering Pipeline (6 Steps)

### Step 1: Raw File Extraction
Text files are read as raw strings. CSV files are parsed row-by-row using `csv.DictReader`, with each row flattened into a single searchable text string.

### Step 2: PII Sanitization
A two-stage redaction pipeline strips sensitive data before indexing:
- **Stage 1 (Regex):** Detects and masks emails and phone numbers with `[REDACTED_EMAIL]` / `[REDACTED_PHONE]` tokens.
- **Stage 2 (NER):** spaCy's `en_core_web_sm` model detects PERSON entities. Names are sorted by length (longest first) to prevent partial leaks — redacting "Dr. Sarah Jenkins" before "Sarah" ensures no last names are exposed.

### Step 3: Text Chunking
Unstructured text is split using a rolling window: **800 characters** per chunk with **150 character overlap**. The 800-char window keeps content within the embedding model's 512-token limit (~200 tokens), while overlap ensures boundary sentences appear in both adjacent chunks.

### Step 4: Metadata Enrichment
Each document is tagged with structured metadata for filtered search:

| Field | Source | Example | Enables |
|---|---|---|---|
| `priority` | Tickets | `"P1"` | "Show me P1 tickets" |
| `status` | Tickets | `"Resolved"` | Status-based filtering |
| `error_code` | Tickets | `"ERR-AUTH-9092"` | Error code lookups |
| `system` | Auto-detected | `"AUTH-GATEWAY"` | System-specific queries |

### Step 5: Knowledge Graph Construction
Org chart triples are loaded as directed edges in NetworkX (`G.add_edge(entity, target, relation=relationship)`). Additionally, automatic entity co-occurrence mining discovers implicit relationships by linking NER-extracted entities that appear in the same CSV row.

### Step 6: Multi-Mode Indexing
Processed chunks are indexed depending on the active configuration tier:
* **Local Sandbox Mode**: Chunks are committed to three local files/folders.
* **Production Mode**: Chunks are bulk-indexed into a containerized **Elasticsearch** cluster.

| Index Type | Local Technology | Production Technology | Storage Location | Retrieval Purpose |
|---|---|---|---|---|
| **Vector Index** | ChromaDB + `bge-small-en-v1.5` | Elasticsearch 8.x (Dense Vector) | `.index/chroma/` or Elasticsearch Cluster | Semantic similarity queries |
| **Keyword Index** | BM25Okapi | Elasticsearch 8.x (Sparse BM25) | `.index/bm25.pkl` or Elasticsearch Cluster | Exact term/error code matching |
| **Graph Index** | NetworkX DiGraph | NetworkX DiGraph | `.index/graph.pkl` | Relational network traversal |

---

## 3. Data Quality Challenges Resolved

| Challenge | Resolution |
|---|---|
| Malformed CSV rows with unquoted commas | Custom pre-processing script to detect and quote offending fields |
| Partial PII leaks ("Dr. [REDACTED] Jenkins") | Sort entities by string length (longest first) before redaction |
| Missing metadata fields in ticket rows | Graceful conditional injection with `.strip()` handling |
