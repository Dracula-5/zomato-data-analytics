// PageRank over who ordered from whom, next to the plain order count.
MATCH (r:Restaurant)
WHERE r.pagerank IS NOT NULL
RETURN r.id AS id, r.name AS name, r.city AS city, r.pagerank AS pagerank,
       COUNT { (:Order)-[:FROM]->(r) } AS orders
ORDER BY pagerank DESC, id
