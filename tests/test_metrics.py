from src.metrics import compute_faithfulness

def test_faithfulness_grounded_answer():
    answer = "The VPN certificate expired causing error VPN-CERT-7731"
    contexts = ["VPN client certificate expired. Error code VPN-CERT-7731 indicates an expired certificate."]
    score = compute_faithfulness(answer, contexts)
    assert score > 0.5

def test_faithfulness_hallucinated_answer():
    answer = "Contact NASA headquarters for this issue"
    contexts = ["VPN client certificate expired."]
    score = compute_faithfulness(answer, contexts)
    assert score < 0.3

def test_faithfulness_empty():
    assert compute_faithfulness("", []) == 0.0
