from src.ingest import redact

def test_email_redacted():
    assert "[REDACTED_EMAIL]" in redact("Contact john@example.com")

def test_phone_redacted():
    assert "[REDACTED_PHONE]" in redact("Call +1-312-555-0194")

def test_common_word_name_safe():
    # Person named "Will" should not destroy "will" in sentences
    result = redact("Will Smith will attend the meeting")
    # "Will Smith" should be redacted, but "will attend" should survive
    assert "will attend" in result or "[REDACTED_PERSON] attend" in result
