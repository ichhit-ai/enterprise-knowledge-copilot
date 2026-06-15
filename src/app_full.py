import streamlit as st
import time
import json
import os
import pickle
import asyncio
from datetime import datetime
from src.agent import build_agent

INDEX_DIR = os.path.join(os.path.dirname(__file__), "..", ".index_full")
DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")

st.set_page_config(page_title="NexaCorp Copilot", page_icon="🛡️", layout="wide")

st.markdown("""
<style>
    @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&display=swap');
    .stApp { background: linear-gradient(135deg, #0f0c29, #302b63, #24243e); font-family: 'Inter', sans-serif; }
    .main-title { color: #e0e0ff; font-size: 2.2rem; font-weight: 700; margin-bottom: 0; }
    .sub-title { color: #8888bb; font-size: 0.95rem; margin-top: 0; }
    .citation-box {
        background: rgba(255,255,255,0.04); border-left: 3px solid #6c63ff;
        padding: 10px 14px; margin: 6px 0; border-radius: 6px; font-size: 0.82rem; color: #bbb;
    }
    .metric-row { display: flex; gap: 10px; margin: 8px 0; flex-wrap: wrap; }
    .metric-pill {
        display: inline-flex; align-items: center; gap: 4px;
        background: rgba(108,99,255,0.15); color: #a5a0ff;
        padding: 5px 14px; border-radius: 14px; font-size: 0.8rem; font-weight: 500;
    }
    .metric-pill.good { background: rgba(46,204,113,0.15); color: #6deca9; }
    .metric-pill.warn { background: rgba(241,196,15,0.15); color: #f1d96c; }
    .metric-pill.bad { background: rgba(231,76,60,0.15); color: #f0918a; }
    .tool-badge {
        display: inline-block; background: rgba(108,99,255,0.25); color: #c5c0ff;
        padding: 3px 10px; border-radius: 10px; font-size: 0.75rem; font-weight: 600;
        margin-bottom: 6px;
    }
    .confidence-badge {
        display: inline-block; padding: 4px 12px; border-radius: 12px;
        font-size: 0.85rem; font-weight: 600; margin: 4px 0;
    }
    .conf-high { background: rgba(46,204,113,0.2); color: #6deca9; }
    .conf-med { background: rgba(241,196,15,0.2); color: #f1d96c; }
    .conf-low { background: rgba(231,76,60,0.2); color: #f0918a; }
    div[data-testid="stChatMessage"] { background: rgba(255,255,255,0.02) !important; border-radius: 12px; }
    .stDivider { border-color: rgba(255,255,255,0.08) !important; }
    .suggestion-btn { margin: 4px; }
    .health-stat { color: #a5a0ff; font-size: 0.85rem; padding: 2px 0; }
    .onboard-card {
        background: rgba(108,99,255,0.1); border: 1px solid rgba(108,99,255,0.3);
        padding: 12px; border-radius: 8px; margin: 6px 0;
    }
</style>
""", unsafe_allow_html=True)

