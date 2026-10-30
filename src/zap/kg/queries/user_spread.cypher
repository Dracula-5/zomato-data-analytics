// How far each customer's orders range. Real customers stay in one or two cities.
MATCH (u:User)-[:ORDERED]->(:Order)-[:FROM]->(r:Restaurant)
WITH u, count(DISTINCT r.city) AS cities, count(*) AS orders, count(DISTINCT r) AS restaurants
RETURN count(u) AS users,
       percentileCont(cities, 0.5) AS median_cities,
       max(cities) AS max_cities,
       sum(orders) AS orders,
       sum(restaurants) AS user_restaurant_pairs
