// How many dish pairs could occur within a restaurant: the true number of hypotheses.
MATCH (r:Restaurant)<-[:FROM]-(:Order)-[:CONTAINS]->(f:FoodItem)
WITH r, count(DISTINCT f) AS dishes
RETURN sum(dishes * (dishes - 1) / 2) AS pairs
