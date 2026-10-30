// The cancel rate across all orders, which every outlier test is measured against.
MATCH (o:Order)
RETURN avg(CASE WHEN o.is_cancelled THEN 1.0 ELSE 0.0 END) AS rate, count(o) AS orders
