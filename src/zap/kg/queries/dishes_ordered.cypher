// How many dishes are eligible for the co-ordering test, and how many orders have any dish.
MATCH (f:FoodItem)
WITH f, COUNT { (:Order)-[:CONTAINS]->(f) } AS orders
WHERE orders >= $min_orders
WITH count(f) AS dishes
MATCH (o:Order) WHERE EXISTS { (o)-[:CONTAINS]->() }
RETURN dishes, count(o) AS orders
