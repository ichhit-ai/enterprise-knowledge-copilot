#!/usr/bin/env python3
"""Evaluation script: runs 25 test questions, computes P@5, R@5, and prints a table."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from src.retriever import Retriever

EVAL_DIR = os.path.dirname(__file__)
TEST_SET = os.path.join(EVAL_DIR, "test_set.json")


def run_eval():
    print("Loading retriever...")
    retriever = Retriever()

    with open(TEST_SET) as f:
        tests = json.load(f)

    print(f"\nRunning evaluation on {len(tests)} questions...\n")
    print(f"{'ID':>3} {'Type':<12} {'P@5':>5} {'R@5':>5} {'Source Hit':>10}  Question")
    print("-" * 90)

    total_p5, total_r5, total_hits = 0, 0, 0

    for t in tests:
        qid = t["id"]
        qtype = t["type"]
        question = t["question"]
        expected_source = t["source"]
        expected_answer = t["expected_answer"].lower()

        # Route to appropriate search
        if qtype == "ticket":
            docs, score = retriever.search_tickets(question, k=5)
        elif qtype == "summary":
            docs, score, _ = retriever.search_all(question, k=5)
        else:
            docs, score = retriever.search_docs(question, k=5)

        # Check if expected source appears in top-5
        source_hit = any(expected_source in d.metadata.get("source", "") for d in docs)

        # Check if expected answer keywords appear in retrieved docs
        relevant_count = 0
        answer_words = set(expected_answer.split())
        for d in docs:
            content = d.page_content.lower()
            if sum(1 for w in answer_words if w in content) >= len(answer_words) * 0.3:
                relevant_count += 1

        p5 = relevant_count / 5 if docs else 0
        r5 = min(relevant_count / max(1, 1), 1.0)  # Assume 1 relevant doc
        hit = "✅" if source_hit else "❌"

        total_p5 += p5
        total_r5 += r5
        total_hits += int(source_hit)

        print(f"{qid:>3} {qtype:<12} {p5:>5.2f} {r5:>5.2f} {hit:>10}  {question[:55]}")

    n = len(tests)
    print("-" * 90)
    print(f"{'AVG':<16} {total_p5/n:>5.2f} {total_r5/n:>5.2f} {total_hits}/{n} hits")
    print(f"\nPrecision@5: {total_p5/n:.3f}")
    print(f"Recall@5:    {total_r5/n:.3f}")
    print(f"Source Hit Rate: {total_hits/n:.1%}")

    # Save results
    results = {
        "precision_at_5": round(total_p5 / n, 3),
        "recall_at_5": round(total_r5 / n, 3),
        "source_hit_rate": round(total_hits / n, 3),
    }
    with open(os.path.join(EVAL_DIR, "eval_results.json"), "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nResults saved to eval/eval_results.json")


if __name__ == "__main__":
    run_eval()
