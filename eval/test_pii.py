#!/usr/bin/env python3
"""Verify PII redaction completeness: scan indexed documents for leaked PII."""
import os
import re
import sys
import pickle

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

INDEX_DIR = os.path.join(os.path.dirname(__file__), "..", ".index")

# Known PII from the handbook
KNOWN_EMAILS = [
    "p.nair@nexacorp.com", "l.kovacs@nexacorp.com", "sarah.j@nexacorp.com",
    "m.thompson@nexacorp.com", "j.okafor@nexacorp.com", "d.walsh@nexacorp.com",
    "c.fontaine@nexacorp.com", "n.xu@nexacorp.com", "s.reyes@nexacorp.com",
    "k.marsh@nexacorp.com", "t.brewer@nexacorp.com", "r.patel@nexacorp.com",
    "f.hassan@nexacorp.com", "o.pine@nexacorp.com", "anita.s@nexacorp.com",
    "oncall-schedule@nexacorp.com", "phishing@nexacorp.com",
]

KNOWN_PHONES = [
    "+1-312-555-0194", "+1-312-555-0271", "+1-312-555-0188",
]

EMAIL_RE = re.compile(r'[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+')
PHONE_RE = re.compile(r'\+?\d[\d\s\-()]{7,}\d')


def test_pii_redaction():
    print("Loading indexed documents...")
    with open(os.path.join(INDEX_DIR, "bm25.pkl"), "rb") as f:
        _, docs = pickle.load(f)

    print(f"Scanning {len(docs)} documents for PII leaks...\n")

    leaked_emails = []
    leaked_phones = []
    total_redacted_emails = 0
    total_redacted_phones = 0
    total_redacted_persons = 0

    for i, doc in enumerate(docs):
        content = doc.page_content

        # Count redacted tokens (good)
        total_redacted_emails += content.count("[REDACTED_EMAIL]")
        total_redacted_phones += content.count("[REDACTED_PHONE]")
        total_redacted_persons += content.count("[REDACTED_PERSON]")

        # Scan for leaked emails
        emails = EMAIL_RE.findall(content)
        for email in emails:
            if email not in ("[REDACTED_EMAIL]",):
                leaked_emails.append((i, email, doc.metadata.get("source", "?")))

        # Scan for leaked phones
        phones = PHONE_RE.findall(content)
        for phone in phones:
            if "[REDACTED" not in phone:
                leaked_phones.append((i, phone, doc.metadata.get("source", "?")))

    print(f"Redaction counts:")
    print(f"  [REDACTED_EMAIL]:  {total_redacted_emails}")
    print(f"  [REDACTED_PHONE]:  {total_redacted_phones}")
    print(f"  [REDACTED_PERSON]: {total_redacted_persons}")
    print()

    if leaked_emails:
        print(f"⚠️  LEAKED EMAILS ({len(leaked_emails)}):")
        for idx, email, source in leaked_emails[:10]:
            print(f"    Doc {idx} ({source}): {email}")
    else:
        print("✅ No email addresses leaked")

    if leaked_phones:
        print(f"⚠️  LEAKED PHONES ({len(leaked_phones)}):")
        for idx, phone, source in leaked_phones[:10]:
            print(f"    Doc {idx} ({source}): {phone}")
    else:
        print("✅ No phone numbers leaked")

    total_leaks = len(leaked_emails) + len(leaked_phones)
    print(f"\n{'✅ PII REDACTION VERIFIED — PASSED' if total_leaks == 0 else f'❌ PII REDACTION FAILED — {total_leaks} leaks found'}")
    return total_leaks == 0


if __name__ == "__main__":
    success = test_pii_redaction()
    sys.exit(0 if success else 1)
