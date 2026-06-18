import re
import spacy
from src.config import SYSTEMS, CREATE_TICKET_KEYWORDS, SUMMARY_KEYWORDS, FILTER_KEYWORDS, TICKET_KEYWORDS, MULTIHOP_KEYWORDS

try:
    nlp = spacy.load("en_core_web_sm")
except OSError:
    import spacy.cli
    spacy.cli.download("en_core_web_sm")
    nlp = spacy.load("en_core_web_sm")

ERROR_CODE_RE = re.compile(r'[A-Z]{2,}[\-][A-Z]*[\-]?\w+')
TICKET_RE = re.compile(r'TKT-\d+', re.IGNORECASE)
PRIORITY_RE = re.compile(r'\bP[1-4]\b', re.IGNORECASE)

def extract_ticket_id(q):
    # Match TKT-XXXXX (case-insensitive) or standalone 5-6 digit numbers
    # Also match numbers preceded by ticket/id/number/no/#
    tkt_match = re.search(r'\bTKT-(\d+)\b', q, re.IGNORECASE)
    if tkt_match:
        return f"TKT-{tkt_match.group(1)}"
    
    # Standalone 5-6 digit number
    num_match = re.search(r'\b(\d{5,6})\b', q)
    if num_match:
        return num_match.group(1)
        
    # Preceded by ticket/id/number indicators
    label_match = re.search(r'\b(?:ticket|id|number|no\.?|#)\s*(\d+)\b', q, re.IGNORECASE)
    if label_match:
        return label_match.group(1)
        
    return None

def extract_entities(text):
    doc = nlp(text)
    ents = [e.text for e in doc.ents if e.label_ in ("PERSON", "ORG", "PRODUCT", "GPE")]
    codes = ERROR_CODE_RE.findall(text)
    
    # Normalize input text by removing spaces and hyphens
    normalized_text = re.sub(r'[\s\-]', '', text.upper())
    matched = []
    for s in SYSTEMS:
        normalized_sys = re.sub(r'[\s\-]', '', s.upper())
        if normalized_sys in normalized_text:
            matched.append(s)
            
    return list(set(ents + codes + matched))

def classify_query(question):
    if extract_ticket_id(question) is not None:
        return "tickets"
    q = question.lower()
    if any(kw in q for kw in CREATE_TICKET_KEYWORDS):
        return "create_ticket"
    if any(kw in q for kw in SUMMARY_KEYWORDS):
        return "summarize"
    if TICKET_RE.search(question):
        return "tickets"
    if any(kw in q for kw in FILTER_KEYWORDS):
        return "filtered_tickets"
    if any(kw in q for kw in TICKET_KEYWORDS):
        return "tickets"
    return "docs"

def needs_multihop(question):
    q = question.lower()
    return any(kw in q for kw in MULTIHOP_KEYWORDS)
