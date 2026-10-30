// How big the graph is, by relationship type.
MATCH ()-[r]->()
RETURN type(r) AS relationship, count(*) AS relationships
ORDER BY relationships DESC
