// A customer with plenty of history, for the recommendation example.
MATCH (u:User)-[:ORDERED]->(:Order)-[:FROM]->(:Restaurant)-[:SIMILAR_TO]-()
RETURN u.id AS id, count(*) AS paths
ORDER BY paths DESC, id
LIMIT 1
