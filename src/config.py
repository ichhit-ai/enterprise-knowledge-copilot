import os

# ── Global configuration for performance bypasses ─────────────────────────────
BYPASS_ENABLED = True  # Enable template bypasses for error codes, owners, and tickets
CACHE_ENABLED = True   # Enable semantic caching for repetitive queries
CACHE_PATH = os.path.join(os.path.dirname(__file__), "..", ".index", "response_cache.pkl")

SYSTEMS = ["AUTH-GATEWAY", "NEXACORE-DB", "NEXAVPN", "CLOUDSYNC-S3", "NEXAMAIL",
           "BUILDPIPE-CI", "NEXASEC-FW", "HRPORTAL", "MONITORX", "NEXABACKUP",
           "APIGATEWAY-V2", "TICKETSYS"]

SYSTEM_OWNERS = {
    "AUTH-GATEWAY": ("Marcus Thompson", "m.thompson@nexacorp.com"),
    "NEXACORE-DB": ("Derek Walsh", "d.walsh@nexacorp.com"),
    "NEXAVPN": ("Jordan Okafor", "j.okafor@nexacorp.com"),
    "CLOUDSYNC-S3": ("Chloe Fontaine", "c.fontaine@nexacorp.com"),
    "NEXAMAIL": ("Marcus Thompson", "m.thompson@nexacorp.com"),
    "BUILDPIPE-CI": ("Nathan Xu", "n.xu@nexacorp.com"),
    "NEXASEC-FW": ("Oliver Pine", "o.pine@nexacorp.com"),
    "HRPORTAL": ("Tomas Brewer", "t.brewer@nexacorp.com"),
    "MONITORX": ("Nathan Xu", "n.xu@nexacorp.com"),
    "NEXABACKUP": ("Farida Hassan", "f.hassan@nexacorp.com"),
    "APIGATEWAY-V2": ("Raj Patel", "r.patel@nexacorp.com"),
    "TICKETSYS": ("Tomas Brewer", "t.brewer@nexacorp.com"),
}

TICKET_KEYWORDS = ["ticket", "incident", "tkt-", "escalated", "resolved",
                    "in progress", "who filed", "how many tickets", "open tickets",
                    "recent issues", "ticket status", "bug", "bugs", "reported",
                    "customer support", "customer tickets", "issue", "issues",
                    "problem", "problems", "error", "errors", "failed", "broken"]
SUMMARY_KEYWORDS = ["summarize", "summary", "overview", "explain the policy",
                     "break down", "what are all", "list all", "give me a rundown"]
CREATE_TICKET_KEYWORDS = ["file a ticket", "create a ticket", "raise a ticket",
                           "open a ticket", "submit a ticket", "log a ticket",
                           "new ticket", "file ticket", "create ticket"]
FILTER_KEYWORDS = ["p1 ticket", "p2 ticket", "p3 ticket", "p4 ticket",
                   "priority 1", "priority 2", "high priority", "critical ticket",
                   "tickets from", "tickets about", "filter ticket"]
MULTIHOP_KEYWORDS = ["what should i do about", "how do i fix", "how to resolve",
                     "troubleshoot", "steps to fix", "what causes and how"]

# Role-based access tiers
ROLE_ACCESS = {
    "Employee": {"max_tier": 2},
    "Manager": {"max_tier": 3},
    "IT Admin": {"max_tier": 4},
}

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
AUDIT_PATH = os.path.join(os.path.dirname(__file__), "..", "logs", "audit.jsonl")
