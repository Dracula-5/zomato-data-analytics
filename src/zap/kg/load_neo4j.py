"""Load one dataset's warehouse into Neo4j: constraints, then nodes, then relationships.

Everything is MERGE, so a re-run changes nothing. After loading, every count is compared
with the rows sent: a Cypher MATCH that finds nothing skips the row without any error, so a
bad key would otherwise lose relationships silently.

Only the columns selected here reach the graph: no names, emails or phone numbers of users.
"""

# ruff: noqa: S608 - every statement here is built from constants in this module

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, LiteralString

import duckdb
from neo4j import Driver, GraphDatabase, ManagedTransaction

from zap.config import Settings

BATCH_SIZE = 10_000
LABELS = ("City", "Locality", "Cuisine", "Restaurant", "FoodItem", "User", "Order")


def cypher(text: str) -> LiteralString:
    """The driver only accepts literal strings, to discourage injection. Ours are built from
    the fixed names in this module, and values always travel as parameters."""
    return text


@dataclass(frozen=True)
class Step:
    name: str
    sql: str
    write: str
    count: str


def _node(label: str, sql: str) -> Step:
    return Step(
        name=label,
        sql=sql,
        write=f"UNWIND $rows AS row MERGE (n:{label} {{id: row.id}}) SET n += row",
        count=f"MATCH (n:{label}) RETURN count(n)",
    )


def _edge(rel: str, start: str, end: str, sql: str, key: str | None = None) -> Step:
    """`sql` returns columns `a` and `b`, then any properties. `key` keeps parallel edges apart."""
    identity = f"{{{key}: row.props.{key}}}" if key else ""
    return Step(
        name=f"{start}-{rel}->{end}",
        sql=sql,
        write=(
            f"UNWIND $rows AS row MATCH (a:{start} {{id: row.a}}) MATCH (b:{end} {{id: row.b}}) "
            f"MERGE (a)-[r:{rel}{identity}]->(b) SET r += row.props"
        ),
        count=f"MATCH (:{start})-[r:{rel}]->(:{end}) RETURN count(r)",
    )


CUISINE_PAIRS = """select distinct restaurant_id as a,
                          trim(unnest(string_split(cuisines, ','))) as b
                   from marts.dim_restaurant where cuisines is not null"""
LOCALITY_ID = "city || ' / ' || locality"
USERS_WHO_ORDERED = "select distinct customer_id from marts.fct_orders"

STEPS = (
    _node("City", "select city as id from marts.dim_city"),
    _node(
        "Locality",
        f"""select distinct {LOCALITY_ID} as id, locality as name, city
            from marts.dim_restaurant where locality is not null""",
    ),
    _node(
        "Cuisine",
        f"select distinct b as id from ({CUISINE_PAIRS}) where b <> ''",
    ),
    _node(
        "Restaurant",
        """select restaurant_id as id, restaurant_name as name, city, primary_cuisine,
                  cast(rating as double) as rating, cost_for_two
           from marts.dim_restaurant""",
    ),
    _node("FoodItem", "select food_id as id, food_name as name, is_veg from marts.dim_food"),
    _node(
        "User",
        f"""select customer_id as id, age_band, occupation from marts.dim_customer
            where customer_id in ({USERS_WHO_ORDERED})""",
    ),
    _node(
        "Order",
        """select order_id as id, cast(order_date as varchar) as date, order_status as status,
                  is_cancelled, cast(sales_amount as double) as sales_amount
           from marts.fct_orders""",
    ),
    _edge(
        "LOCATED_IN",
        "Restaurant",
        "Locality",
        f"""select restaurant_id as a, {LOCALITY_ID} as b from marts.dim_restaurant
            where locality is not null""",
    ),
    _edge(
        "LOCATED_IN",
        "Locality",
        "City",
        f"""select distinct {LOCALITY_ID} as a, city as b from marts.dim_restaurant
            where locality is not null""",
    ),
    _edge(
        "HAS_CUISINE",
        "Restaurant",
        "Cuisine",
        f"select a, b from ({CUISINE_PAIRS}) where b <> ''",
    ),
    _edge(
        "SERVES",
        "Restaurant",
        "FoodItem",
        """select distinct m.restaurant_id as a, m.food_id as b from staging.stg_menu m
           join marts.dim_restaurant r on r.restaurant_id = m.restaurant_id
           join marts.dim_food f on f.food_id = m.food_id""",
    ),
    _edge(
        "ORDERED", "User", "Order", "select customer_id as a, order_id as b from marts.fct_orders"
    ),
    _edge(
        "FROM",
        "Order",
        "Restaurant",
        "select order_id as a, restaurant_id as b from marts.fct_orders",
    ),
    _edge(
        "CONTAINS",
        "Order",
        "FoodItem",
        """select i.order_id as a, i.food_id as b, cast(sum(i.quantity) as integer) as quantity
           from marts.fct_order_items i
           join marts.dim_food f on f.food_id = i.food_id
           group by i.order_id, i.food_id""",
    ),
    _edge(
        "REVIEWED",
        "User",
        "Restaurant",
        f"""select v.customer_id as a, v.restaurant_id as b, v.review_id, v.rating,
                   cast(v.review_date as varchar) as date
            from marts.fct_reviews v
            where v.customer_id in ({USERS_WHO_ORDERED})""",
        key="review_id",
    ),
)


