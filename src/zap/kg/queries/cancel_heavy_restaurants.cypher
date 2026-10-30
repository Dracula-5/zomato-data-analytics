// Orders and cancellations per restaurant, for those with enough orders to test.
MATCH (o:Order)-[:FROM]->(r:Restaurant)
WITH r, count(o) AS orders, sum(CASE WHEN o.is_cancelled THEN 1 ELSE 0 END) AS cancelled
WHERE orders >= $min_orders
RETURN r.id AS id, r.name AS name, r.city AS city, orders, cancelled
ORDER BY id
