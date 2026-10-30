// Where a restaurant has a menu at all, is each ordered dish on it?
MATCH (r:Restaurant)<-[:FROM]-(:Order)-[:CONTAINS]->(f:FoodItem)
WITH EXISTS { (r)-[:SERVES]->() } AS has_menu, EXISTS { (r)-[:SERVES]->(f) } AS on_menu
RETURN count(*) AS order_lines,
       sum(CASE WHEN has_menu THEN 1 ELSE 0 END) AS lines_with_a_menu,
       sum(CASE WHEN on_menu THEN 1 ELSE 0 END) AS lines_on_the_menu