# ── Sidebar: Health Dashboard, Role, Settings ──
with st.sidebar:
    st.markdown("### ⚙️ Settings")

    # Retrieval Engine selection
    retrieval_mode = st.selectbox(
        "🔍 Retrieval Engine",
        ["Edge Sandbox (Local Chroma+BM25)", "Enterprise Cluster (Elasticsearch+MCP)"],
        help="Select search infrastructure (local vs production scale)"
    )
    mode_key = "elasticsearch" if "Elasticsearch" in retrieval_mode else "local"

    if mode_key == "elasticsearch":
        st.markdown("#### ☁️ Elastic Cloud Management")
        # Check environment configuration
        es_url = os.environ.get("ES_URL")
        es_api_key = os.environ.get("ES_API_KEY")
        if not es_url:
            st.info("💡 `ES_URL` is not configured in environment/secrets. Click below to try local Elasticsearch at localhost:9200.")
        if st.button("Index Data to Elastic Cloud", use_container_width=True):
            with st.spinner("Indexing documents..."):
                try:
                    from scripts.ingest_elasticsearch import ingest_to_elasticsearch
                    ingest_to_elasticsearch()
                    st.success("Successfully indexed documents!")
                except Exception as e:
                    st.error(f"Failed to index: {e}")
        st.divider()

    # Role-based access
    role = st.selectbox("👤 Access Role", ["Employee", "Manager", "IT Admin"],
                       help="Controls document access level based on data classification tiers")


    # Explain reasoning toggle
    show_reasoning = st.checkbox("🧠 Show Reasoning Trace", value=False,
                                help="Display the agent's decision-making process")

    st.divider()

    # System health dashboard
    st.markdown("### 📊 System Health")

    try:
        with open(os.path.join(INDEX_DIR, "bm25.pkl"), "rb") as f:
            _, bm25_docs = pickle.load(f)
        total_docs = len(bm25_docs)
    except Exception:
        total_docs = "?"

    try:
        with open(os.path.join(INDEX_DIR, "graph.pkl"), "rb") as f:
            graph = pickle.load(f)
        nodes, edges = graph.number_of_nodes(), graph.number_of_edges()
    except Exception:
        nodes, edges = "?", "?"

    try:
        import csv
        ticket_path = os.path.join(DATA_DIR, "customer_support_tickets_200k.csv")
        if not os.path.exists(ticket_path):
            ticket_path = os.path.join(DATA_DIR, "customer_support_tickets_200k.csv.bak")
        with open(ticket_path) as f:
            ticket_count = sum(1 for _ in csv.DictReader(f))
    except Exception:
        ticket_count = "?"

    try:
        with open(os.path.join(INDEX_DIR, "ingest_meta.pkl"), "rb") as f:
            meta = pickle.load(f)
        last_ingest = meta.get("timestamp", "Unknown")[:16]
    except Exception:
        last_ingest = "Unknown"

    st.markdown(f'<p class="health-stat">📄 Documents indexed: <b>{total_docs}</b></p>', unsafe_allow_html=True)
    st.markdown(f'<p class="health-stat">🕸️ Graph nodes: <b>{nodes}</b> | edges: <b>{edges}</b></p>', unsafe_allow_html=True)
    st.markdown(f'<p class="health-stat">🎫 Tickets: <b>{ticket_count}</b></p>', unsafe_allow_html=True)
    st.markdown(f'<p class="health-stat">🕐 Last ingest: <b>{last_ingest}</b></p>', unsafe_allow_html=True)

    st.divider()

    # Onboarding mode
    if st.button("🎓 New Employee Onboarding", use_container_width=True):
        st.session_state.onboarding_mode = True

    # Feedback stats
    feedback_path = os.path.join(os.path.dirname(__file__), "..", "feedback.jsonl")
    if os.path.exists(feedback_path):
        try:
            with open(feedback_path) as f:
                entries = [json.loads(l) for l in f if l.strip()]
            thumbs_up = sum(1 for e in entries if e.get("rating") == "up")
            thumbs_down = sum(1 for e in entries if e.get("rating") == "down")
            st.markdown(f"### 📈 Feedback\n👍 {thumbs_up} | 👎 {thumbs_down}")
        except Exception:
            pass

# ── Main Area ──
st.markdown('<p class="main-title">🛡️ Enterprise Knowledge Copilot</p>', unsafe_allow_html=True)
st.markdown('<p class="sub-title">Privacy-first · 100% Local · Hybrid Retrieval (Chroma + BM25 + Graph) · 6 Agent Tools · Cross-Encoder Reranking</p>', unsafe_allow_html=True)
st.divider()

@st.cache_resource
def get_agent():
    return build_agent(index_dir=INDEX_DIR)

agent, retriever = get_agent()

if "messages" not in st.session_state:
    st.session_state.messages = []
if "feedback_given" not in st.session_state:
    st.session_state.feedback_given = set()

