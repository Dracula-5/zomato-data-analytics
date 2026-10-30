// The naive version: dish pairs bought together, tested across all orders. It is kept to show
// how much of this "signal" disappears once each pair is compared within its restaurant.
MATCH (a:FoodItem)<-[:CONTAINS]-(o:Order)-[:CONTAINS]->(b:FoodItem)
WHERE a.id < b.id
WITH a, b, count(o) AS both
WHERE both >= $min_both
WITH a, b, both,
     COUNT { (:Order)-[:CONTAINS]->(a) } AS orders_a,
     COUNT { (:Order)-[:CONTAINS]->(b) } AS orders_b
WHERE orders_a >= $min_orders AND orders_b >= $min_orders
RETURN a.name AS name_a, b.name AS name_b, both, orders_a, orders_b
ORDER BY both DESC, name_a, name_b