@dataclass
class StepResult:
    step: str
    rows_sent: int
    in_graph: int
    seconds: float

    @property
    def ok(self) -> bool:
        return self.rows_sent == self.in_graph


@dataclass
class LoadReport:
    dataset: str
    steps: list[StepResult] = field(default_factory=list)
    seconds: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {**asdict(self), "ok": all(s.ok for s in self.steps)}


def connect(settings: Settings) -> Driver:
    if settings.neo4j_password is None:
        raise ValueError("NEO4J_PASSWORD is not set (see .env.example)")
    auth = (settings.neo4j_user, settings.neo4j_password.get_secret_value())
    return GraphDatabase.driver(settings.neo4j_uri, auth=auth)


def create_constraints(driver: Driver) -> None:
    with driver.session() as session:
        for label in LABELS:
            session.run(
                cypher(
                    f"CREATE CONSTRAINT {label.lower()}_id IF NOT EXISTS "
                    f"FOR (n:{label}) REQUIRE n.id IS UNIQUE"
                )
            ).consume()


def clear_graph(driver: Driver) -> None:
    """Relationships first: one Cuisine can have 30,000+, so deleting nodes along with their
    relationships can put far more than a batch into one transaction and run out of memory."""
    with driver.session() as session:
        for statement in (
            "MATCH ()-[r]->() CALL (r) { DELETE r } IN TRANSACTIONS OF 10000 ROWS",
            "MATCH (n) CALL (n) { DELETE n } IN TRANSACTIONS OF 10000 ROWS",
        ):
            session.run(cypher(statement)).consume()


def _rows_for(names: list[str], rows: list[tuple[Any, ...]]) -> list[dict[str, Any]]:
    if names[:2] != ["a", "b"]:
        return [dict(zip(names, row, strict=True)) for row in rows]
    # Relationship rows carry their properties separately, so MATCH can use a and b.
    return [
        {"a": row[0], "b": row[1], "props": dict(zip(names[2:], row[2:], strict=True))}
        for row in rows
    ]


def _write_batch(tx: ManagedTransaction, query: str, rows: list[dict[str, Any]]) -> None:
    tx.run(cypher(query), rows=rows).consume()


def run_step(
    driver: Driver, con: duckdb.DuckDBPyConnection, step: Step, batch_size: int = BATCH_SIZE
) -> StepResult:
    started = time.perf_counter()
    cursor = con.execute(step.sql)
    names = [column[0] for column in cursor.description or []]
    sent = 0
    with driver.session() as session:
        while rows := cursor.fetchmany(batch_size):
            session.execute_write(_write_batch, step.write, _rows_for(names, rows))
            sent += len(rows)
        in_graph = int(session.run(cypher(step.count)).single(strict=True)[0])
    return StepResult(step.name, sent, in_graph, round(time.perf_counter() - started, 3))


def load_graph(driver: Driver, db_path: Path, reset: bool = False) -> LoadReport:
    started = time.perf_counter()
    report = LoadReport(dataset=db_path.stem)
    con = duckdb.connect(str(db_path), read_only=True, config={"python_enable_replacements": False})
    try:
        if reset:
            clear_graph(driver)
        create_constraints(driver)
        report.steps = [run_step(driver, con, step) for step in STEPS]
    finally:
        con.close()
    report.seconds = round(time.perf_counter() - started, 3)
    return report
