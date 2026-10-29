"""Deterministic SYNTHETIC fixture that mirrors the real dataset's files and columns.

Tests, CI and `make demo` run on this, so nothing in the repo needs the private 2+ GB
dataset. It copies the quirks of the real files so readers are tested against them:

* dimension CSVs start with an unnamed pandas index column and use CRLF line endings;
* fact CSVs have no index column and use LF;
* restaurant fields look like the scraped source: rating "--", cost "₹ 200",
  city "Area,City", and one quoted address containing a newline.

A known set of defects is injected on purpose (see `Defects`), so the data-quality
layer has something to catch and tests can assert it catches exactly that many.
"""

from __future__ import annotations

import csv
import random
from collections.abc import Iterable, Sequence
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from pathlib import Path

# Real-file headers. Dimension headers start with "" (the pandas index column).
HEADERS: dict[str, list[str]] = {
    "restaurant": [
        "",
        "id",
        "name",
        "city",
        "rating",
        "rating_count",
        "cost",
        "cuisine",
        "lic_no",
        "link",
        "address",
        "menu",
    ],
    "food": ["", "f_id", "item", "veg_or_non_veg"],
    "menu": ["", "menu_id", "r_id", "f_id", "cuisine", "price"],
    "users": [
        "",
        "user_id",
        "name",
        "email",
        "password",
        "Age",
        "Gender",
        "Marital Status",
        "Occupation",
        "Monthly Income",
        "Educational Qualifications",
        "Family size",
    ],
    "orders": [
        "order_id",
        "order_timestamp",
        "order_date",
        "user_id",
        "r_id",
        "restaurant_city",
        "cuisine",
        "items_count",
        "sales_qty",
        "subtotal",
        "discount",
        "delivery_fee",
        "gst",
        "sales_amount",
        "currency",
        "payment_method",
        "order_status",
        "customer_rating",
        "delivery_time_min",
    ],
    "order_items": [
        "order_item_id",
        "order_id",
        "r_id",
        "f_id",
        "price",
        "quantity",
        "line_amount",
    ],
    "reviews": [
        "review_id",
        "order_id",
        "user_id",
        "restaurant_id",
        "rating",
        "comment",
        "review_date",
    ],
}
DIMENSIONS = ("restaurant", "food", "menu", "users")

LOCATIONS: Sequence[tuple[str | None, str]] = (
    ("Koramangala", "Bangalore"),
    ("Indiranagar", "Bangalore"),
    ("HSR", "Bangalore"),
    ("Bandra West", "Mumbai"),
    ("Vashi", "Mumbai"),
    ("Rohini", "Delhi"),
    ("PCMC", "Pune"),
    ("Ameerpet", "Hyderabad"),
    (None, "Bikaner"),
    (None, "Kanpur"),
)
CUISINES = (
    "North Indian",
    "Chinese",
    "Biryani",
    "Pizzas",
    "Desserts",
    "Beverages",
    "South Indian",
    "Sweets",
    "Bakery",
    "American",
)
DISHES = (
    "Paneer Tikka",
    "Veg Biryani",
    "Chicken Biryani",
    "Masala Dosa",
    "Hakka Noodles",
    "Margherita Pizza",
    "Gulab Jamun",
    "Cold Coffee",
    "Butter Chicken",
    "Veg Burger",
    "Chicken Momos",
    "Chocolate Brownie",
    "Idli Sambar",
    "Fish Curry",
    "Aloo Paratha",
)
NON_VEG_WORDS = ("Chicken", "Fish", "Mutton", "Egg")
# Category names with relative weights, roughly the real data's mix.
PAYMENTS: dict[str, float] = {"UPI": 46, "Card": 20, "Wallet": 15, "COD": 14, "NetBanking": 5}
STATUSES: dict[str, float] = {"Delivered": 915, "Cancelled": 60, "Refunded": 25}
# Relative order volume by hour, shaped like the real data (lunch and dinner peaks).
HOUR_WEIGHTS = (
    4,
    2,
    1,
    1,
    1,
    2,
    6,
    16,
    24,
    28,
    32,
    45,
    91,
    97,
    65,
    37,
    33,
    45,
    77,
    112,
    118,
    91,
    51,
    20,
)
# Monday=0 … Sunday=6; weekends are busier, as in the real data.
WEEKDAY_WEIGHTS = (1.0, 1.0, 1.0, 1.05, 1.35, 1.6, 1.45)
COMMENTS: dict[int, tuple[str, ...]] = {
    1: ("Food arrived cold and soggy.", "Order was missing items. Very disappointed."),
    2: ("Far too expensive for what you get.", "Delivery took forever."),
    3: ("An average experience overall.", "It was okay, nothing special."),
    4: ("Great value for the price.", "Tasty food, slightly late delivery."),
    5: ("Absolutely delicious! Delivered right on time.", "Great eco-friendly packaging."),
}


