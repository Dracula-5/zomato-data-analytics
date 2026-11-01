"""Entry point: the grouped navigation. `make app` runs this, and each page is its own script.

The pages live in views/ and not pages/, because Streamlit builds its own flat menu from a
folder called pages and shows it until this file has run.
"""

from pathlib import Path

import streamlit as st

LOGO = Path(__file__).parent / "assets" / "logo.svg"


def page(file: str, title: str, icon: str, slug: str) -> st.Page:
    return st.Page(f"views/{file}", title=title, icon=f":material/{icon}:", url_path=slug)


st.logo(str(LOGO), size="large")
navigation = st.navigation(
    {
        "Business": [
            st.Page(
                "views/0_Overview.py",
                title="Overview",
                icon=":material/dashboard:",
                default=True,
            ),
            page("9_Live_Ops.py", "Live Ops", "sensors", "Live_Ops"),
            page("10_Insights.py", "Insights", "lightbulb", "Insights"),
            page("1_Cities.py", "Cities", "location_city", "Cities"),
            page("2_Restaurants.py", "Restaurants", "restaurant", "Restaurants"),
            page("3_Delivery_SLA.py", "Delivery SLA", "schedule", "Delivery_SLA"),
            page("11_Customer_Voice.py", "Customer Voice", "reviews", "Customer_Voice"),
        ],
        "Data and ML": [
            page("4_Data_Quality.py", "Data Quality", "verified", "Data_Quality"),
            page("5_Forecasts.py", "Forecasts", "trending_up", "Forecasts"),
            page("6_Knowledge_Graph.py", "Knowledge Graph", "hub", "Knowledge_Graph"),
        ],
        "AI": [
            page("7_Copilot.py", "Copilot", "chat", "Copilot"),
            page("8_AI_Evaluation.py", "AI Evaluation", "fact_check", "AI_Evaluation"),
        ],
    }
)
navigation.run()
