"""Entry point: the grouped navigation. `make app` runs this, and each page is its own script.

The pages live in views/ and not pages/, because Streamlit builds its own flat menu from a
folder called pages and shows it until this file has run.
"""

from pathlib import Path

import streamlit as st

from zap.dashboard.ui import theme_mode

ASSETS = Path(__file__).parent / "assets"

st.logo(str(ASSETS / f"logo_{theme_mode()}.svg"), size="large")
navigation = st.navigation(
    {
        "Business": [
            st.Page(
                "views/0_Overview.py",
                title="Overview",
                icon=":material/dashboard:",
                default=True,
            ),
            st.Page(
                "views/1_Cities.py",
                title="Cities",
                icon=":material/location_city:",
                url_path="Cities",
            ),
            st.Page(
                "views/2_Restaurants.py",
                title="Restaurants",
                icon=":material/restaurant:",
                url_path="Restaurants",
            ),
            st.Page(
                "views/3_Delivery_SLA.py",
                title="Delivery SLA",
                icon=":material/schedule:",
                url_path="Delivery_SLA",
            ),
        ],
        "Data and ML": [
            st.Page(
                "views/4_Data_Quality.py",
                title="Data Quality",
                icon=":material/verified:",
                url_path="Data_Quality",
            ),
            st.Page(
                "views/5_Forecasts.py",
                title="Forecasts",
                icon=":material/trending_up:",
                url_path="Forecasts",
            ),
            st.Page(
                "views/6_Knowledge_Graph.py",
                title="Knowledge Graph",
                icon=":material/hub:",
                url_path="Knowledge_Graph",
            ),
        ],
        "AI": [
            st.Page(
                "views/7_Copilot.py",
                title="Copilot",
                icon=":material/chat:",
                url_path="Copilot",
            ),
            st.Page(
                "views/8_AI_Evaluation.py",
                title="AI Evaluation",
                icon=":material/fact_check:",
                url_path="AI_Evaluation",
            ),
        ],
    }
)
navigation.run()
