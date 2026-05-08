# 🛡️ Enterprise Knowledge Copilot

[![Python](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![LangGraph](https://img.shields.io/badge/Agent-LangGraph-green.svg)](https://github.com/langchain-ai/langgraph)
[![Ollama](https://img.shields.io/badge/LLM-Ollama-lightgrey.svg)](https://ollama.ai/)
[![Streamlit](https://img.shields.io/badge/UI-Streamlit-red.svg)](https://streamlit.io/)
[![SentenceTransformers](https://img.shields.io/badge/Embedding-BGE_Small-orange.svg)](#)

A production-grade, privacy-first, 100% local AI copilot designed for enterprise environments. 

Large organizations drown in scattered knowledge—unread PDFs, unmaintained org charts, and endless duplicate IT tickets. **Enterprise Knowledge Copilot** solves this by ingesting internal company data, aggressively stripping sensitive PII, and using a local LLaMA 3.2 model to synthesize answers, troubleshoot complex network issues, and **automatically file deduplicated support tickets.**

**Because it runs entirely on local hardware, highly sensitive corporate data never leaves your infrastructure.**

---

## ✨ Enterprise-Grade Capabilities (Out of the Box)

If you've seen one "RAG Tutorial," you've seen them all. This repository is different. We engineered a massive, resilient architecture that bridges the gap between prototype and production.

### 🧠 1. Cognitive Architecture & Advanced Retrieval
- **Three-Headed Memory Engine**: Runs Semantic Search (ChromaDB), Keyword Search (BM25), and Relational Graph Traversal (NetworkX) simultaneously.
- **Query Expansion**: Automatically maps user abbreviations to system names (e.g., "vpn" → `NEXAVPN`, "ci" → `BUILDPIPE-CI`) before searching, dramatically improving recall.
- **Cross-Encoder Reranking**: We fuse results using Reciprocal Rank Fusion (RRF), then pass the top 10 through a highly accurate `ms-marco-MiniLM-L-6-v2` cross-encoder to guarantee the LLM only sees the absolute best 5 chunks.
- **Metadata-Aware Filtering**: Executes granular SQL-like searches against vector databases (e.g., "Show me P1 tickets").

### 🤖 2. Autonomous LangGraph Agent (6 Tools)
The LLM is an orchestrated state machine equipped with 6 specialized tools:
1. `tool_search_docs`: Scans policies and runbooks.
2. `tool_search_tickets`: Searches historical incident reports.
3. `tool_filtered_tickets`: Uses ChromaDB metadata tags to filter tickets.
4. `tool_summarize`: Broadly searches all data sources to provide high-level overviews.
5. `tool_multihop`: Synthesizes data across the handbook, tickets, and graph simultaneously for complex reasoning.
6. `tool_create_ticket`: **Takes Action**. It writes tickets to the CSV, but first runs a Cosine Similarity check (>0.75 threshold) to instantly block duplicate ticket spam.

### 🛡️ 3. Security & Compliance
- **Zero-Leak PII Shield**: Before text hits the database, a spaCy NER pipeline alongside Regex hunts down and replaces real names, emails, and phone numbers with `[REDACTED]` tags.
- **Automated Verification**: Our `test_pii.py` suite scans all 645 indexed chunks to cryptographically prove 0 PII leaks.
- **Audit Logging**: Every query, tool selection, and metric is permanently logged to `audit.jsonl` for compliance reviews.

### 🖥️ 4. Premium UI/UX Polish
- **Real-Time Confidence Calibration**: Every response displays a UI badge calculating total confidence (`40% retrieval score + 30% faithfulness + 30% context relevance`).
- **System Health Dashboard**: A real-time sidebar displaying indexed chunks, graph edges, and last ingestion timestamps.
- **Reasoning Trace & Feedback**: Users can expand a debug window to see the agent's internal tool routing, and provide 👍/👎 feedback logged directly to the system.
- **Smart Escalation**: If the agent can't find an answer, it bypasses the LLM to prevent hallucinations, queries the Knowledge Graph for the exact system owner, and returns their contact info.
- **Onboarding Walkthrough**: Includes a guided 10-step mode for training new employees.

---

<img width="1222" height="769" alt="Screenshot From 2026-05-08 15-03-11" src="https://github.com/user-attachments/assets/f6db6d84-881c-4ed6-bf59-4bf15b9e415d" />


<img width="1251" height="927" alt="Screenshot From 2026-05-08 15-02-54" src="https://github.com/user-attachments/assets/3235d19f-1882-4e3f-9609-e796744ae2f8" />

<img width="954" height="947" alt="Screenshot From 2026-05-08 15-02-31" src="https://github.com/user-attachments/assets/0243712c-0a25-4a9e-a3a1-4bb241ce291a" />

<img width="954" height="947" alt="Screenshot From 2026-05-08 15-02-14" src="https://github.com/user-attachments/assets/c7e26e9d-96b7-4e73-8690-edc3b8a15429" />



## 💻 Installation & Setup

Because this system runs a local LLM, you need to run two separate processes: the Ollama server, and the Python app.

### Step 1: Install Dependencies
Clone the repository and install the required Python packages.

```bash
git clone https://github.com/ichhit-ai/enterprise-knowledge-copilot.git
cd enterprise-knowledge-copilot

# Install Python dependencies
pip install -r requirements.txt

# Download the spaCy NLP model (CRITICAL for PII redaction)
python -m spacy download en_core_web_sm
```

### Step 2: Start the Ollama Server (Terminal 1)
You must have [Ollama](https://ollama.com/) installed on your machine.

```bash
# Start the Ollama background service
ollama serve
```
*(Leave this terminal window open and running!)*

### Step 3: Pull the LLaMA Model (Terminal 2)
In a new terminal window, download the LLaMA 3.2 model that powers the agent's brain:

```bash
ollama pull llama3.2
```

### Step 4: Build the "Brain" (Ingestion)
Before running the app, you need to ingest the raw CSV and TXT files. This script will strip the PII, generate embeddings, and build the ChromaDB, BM25, and NetworkX indices.

```bash
# Takes ~30 seconds. You only need to run this once (or whenever your raw data changes).
PYTHONPATH=. python3 src/ingest.py
```

### Step 5: Launch the Copilot UI
Start the Streamlit web interface:

```bash
PYTHONPATH=. streamlit run src/app.py
```
*The app will automatically open in your browser at `http://localhost:8501`*

---

## 🧪 Evaluation & Testing Benchmarks

Enterprise systems require provable metrics. This project includes a robust evaluation framework with a 25-question ground-truth test set.

**Run the Retrieval Benchmark (Precision/Recall):**
```bash
PYTHONPATH=. python3 eval/eval.py
```
*Current Performance: P@5 = 0.456 | R@5 = 0.880 | Source Hit Rate = 80.0%*

**Verify PII Redaction (Zero Leaks):**
```bash
PYTHONPATH=. python3 eval/test_pii.py
```

---

## 📚 Deep Dive Architecture

Want to know exactly how the math behind Reciprocal Rank Fusion works, or how the LangGraph state machine handles multi-hop reasoning?

Read the massive, comprehensive technical deep-dive: [REPORT.md](./REPORT.md)

*(For formal architecture diagrams, sequence flows, and LLD, see [DESIGN_DOCUMENT.md](./DESIGN_DOCUMENT.md))*

---

## 🤝 Contributing
Contributions are welcome! Please feel free to submit a Pull Request. If you plan to add a new tool to the agent, please ensure it includes appropriate fallback logic in `agent.py`.

## 📄 License
This project is licensed under the MIT License.
