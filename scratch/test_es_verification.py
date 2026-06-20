from elasticsearch import Elasticsearch
es = Elasticsearch("http://localhost:9200")
res = es.search(index="nexacorp_docs", body={
    "query": {
        "match": {
            "page_content": "TKT-10982"
        }
    }
})
print("Hits:", res["hits"]["total"]["value"])
if res["hits"]["hits"]:
    print("Found TKT-10982 in Elasticsearch index!")
    print("Content:", res["hits"]["hits"][0]["_source"]["page_content"])
else:
    print("TKT-10982 not found in ES index.")
