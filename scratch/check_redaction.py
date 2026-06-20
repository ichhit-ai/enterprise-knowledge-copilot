import pickle
import os

for idx_dir in [".index", ".index_full"]:
    path = os.path.join(idx_dir, "bm25.pkl")
    if os.path.exists(path):
        print(f"\n--- Checking index: {idx_dir} ---")
        with open(path, "rb") as f:
            bm25, bm25_docs = pickle.load(f)
        tickets = [doc for doc in bm25_docs if doc.metadata.get("ticket_id") == "TKT-10003"]
        if tickets:
            print("Found TKT-10003:")
            print("Page content:", tickets[0].page_content)
            print("Metadata:", tickets[0].metadata)
        else:
            print("TKT-10003 not found in this index.")
            # Print a few random tickets
            t_docs = [doc for doc in bm25_docs if doc.metadata.get("ticket_id")][:2]
            for t in t_docs:
                print(f"Ticket {t.metadata.get('ticket_id')}:")
                print("Page content:", t.page_content)