# ── Onboarding Mode ──
if st.session_state.get("onboarding_mode"):
    st.markdown("## 🎓 New Employee Onboarding Guide")
    onboarding_questions = [
        "How do I set up VPN access?",
        "How do I register my MFA device?",
        "How do I submit a ticket on TICKETSYS?",
        "What is the escalation process for incidents?",
        "Who are the key system owners I should know?",
        "What is the data classification policy?",
        "How do I request hardware or software?",
        "What are the remote work requirements?",
        "How do I access HRPORTAL for leave requests?",
        "What should I do if I suspect a security incident?",
    ]
    for i, q in enumerate(onboarding_questions):
        with st.expander(f"**{i+1}.** {q}", expanded=(i == 0)):
            if st.button(f"Get answer", key=f"onboard_{i}"):
                with st.spinner("Searching..."):
                    try:
                        result = asyncio.run(agent.ainvoke({"question": q, "role": role, "mode": mode_key}))
                    except Exception as e:
                        st.warning("⚠️ Remote Elasticsearch connection failed. Falling back to Local Edge Sandbox.")
                        result = asyncio.run(agent.ainvoke({"question": q, "role": role, "mode": "local"}))

                st.markdown(result.get("answer", "No answer available."))
    if st.button("← Back to Chat"):
        st.session_state.onboarding_mode = False
        st.rerun()
    st.stop()

# ── Chat History Display ──
for idx, msg in enumerate(st.session_state.messages):
    with st.chat_message(msg["role"]):
        if msg.get("tool_used"):
            st.markdown(f'<span class="tool-badge">{msg["tool_used"]}</span>', unsafe_allow_html=True)
        st.markdown(msg["content"])
        if msg.get("metrics_html"):
            st.markdown(msg["metrics_html"], unsafe_allow_html=True)
        if msg.get("citations"):
            with st.expander("📄 Sources"):
                for c in msg["citations"]:
                    source_name = c["source"]
                    if source_name == "nexacorp_tickets.csv":
                        source_name = "customer_support_tickets_200k.csv"
                    st.markdown(f'<div class="citation-box"><b>{source_name}</b><br>{c["snippet"][:250]}...</div>', unsafe_allow_html=True)
        if msg.get("reasoning") and show_reasoning:
            with st.expander("🧠 Reasoning Trace"):
                for step in msg["reasoning"]:
                    st.markdown(f"- {step}")
        # Feedback buttons
        if msg["role"] == "assistant" and idx not in st.session_state.feedback_given:
            col1, col2, col3 = st.columns([1, 1, 20])
            with col1:
                if st.button("👍", key=f"up_{idx}"):
                    _log_feedback(msg, "up", idx)
            with col2:
                if st.button("👎", key=f"dn_{idx}"):
                    _log_feedback(msg, "down", idx)


def _log_feedback(msg, rating, idx):
    feedback_path = os.path.join(os.path.dirname(__file__), "..", "feedback.jsonl")
    entry = {
        "timestamp": datetime.now().isoformat(),
        "question": st.session_state.messages[idx - 1]["content"] if idx > 0 else "",
        "answer": msg["content"][:500],
        "rating": rating,
    }
    try:
        with open(feedback_path, "a") as f:
            f.write(json.dumps(entry) + "\n")
        st.session_state.feedback_given.add(idx)
        st.toast(f"{'👍' if rating == 'up' else '👎'} Feedback recorded!")
    except Exception:
        pass


def score_class(val, good=0.5, bad=0.2):
    if val >= good:
        return "good"
    elif val >= bad:
        return "warn"
    return "bad"


# ── Query Suggestions (when chat is empty) ──
if not st.session_state.messages:
    st.markdown("### 💡 Try asking:")
    suggestions = [
        "What is error code ERR-AUTH-9092?",
        "How do I request time off?",
        "Who manages AUTH-GATEWAY?",
        "Show me tickets related to VPN issues",
        "Summarize the data classification policy",
        "How do I fix VPN-CERT-7731?",
    ]
    cols = st.columns(3)
    for i, s in enumerate(suggestions):
        with cols[i % 3]:
            if st.button(s, key=f"sug_{i}", use_container_width=True):
                st.session_state.suggestion_query = s
                st.rerun()

