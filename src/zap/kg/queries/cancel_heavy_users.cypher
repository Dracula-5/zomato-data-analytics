// Orders and cancellations per customer, for those with enough orders to test.
MATCH (u:User)-[:ORDERED]->(o:Order)
WITH u, count(o) AS orders, sum(CASE WHEN o.is_cancelled THEN 1 ELSE 0 END) AS cancelled
WHERE orders >= $min_orders
RETURN u.id AS id, orders, cancelled
ORDER BY id
