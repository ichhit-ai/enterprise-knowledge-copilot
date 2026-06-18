import os
import csv
import portalocker
from datetime import datetime
from src.utils import ERROR_CODE_RE, PRIORITY_RE
from src.config import DATA_DIR, SYSTEMS, SYSTEM_OWNERS

async def tool_create_ticket(state, retriever_local, retriever_mcp):
    q = state["question"]
    mode = state.get("mode", "local")
    entities = state.get("entities", [])
    trace = state.get("reasoning_trace", [])
    trace.append("🆕 Tool: Create Ticket — checking for duplicates first...")

    # Deduplication: search for similar open tickets
    if mode == "elasticsearch":
        filter_dict = {
            "terms": {
                "metadata.source": ["nexacorp_tickets.csv", "customer_support_tickets_200k.csv", "customer_support_tickets_200k.csv.bak"]
            }
        }
        docs, score = await retriever_mcp.search_docs(q, index_name="nexacorp_docs", k=3, filter_dict=filter_dict)
    else:
        docs, score = retriever_local.search_tickets(q, k=3)

    open_tickets = [d for d in docs if "Resolved" not in d.page_content]
    if open_tickets:
        sim = retriever_local.compute_semantic_similarity(q, open_tickets[0].page_content)
        if sim > 0.75:
            trace.append(f"⚠️ Duplicate detected! Similarity={sim:.2f} with existing ticket")
            snippet = open_tickets[0].page_content[:200]
            answer = (f"⚠️ **Potential duplicate detected!** An existing open ticket appears "
                     f"very similar (similarity: {sim:.0%}):\n\n> {snippet}\n\n"
                     f"Please check the existing ticket before creating a new one.")
            citations = [{"source": "TICKETSYS", "snippet": snippet}]
            return {"documents": open_tickets, "graph_context": "", "retrieval_score": score,
                    "citations": citations, "tool_used": "🔄 Duplicate Check",
                    "answer": answer, "reasoning_trace": trace,
                    "faithfulness": 1.0, "semantic_similarity": sim,
                    "context_relevance": sim, "confidence": sim}

    # Create new ticket
    tickets_path = os.path.join(DATA_DIR, "nexacorp_tickets.csv")
    max_id = 10100
    try:
        with open(tickets_path) as f:
            for row in csv.DictReader(f):
                tid = row.get("ticket_id", "").strip().strip('"')
                if tid.startswith("TKT-"):
                    num = int(tid.split("-")[1])
                    max_id = max(max_id, num)
    except Exception:
        pass

    new_id = f"TKT-{max_id + 1}"
    error_codes = ERROR_CODE_RE.findall(q)
    error_code = error_codes[0] if error_codes else "UNKNOWN"
    system = ""
    for s in SYSTEMS:
        if s.lower() in q.lower() or s in q.upper():
            system = s
            break
    priority = "P3"
    pm = PRIORITY_RE.search(q)
    if pm:
        priority = pm.group().upper()

    new_row = {
        "ticket_id": new_id,
        "employee_name": "Copilot Auto-Filed",
        "issue_description": q,
        "status": "In Progress",
        "exact_error_code": error_code,
        "created_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "priority": priority,
        "resolution_notes": "",
    }

    try:
        with open(tickets_path, "a", newline="") as f:
            portalocker.lock(f, portalocker.LOCK_EX)
            writer = csv.DictWriter(f, fieldnames=list(new_row.keys()), quoting=csv.QUOTE_ALL)
            writer.writerow(new_row)
            portalocker.unlock(f)
        trace.append(f"✅ Created ticket {new_id} in local CSV")
    except Exception as e:
        trace.append(f"❌ Failed to create ticket: {e}")
        new_id = "FAILED"

    # If elasticsearch mode, index it directly into Elasticsearch in real-time
    if mode == "elasticsearch" and new_id != "FAILED":
        try:
            from elasticsearch import Elasticsearch
            es = Elasticsearch("http://localhost:9200")
            if es.ping():
                parts = [f"{k}: {v}" for k, v in new_row.items() if v]
                text = " | ".join(parts)
                meta = {
                    "source": "nexacorp_tickets.csv",
                    "type": "structured",
                    "priority": priority,
                    "created_at": new_row["created_at"],
                    "status": "In Progress",
                    "error_code": error_code,
                    "ticket_id": new_id
                }
                if system:
                    meta["system"] = system
                
                vector = retriever_mcp.embeddings_model.embed_query(text)
                
                es.index(index="nexacorp_docs", id=f"doc_{new_id}", document={
                    "page_content": text,
                    "embeddings": vector,
                    "metadata": meta
                })
                trace.append(f"✅ Indexed ticket {new_id} in Elasticsearch cluster")
        except Exception as es_err:
            trace.append(f"⚠️ Failed to index ticket in Elasticsearch: {es_err}")

    owner_info = ""
    if system in SYSTEM_OWNERS:
        name, email = SYSTEM_OWNERS[system]
        owner_info = f" It has been assigned to {name} ({email})."

    answer = (f"✅ **Ticket Created: {new_id}**\n\n"
             f"- **Priority:** {priority}\n"
             f"- **Error Code:** {error_code}\n"
             f"- **System:** {system or 'Unidentified'}\n"
             f"- **Status:** In Progress\n"
             f"- **Created:** {new_row['created_at']}\n\n"
             f"Your issue has been logged in TICKETSYS.{owner_info}")

    return {"documents": [], "graph_context": "", "retrieval_score": 0,
            "citations": [], "tool_used": "🆕 Ticket Created",
            "answer": answer, "reasoning_trace": trace,
            "faithfulness": 1.0, "semantic_similarity": 0.5,
            "context_relevance": 0.5, "confidence": 0.9}
