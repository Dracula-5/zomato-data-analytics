// Dish pairs bought together, compared within one restaurant. This is the honest version:
// each restaurant sells its own dishes, so across all orders any two of them look linked.
MATCH (r:Restaurant)<-[:FROM]-(o:Order)-[:CONTAINS]->(a:FoodItem)
MATCH (o)-[:CONTAINS]->(b:FoodItem)
WHERE a.id < b.id
WITH r, a, b, count(o) AS both
WHERE both >= $min_both
RETURN r.id AS restaurant_id, r.name AS restaurant, a.name AS name_a, b.name AS name_b, both,
       COUNT { (r)<-[:FROM]-(:Order)-[:CONTAINS]->(a) } AS orders_a,
       COUNT { (r)<-[:FROM]-(:Order)-[:CONTAINS]->(b) } AS orders_b,
       COUNT { (r)<-[:FROM]-(:Order) } AS orders_here
ORDER BY both DESC, restaurant_id, name_a, name_b
