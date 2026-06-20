# 📖 User Guide: Enterprise Knowledge Copilot

Welcome to the **Enterprise Knowledge Copilot** User Guide. This document provides step-by-step instructions on setting up, running, and demonstrating the system's capabilities for the hackathon.

---

## 🛠️ System Overview & Architecture Modes

The Copilot is built to run in two distinct architecture modes:
1. **Local Edge Sandbox (Developer/Local Mode):**
   * Uses **ChromaDB** for vector storage.
   * Uses local **Ollama** (`llama3.2:3b`) for text generation.
   * Perfect for local execution on developer laptops.
2. **Production Enterprise Cluster (Production Mode):**
   * Migrates from ChromaDB to a containerized **Elasticsearch** cluster.
   * Automatically switches from Ollama to a high-performance **vLLM** endpoint if configured.
   * Eliminates memory limits (no OOM crashes) and improves retrieval speed by **140x** on large datasets.

---

## 🚀 Step 1: Prerequisites & Dependencies

Ensure your Linux environment has the following installed:
* **Python 3.10+**
* **Podman** or **Docker** (to run the Elasticsearch cluster)
* **Ollama** (for local model inference)

### Install Python Libraries:
Run the following commands in your terminal:
```bash
pip install -r requirements.txt
python3 -m spacy download en_core_web_sm
```

---

## 📂 Step 2: Ingesting the Enterprise Data

To build the semantic, keyword, and knowledge graph indices, run the ingestion pipeline.

### Local Ingestion (ChromaDB + NetworkX):
```bash
PYTHONPATH=. python3 src/ingest.py
```
*This processes the documents in `data/`, redacts PII, chunks the data, and saves the index files in `.index/`.*

### Enterprise Ingestion (Elasticsearch):
Ensure Elasticsearch is running first:
```bash
podman start elasticsearch
```
Then run the Elasticsearch ingestion script:
```bash
PYTHONPATH=. python3 scripts/ingest_elasticsearch.py
```

---

## 🖥️ Step 3: Running the Streamlit Chat Application

Launch the interactive web UI:
```bash
PYTHONPATH=. streamlit run src/app.py
```
*Your browser will open automatically at `http://localhost:8501`.*

---

## 🎭 Step 4: Live Demo Walkthrough (For the Jury)

Use the following sequence of queries to demonstrate the core capabilities:

### 1. General Policy Q&A (Local Document Search)
* **Query:** *"How do I request time off?"*
* **What to highlight:** The Copilot retrieves the policy from the HR handbook, cites the source, and displays the confidence score badge.

### 🔒 2. Role-Based Access Control (RBAC) & PII Redaction
* **Query:** *"Show me tickets related to VPN issues"*
* **Action:** Toggle the **Role Switcher** in the sidebar.
  * Set Role to **Employee**: The system blocks the lookup and outputs an `🔒 Access Denied` warning.
  * Set Role to **IT Admin**: The system successfully searches the ticket base and lists the incidents.
* **PII Check:** Note that all customer names, emails, and phone numbers in the retrieved tickets are automatically replaced with `[REDACTED]` tags.

### ⚡ 3. Direct Bypass (Sub-Second Latency)
* **Query:** *"Who owns the server database system?"*
* **What to highlight:** The agent uses relational graph traversal to bypass the LLM entirely, answering in under 10ms with direct owner contact details.

---

## 📈 Step 5: Running the Validation Benchmarks

Prove the scalability and robustness of your solution with the built-in testing suites:

### 1. PII Redaction Compliance Audit
Run the automated audit script to verify that 0% of PII is leaked across all database chunks:
```bash
PYTHONPATH=. python3 eval/test_pii.py
```

### 2. Retrieval Accuracy Benchmark
Measure search recall, precision, and source hit rates:
```bash
PYTHONPATH=. python3 eval/eval.py
```
