from src.metrics import compute_faithfulness, compute_confidence

def apply_rbac_filter(docs, role, trace):
    """Filter out Tier 3 Confidential documents for Employee role."""
    if role != "Employee":
        return docs
    filtered = []
    for d in docs:
        content_lower = d.page_content.lower()
        if any(w in content_lower for w in ["tier 3", "compensation", "rating scale", "salary"]):
            trace.append(f"🔒 Security Gate: Filtered out Tier 3 Confidential document from source '{d.metadata.get('source', '?')}'")
        else:
            filtered.append(d)
    return filtered

def build_graph_context(entities, retriever_local, trace):
    """Shared graph context builder."""
    if not entities:
        return ""
    triples = retriever_local.search_graph(entities)
    if triples:
        trace.append(f"🕸️ Graph: Found {len(triples)} entity relationships")
        return "\n".join(f"{s} --[{r}]--> {t}" for s, r, t in triples)
    return ""

def format_document(d):
    content = d.page_content
    # Clean up pipe-separated values for better readability and token reduction
    if " | " in content and (":" in content or "ticket" in d.metadata.get("source", "").lower()):
        pairs = {}
        for part in content.split(" | "):
            if ":" in part:
                k_v = part.split(":", 1)
                pairs[k_v[0].strip().lower()] = k_v[1].strip()
        
        tid = pairs.get("ticket_id") or pairs.get("ticket id") or pairs.get("ticket") or ""
        name = "[REDACTED_PERSON]" if (pairs.get("customer_name") or pairs.get("employee_name") or pairs.get("customer name") or pairs.get("employee name")) else ""
        desc = pairs.get("issue_description") or pairs.get("issue description") or pairs.get("description") or ""
        status = pairs.get("status") or ""
        priority = pairs.get("priority") or ""
        err = pairs.get("exact_error_code") or pairs.get("error_code") or pairs.get("exact error code") or ""
        res = pairs.get("resolution_notes") or pairs.get("resolution notes") or pairs.get("resolution") or ""
        
        parts = []
        if tid: parts.append(f"Ticket ID: {tid}")
        if name: parts.append(f"Name: {name}")
        if desc: parts.append(f"Issue: {desc}")
        if status: parts.append(f"Status: {status}")
        if priority: parts.append(f"Priority: {priority}")
        if err: parts.append(f"Error Code: {err}")
        if res: parts.append(f"Resolution: {res}")
        
        return "\n".join(parts)
    return content

def format_ctx(documents):
    return "\n\n---\n\n".join(format_document(d) for d in documents)

def compute_metrics(state, answer, retriever_local):
    """Compute all evaluation metrics for a response."""
    q = state["question"]
    docs = state.get("documents", [])
    contexts = [format_document(d) for d in docs]
    faith = compute_faithfulness(answer, contexts)
    sem_sim = retriever_local.compute_semantic_similarity(q, answer)
    ctx_rel = retriever_local.compute_context_relevance(q, docs)
    conf = compute_confidence(state.get("retrieval_score", 0), faith, ctx_rel)
    return {
        "faithfulness": faith,
        "semantic_similarity": sem_sim,
        "context_relevance": ctx_rel,
        "confidence": conf,
    }