@dataclass(frozen=True)
class FixtureSpec:
    """Size and seed of the fixture. Same spec gives byte-identical files."""

    n_restaurants: int = 60
    n_users: int = 300
    n_foods: int = 120
    n_orders: int = 5_000
    n_days: int = 60
    end_date: date = date(2026, 6, 30)
    review_rate: float = 0.03
    seed: int = 42
    defects: bool = True


@dataclass
class Defects:
    """Counts of deliberately injected defects, keyed by the check that should catch them."""

    duplicate_order_id: int = 0
    amount_mismatch: int = 0
    orphan_order_restaurant: int = 0
    review_before_order: int = 0
    review_restaurant_mismatch: int = 0
    review_on_undelivered: int = 0
    null_veg_flag: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


@dataclass
class FixtureResult:
    rows: dict[str, int] = field(default_factory=dict)
    defects: Defects = field(default_factory=Defects)


def _write_csv(path: Path, header: list[str], rows: Iterable[Sequence[object]], crlf: bool) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    n = 0
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, lineterminator="\r\n" if crlf else "\n")
        writer.writerow(header)
        for row in rows:
            writer.writerow(row)
            n += 1
    return n


def _gst(subtotal: int) -> int:
    """5% GST rounded half-up, which matches every row of the real orders file."""
    return int(subtotal * 0.05 + 0.5)


