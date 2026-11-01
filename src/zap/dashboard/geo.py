"""Where the largest cities are, only to place bubbles on a map. The data itself has no coordinates.

City centres, rounded to two decimals. A city that isn't listed is simply left off the map.
"""

from __future__ import annotations

CITY_CENTRES: dict[str, tuple[float, float]] = {
    "Ahmedabad": (23.02, 72.57),
    "Bangalore": (12.97, 77.59),
    "Bikaner": (28.02, 73.31),
    "Chandigarh": (30.73, 76.78),
    "Chennai": (13.08, 80.27),
    "Coimbatore": (11.02, 76.96),
    "Dehradun": (30.32, 78.03),
    "Delhi": (28.61, 77.21),
    "Gurgaon": (28.46, 77.03),
    "Guwahati": (26.14, 91.74),
    "Hyderabad": (17.39, 78.49),
    "Indore": (22.72, 75.86),
    "Kanpur": (26.45, 80.33),
    "Kochi": (9.93, 76.27),
    "Kolkata": (22.57, 88.36),
    "Lucknow": (26.85, 80.95),
    "Mumbai": (19.08, 72.88),
    "Mysore": (12.30, 76.64),
    "Nagpur": (21.15, 79.09),
    "Noida-1": (28.54, 77.39),
    "Patna": (25.59, 85.14),
    "Pune": (18.52, 73.86),
    "Surat": (21.17, 72.83),
    "Vijayawada": (16.51, 80.65),
    "Vizag": (17.69, 83.22),
}
