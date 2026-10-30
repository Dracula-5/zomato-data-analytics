// Two hops: restaurants with menus like the ones this customer used, that they haven't tried.
MATCH (u:User {id: $user})-[:ORDERED]->(:Order)-[:FROM]->(seen:Restaurant)
      -[s:SIMILAR_TO]-(rec:Restaurant)
WHERE NOT EXISTS { (u)-[:ORDERED]->(:Order)-[:FROM]->(rec) }
RETURN rec.id AS id, rec.name AS name, rec.city AS city,
       count(DISTINCT seen) AS via_restaurants, round(sum(s.jaccard), 3) AS score
ORDER BY score DESC, id
LIMIT $limit
