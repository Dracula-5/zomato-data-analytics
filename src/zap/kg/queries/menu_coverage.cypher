// How many restaurants have any menu data.
MATCH (r:Restaurant)
RETURN count(r) AS restaurants,
       sum(CASE WHEN EXISTS { (r)-[:SERVES]->() } THEN 1 ELSE 0 END) AS with_menu
