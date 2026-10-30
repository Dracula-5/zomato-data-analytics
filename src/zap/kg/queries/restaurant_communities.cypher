// Louvain communities over shared menus, with what's needed to judge them.
MATCH (r:Restaurant)
WHERE r.community IS NOT NULL
RETURN r.community AS community, r.id AS id, r.name AS name,
       r.primary_cuisine AS cuisine, r.city AS city
ORDER BY community, id