def generate_fixture(out_dir: Path, spec: FixtureSpec | None = None) -> FixtureResult:
    """Write the seven CSVs to `out_dir` and return row counts and injected-defect counts."""
    spec = spec or FixtureSpec()
    rng = random.Random(spec.seed)
    result = FixtureResult()
    defects = result.defects

    # --- restaurants --------------------------------------------------------------------
    rest_ids = rng.sample(range(100_000, 999_999), spec.n_restaurants)
    restaurants: list[list[object]] = []
    rest_info: dict[int, tuple[str, str]] = {}  # id -> (city text, cuisine text)
    for i, rid in enumerate(rest_ids):
        area, city = rng.choice(LOCATIONS)
        city_text = f"{area},{city}" if area else city
        cuisine = ",".join(rng.sample(CUISINES, rng.choice((1, 2))))
        rated = rng.random() > 0.4
        rating = f"{rng.uniform(3.0, 4.8):.1f}" if rated else "--"
        rating_count = (
            rng.choice(("20+ ratings", "50+ ratings", "100+ ratings", "1K+ ratings"))
            if rated
            else "Too Few Ratings"
        )
        name = f"Fixture Kitchen {i}"
        address = f"{name}, Main Road, {city}"
        if i == 0:
            address = f"{name}, Shop 4,\nMain Road, {city}"  # embedded newline, as in real data
        restaurants.append(
            [
                i,
                rid,
                name,
                city_text,
                rating,
                rating_count,
                f"₹ {rng.randrange(100, 650, 50)}",
                cuisine,
                str(rng.randrange(10**13, 10**14)),
                f"https://example.com/restaurants/fixture-kitchen-{i}-{rid}",
                address,
                f"Menu/{rid}.json",
            ]
        )
        rest_info[rid] = (city_text, cuisine)
    result.rows["restaurant"] = _write_csv(
        out_dir / "restaurant.csv", HEADERS["restaurant"], restaurants, crlf=True
    )

    # --- food ---------------------------------------------------------------------------
    foods: list[list[object]] = []
    for i in range(spec.n_foods):
        dish = rng.choice(DISHES)
        veg = "Non-veg" if any(w in dish for w in NON_VEG_WORDS) else "Veg"
        foods.append([i, f"fd{i}", f"{dish} {i}", veg])
    if spec.defects:
        foods[-1][3] = ""
        defects.null_veg_flag = 1
    result.rows["food"] = _write_csv(out_dir / "food.csv", HEADERS["food"], foods, crlf=True)
    food_ids = [str(f[1]) for f in foods]

    # --- menu ---------------------------------------------------------------------------
    menus: list[list[object]] = []
    menu_by_rest: dict[int, list[tuple[str, int]]] = {}
    for rid in rest_ids:
        first_cuisine = rest_info[rid][1].split(",")[0]
        for fid in rng.sample(food_ids, rng.randint(3, 8)):
            price = rng.randrange(60, 450, 10)
            menus.append([len(menus), f"mn{len(menus)}", rid, fid, first_cuisine, f"{price}.0"])
            menu_by_rest.setdefault(rid, []).append((fid, price))
    result.rows["menu"] = _write_csv(out_dir / "menu.csv", HEADERS["menu"], menus, crlf=True)

    # --- users --------------------------------------------------------------------------
    users: list[list[object]] = []
    for uid in range(1, spec.n_users + 1):
        users.append(
            [
                uid - 1,
                uid,
                f"Fixture User {uid}",
                f"fixture.user{uid}@example.com",
                f"fx{rng.getrandbits(32):08x}",
                rng.randint(18, 60),
                rng.choice(("Male", "Female")),
                rng.choice(("Single", "Married", "Prefer not to say")),
                rng.choice(("Student", "Employee", "Self Employeed", "House wife")),
                rng.choice(
                    (
                        "No Income",
                        "Below Rs.10000",
                        "10001 to 25000",
                        "25001 to 50000",
                        "More than 50000",
                    )
                ),
                rng.choice(("Graduate", "Post Graduate", "Ph.D", "School", "Uneducated")),
                rng.randint(1, 6),
            ]
        )
    result.rows["users"] = _write_csv(out_dir / "users.csv", HEADERS["users"], users, crlf=True)

    # --- orders + order_items -------------------------------------------------------------
    start = spec.end_date - timedelta(days=spec.n_days - 1)
    days = [start + timedelta(days=d) for d in range(spec.n_days)]
    # Linear growth across the window times the weekday pattern.
    day_weights = [
        (1 + 0.4 * d / max(spec.n_days - 1, 1)) * WEEKDAY_WEIGHTS[day.weekday()]
        for d, day in enumerate(days)
    ]
    order_days = sorted(rng.choices(days, weights=day_weights, k=spec.n_orders))
    pay_names, pay_w = list(PAYMENTS), list(PAYMENTS.values())
    status_names, status_w = list(STATUSES), list(STATUSES.values())

    orders: list[list[object]] = []
    items: list[list[object]] = []
    order_meta: dict[int, tuple[date, int, int, str]] = {}  # id -> (date, user, rest, status)
    for oid, day in enumerate(order_days, start=1):
        hour = rng.choices(range(24), weights=HOUR_WEIGHTS)[0]
        ts = datetime.combine(day, time(hour, rng.randrange(60), rng.randrange(60)))
        uid = rng.randint(1, spec.n_users)
        rid = rng.choice(rest_ids)
        lines = rng.sample(menu_by_rest[rid], rng.randint(1, min(4, len(menu_by_rest[rid]))))
        subtotal = qty_total = 0
        for fid, price in lines:
            qty = rng.choices((1, 2, 3), weights=(80, 15, 5))[0]
            items.append([len(items) + 1, oid, rid, fid, price, qty, price * qty])
            subtotal += price * qty
            qty_total += qty
        discount = 0 if rng.random() < 0.5 else int(subtotal * rng.uniform(0.1, 0.35))
        fee = rng.choice((0, 0, 15, 20, 25, 30))
        gst = _gst(subtotal)
        status = rng.choices(status_names, weights=status_w)[0]
        delivered = status == "Delivered"
        orders.append(
            [
                oid,
                ts.strftime("%Y-%m-%d %H:%M:%S"),
                day.isoformat(),
                uid,
                rid,
                rest_info[rid][0],
                rest_info[rid][1],
                len(lines),
                qty_total,
                subtotal,
                discount,
                fee,
                gst,
                subtotal - discount + fee + gst,
                "INR",
                rng.choices(pay_names, weights=pay_w)[0],
                status,
                f"{rng.randint(1, 5)}.0" if delivered else "",
                f"{max(8, round(rng.gauss(31, 10)))}.0" if delivered else "",
            ]
        )
        order_meta[oid] = (day, uid, rid, status)

    tainted: set[int] = set()  # orders with injected defects; reviews must not touch them
    if spec.defects:
        orders[10][13] = int(str(orders[10][13])) + 10  # total no longer adds up
        defects.amount_mismatch = 1
        orphan_rid = 99  # outside the 6-digit id range, so never a real restaurant
        orders[20][4] = orphan_rid
        defects.orphan_order_restaurant = 1
        orders.append(list(orders[30]))  # exact duplicate row
        defects.duplicate_order_id = 1
        tainted = {int(str(orders[i][0])) for i in (10, 20, 30)}
    result.rows["orders"] = _write_csv(
        out_dir / "orders.csv", HEADERS["orders"], orders, crlf=False
    )
    result.rows["order_items"] = _write_csv(
        out_dir / "order_items.csv", HEADERS["order_items"], items, crlf=False
    )

    # --- reviews --------------------------------------------------------------------------
    clean = {o: m for o, m in order_meta.items() if o not in tainted}
    delivered_ids = [o for o, m in clean.items() if m[3] == "Delivered"]
    undelivered_ids = [o for o, m in clean.items() if m[3] != "Delivered"]
    n_reviews = max(10, int(spec.n_orders * spec.review_rate))
    reviewed = sorted(rng.sample(delivered_ids, min(n_reviews, len(delivered_ids))))
    reviews: list[list[object]] = []
    for oid in reviewed:
        day, uid, rid, _ = order_meta[oid]
        stars = rng.choices((1, 2, 3, 4, 5), weights=(10, 10, 15, 25, 40))[0]
        reviews.append(
            [
                len(reviews) + 1,
                oid,
                uid,
                rid,
                stars,
                rng.choice(COMMENTS[stars]),
                (day + timedelta(days=rng.randint(0, 7))).isoformat(),
            ]
        )

    if spec.defects:
        for row in reviews[:3]:  # reviewed before ordering
            row[6] = (order_meta[int(str(row[1]))][0] - timedelta(days=2)).isoformat()
        defects.review_before_order = 3
        for row in reviews[3:5]:  # points at the wrong restaurant
            row[3] = next(r for r in rest_ids if r != row[3])
        defects.review_restaurant_mismatch = 2
        for oid in undelivered_ids[:2]:  # review on a cancelled order
            day, uid, rid, _ = order_meta[oid]
            reviews.append([len(reviews) + 1, oid, uid, rid, 1, COMMENTS[1][0], day.isoformat()])
        defects.review_on_undelivered = min(2, len(undelivered_ids))
    result.rows["reviews"] = _write_csv(
        out_dir / "reviews.csv", HEADERS["reviews"], reviews, crlf=False
    )

    return result
