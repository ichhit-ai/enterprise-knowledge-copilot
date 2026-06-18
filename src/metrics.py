import json
from datetime import datetime
from src.config import SYSTEM_OWNERS, AUDIT_PATH

STOP_WORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "dare", "ought",
    "to", "of", "in", "for", "on", "with", "at", "by", "from", "as",
    "into", "through", "during", "before", "after", "above", "below",
    "between", "and", "but", "or", "nor", "not", "so", "yet", "both",
    "either", "neither", "each", "every", "all", "any", "few", "more",
    "most", "other", "some", "such", "no", "only", "own", "same",
    "than", "too", "very", "just", "because", "if", "when", "while",
    "that", "this", "these", "those", "it", "its", "i", "me", "my",
    "we", "our", "you", "your", "he", "him", "his", "she", "her",
    "they", "them", "their", "what", "which", "who", "whom",
})

def compute_faithfulness(answer, contexts):
    if not contexts or not answer:
        return 0.0
    answer_tokens = {w.strip(".,?!;:\"'()") for w in answer.lower().split()}
    answer_tokens = answer_tokens - STOP_WORDS - {""}
    
    ctx_tokens = set()
    for c in contexts:
        tokens = {w.strip(".,?!;:\"'()") for w in c.lower().split()}
        ctx_tokens.update(tokens - STOP_WORDS - {""})
        
    if not answer_tokens:
        return 0.0
    overlap = answer_tokens & ctx_tokens
    return round(len(overlap) / len(answer_tokens), 2)

def compute_confidence(retrieval_score, faithfulness, context_relevance):
    """Weighted confidence: 40% retrieval, 30% faithfulness, 30% context relevance."""
    r_norm = min(retrieval_score / 0.035, 1.0) if retrieval_score else 0
    return round(0.4 * r_norm + 0.3 * faithfulness + 0.3 * context_relevance, 2)

def build_smart_escalation(entities, graph=None):
    """Generate actionable escalation message with contact info."""
    contacts = []
    for ent in entities:
        upper = ent.upper()
        for sys_name, (owner, email) in SYSTEM_OWNERS.items():
            if sys_name in upper or upper in sys_name:
                contacts.append(f"• **{sys_name}** issues → Contact {owner} ({email})")
                break
    msg = ("⚠️ I couldn't find relevant information in the internal documentation to "
           "answer this question confidently.\n\n")
    if contacts:
        msg += "**Recommended contacts based on your query:**\n"
        msg += "\n".join(contacts)
        msg += "\n\n"
    msg += ("**Next steps:** Please raise a ticket on TICKETSYS for further assistance, "
            "or contact the Help Desk Lead Tomas Brewer (t.brewer@nexacorp.com).")
    return msg

def log_audit(state):
    """Append query + response to audit trail."""
    try:
        entry = {
            "timestamp": datetime.now().isoformat(),
            "question": state.get("question", ""),
            "tool_used": state.get("tool_used", ""),
            "faithfulness": state.get("faithfulness", 0),
            "semantic_similarity": state.get("semantic_similarity", 0),
            "context_relevance": state.get("context_relevance", 0),
            "confidence": state.get("confidence", 0),
            "role": state.get("role", "Employee"),
            "answer_length": len(state.get("answer", "")),
        }
        with open(AUDIT_PATH, "a") as f:
            f.write(json.dumps(entry) + "\n")
    except Exception:
        pass
