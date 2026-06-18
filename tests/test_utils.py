import pytest
from src.utils import classify_query, extract_ticket_id, extract_entities

def test_classify_ticket_by_id():
    assert classify_query("What happened with TKT-10033?") == "tickets"

def test_classify_create_ticket():
    assert classify_query("File a ticket for VPN issues") == "create_ticket"

def test_classify_summary():
    assert classify_query("Summarize the data classification policy") == "summarize"

def test_classify_docs_default():
    assert classify_query("How do I request time off?") == "docs"

def test_classify_issue_keyword():
    assert classify_query("What issues does Jordan Okafor have?") == "tickets"
    assert classify_query("what problems are reported?") == "tickets"

def test_extract_ticket_id_tkt_format():
    assert extract_ticket_id("What is TKT-10033?") == "TKT-10033"

def test_extract_ticket_id_bare_number():
    # bare numbers preceded by "ticket" etc. are captured
    assert extract_ticket_id("Look up ticket 10033") == "10033"

def test_extract_entities_with_hyphen():
    ents = extract_entities("Who owns AUTH-GATEWAY?")
    assert "AUTH-GATEWAY" in ents

def test_extract_entities_without_hyphen():
    ents = extract_entities("Who owns auth gateway?")
    assert "AUTH-GATEWAY" in ents