# Handle suggestion click
if "suggestion_query" in st.session_state:
    prompt = st.session_state.pop("suggestion_query")
else:
    prompt = st.chat_input("Ask about policies, error codes, tickets, or request a summary...")

if prompt:
    st.session_state.messages.append({"role": "user", "content": prompt})
    with st.chat_message("user"):
        st.markdown(prompt)

    with st.chat_message("assistant"):
        with st.spinner("Routing query → retrieving → generating..."):
            t0 = time.time()
            # Build history for multi-turn
            history = []
            for m in st.session_state.messages[-10:]:
                if m["role"] == "user":
                    history.append({"question": m["content"]})
                elif m["role"] == "assistant" and history:
                    history[-1]["answer"] = m["content"][:200]

            try:
                result = asyncio.run(agent.ainvoke({"question": prompt, "history": history, "role": role, "mode": mode_key}))
            except Exception as e:
                st.warning("⚠️ Remote Elasticsearch connection failed. Falling back to Local Edge Sandbox.")
                result = asyncio.run(agent.ainvoke({"question": prompt, "history": history, "role": role, "mode": "local"}))
            elapsed = time.time() - t0


        tool_used = result.get("tool_used", "")
        answer = result.get("answer", "Something went wrong.")
        citations = result.get("citations", [])
        retrieval = result.get("retrieval_score", 0)
        faith = result.get("faithfulness", 0)
        sem_sim = result.get("semantic_similarity", 0)
        ctx_rel = result.get("context_relevance", 0)
        confidence = result.get("confidence", 0)
        reasoning = result.get("reasoning_trace", [])

        if tool_used:
            st.markdown(f'<span class="tool-badge">{tool_used}</span>', unsafe_allow_html=True)

        # Confidence badge
        if confidence > 0:
            conf_cls = "conf-high" if confidence >= 0.6 else ("conf-med" if confidence >= 0.3 else "conf-low")
            st.markdown(f'<span class="confidence-badge {conf_cls}">🎯 I am {confidence:.0%} confident in this answer</span>', unsafe_allow_html=True)

        st.markdown(answer)

        r_cls = score_class(retrieval, 0.025, 0.015)
        f_cls = score_class(faith, 0.5, 0.25)
        s_cls = score_class(sem_sim, 0.7, 0.4)
        c_cls = score_class(ctx_rel, 0.6, 0.3)
        t_cls = "good" if elapsed < 15 else ("warn" if elapsed < 30 else "bad")

        metrics_html = f"""<div class="metric-row">
            <span class="metric-pill {r_cls}">📊 Retrieval: {retrieval:.3f}</span>
            <span class="metric-pill {f_cls}">🎯 Faithfulness: {faith:.0%}</span>
            <span class="metric-pill {s_cls}">🔗 Semantic Sim: {sem_sim:.3f}</span>
            <span class="metric-pill {c_cls}">📐 Context Rel: {ctx_rel:.3f}</span>
            <span class="metric-pill {t_cls}">⏱ {elapsed:.1f}s</span>
        </div>"""
        st.markdown(metrics_html, unsafe_allow_html=True)

        if citations:
            with st.expander("📄 Sources"):
                for c in citations:
                    source_name = c["source"]
                    if source_name == "nexacorp_tickets.csv":
                        source_name = "customer_support_tickets_200k.csv"
                    st.markdown(f'<div class="citation-box"><b>{source_name}</b><br>{c["snippet"][:250]}...</div>', unsafe_allow_html=True)

        if reasoning and show_reasoning:
            with st.expander("🧠 Reasoning Trace"):
                for step in reasoning:
                    st.markdown(f"- {step}")

        st.session_state.messages.append({
            "role": "assistant", "content": answer,
            "citations": citations, "tool_used": tool_used,
            "metrics_html": metrics_html, "reasoning": reasoning,
        })
