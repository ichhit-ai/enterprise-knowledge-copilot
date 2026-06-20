import os
from elasticsearch import Elasticsearch

es = Elasticsearch("http://localhost:9200")
print("Ping:", es.ping())
if es.ping():
    # Get index information
    print("Indices:", list(es.indices.get(index="*").keys()))
    
    # Query ticket 10897
    res = es.search(index="nexacorp_docs", body={
        "query": {
            "match_all": {}
        },
        "size": 1
    })
    print("One doc metadata sample:")
    for hit in res['hits']['hits']:
        print("ID:", hit['_id'])
        print("Metadata:", hit['_source'].get('metadata'))
        print("Page Content:", hit['_source'].get('page_content')[:100])
    
    # Query with ticket_id
    res_tkt = es.search(index="nexacorp_docs", body={
        "query": {
            "terms": {
                "metadata.ticket_id": ["10897", "TKT-10897"]
            }
        }
    })
    print("Search by ticket_id hits count:", res_tkt['hits']['total'])
    print("Search by ticket_id results:")
    for hit in res_tkt['hits']['hits']:
        print("ID:", hit['_id'])
        print("Metadata:", hit['_source'].get('metadata'))
        print("Page Content:", hit['_source'].get('page_content')[:200])
