// Every restaurant with a menu, to measure how much of each chain the communities found.
MATCH (r:Restaurant)
WHERE EXISTS { (r)-[:SERVES]->() }
RETURN r.id AS id, r.name AS name
ORDER BY id
