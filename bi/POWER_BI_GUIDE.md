# Power BI report: build guide

This guide builds a two-page Power BI report on the dbt marts, for readers who don't write SQL:

1. **Business Overview**: GMV, orders, average order value and cancellation rate, with a GMV
   trend, the top cities, and city and date slicers.
2. **Forecast vs Actual**: the ETS forecast against what happened, its 80% band, its error,
   and a plain-English note.

Power BI Desktop is a free Windows app, so the `.pbix` file is built by hand. Everything it needs
is in this folder:

| File | What it is |
|---|---|
| `data/*.csv` | The tables, written by `make bi-export` (not committed, rebuilt from the warehouse) |
| `data/export_manifest.json` | Row counts, totals and forecast scores, for checking the report |
| `data/forecast_note.txt` | The text for page 2's text box, generated from the numbers |
| [queries.pq](queries.pq) | Power Query code that loads each CSV with exact data types |
| [measures.dax](measures.dax) | Every DAX measure, ready for DAX query view |
| [theme.json](theme.json) | Colors checked for colorblind readers, plus fonts and gridlines |
| `screenshots/` | Where the report screenshots go (step 11) |

The orders and reviews are synthetic, so the report describes this dataset, not a real business.
Say so on the report (step 7 adds a footnote).

## 1. Export the data

From the repository root:

```bash
make bi-export                    # full warehouse if built, otherwise the SYNTHETIC fixture
make bi-export BI_DATASET=fixture # force the fixture (after `make demo`)
```

Without `make`, run `uv run zap bi-export` (add `--dataset fixture` to choose). It writes five
files to `bi/data/`:

| File | Grain | Role in the model |
|---|---|---|
| `mart_city_daily.csv` | one row per day and city | fact: drives page 1 |
| `mart_daily_kpis.csv` | one row per day, national | fact: national checks, including the SLA breach rate |
| `dim_city.csv` | one row per city | dimension |
| `dim_date.csv` | one row per day, whole months only | dimension: the date table |
| `forecast_vs_actual.csv` | one row per day and scope | fact: drives page 2 |

`forecast_vs_actual` has a `period` column: `backtest` rows are past days the model forecast
without seeing them (walk-forward, 14 days at a time), and `future` rows are the 14 days after the
data ends. `scope` is `National` or one of the ten busiest cities. The lower and upper columns are
an 80% band built only from earlier folds' errors, so the first 14 backtest days have no band.

Install **Power BI Desktop** from the Microsoft Store if you don't have it, open it, and choose
**Blank report**.

## 2. Load the five tables

These steps paste the Power Query code from [queries.pq](queries.pq), so every column gets
exactly the right type, whatever your Windows region settings are.

1. **Home → Transform data**. The Power Query Editor opens.
2. **Home → Manage Parameters → New Parameter**. Set:
   - Name: `BiDataFolder`
   - Type: **Text**
   - Suggested Values: **Any value**
   - Current Value: the full path of the `bi\data` folder, for example
     `C:\Users\dheer\OneDrive\Desktop\zomato-data_analytics\bi\data\`

   Click **OK**.
3. **Home → New Source → Blank Query**.
4. **Home → Advanced Editor**. Select all the text in it and delete it.
5. Open [queries.pq](queries.pq) in a text editor, copy the block under
   `==== Query name: mart_city_daily ====` (from `let` to the last `Typed`), paste it into the
   Advanced Editor, and click **Done**.
6. In the **Queries** list on the left, right-click the new query (`Query1`) → **Rename** →
   `mart_city_daily`.
7. Repeat steps 3 to 6 for `mart_daily_kpis`, `dim_city`, `dim_date` and `forecast_vs_actual`.
   The names must match exactly, because the DAX uses them.
8. Click each query and check the column headers show the types in the table below
   (calendar icon for dates, `123` for whole numbers, `1.2` for decimals, `%` for
   percentages, `ABC` for text, ✓✗ for true/false).
9. **Home → Close & Apply**.

| Table | Types |
|---|---|
| `mart_city_daily` | order_date: Date · city: Text · orders, delivered_orders, cancelled_orders, gmv, active_customers: Whole number · aov: Decimal · cancel_rate, discount_rate: Percentage |
| `mart_daily_kpis` | order_date: Date · is_weekend: True/False · iso_day_of_week, orders, delivered_orders, gmv, active_customers: Whole number · aov: Decimal · cancel_rate, sla_breach_rate: Percentage |
| `dim_city` | city: Text · localities, restaurants: Whole number · avg_restaurant_rating, avg_cost_for_two: Decimal |
| `dim_date` | date, month_start, week_start: Date · month_name, month_label, year_month, day_name: Text · year, month, day_of_month, iso_day_of_week: Whole number · is_weekend: True/False |
| `forecast_vs_actual` | date: Date · scope, period: Text · fold: Whole number · all other columns: Decimal |

Prefer clicking? **Home → Get data → Text/CSV**, pick a file, click **Transform Data**, then set
each column's type from the table above (click the icon at the left of the column header) with
**Using Locale… → English (United States)**. Rename nothing: the file name becomes the query name.

## 3. Build the star schema

1. **File → Options and settings → Options → Current File → Data Load**. Under
   *Time intelligence*, clear **Auto date/time**. Under *Relationships*, clear
   **Autodetect new relationships after data is loaded**. Click **OK**. (Auto date/time adds a
   hidden calendar to every date column; the model has its own date table instead.)
2. Click **Model view** (third icon on the left bar).
3. **Home → Manage relationships**. Delete any relationship already listed.
4. Click **New relationship** and create these four, one at a time. For each: pick the first
   table and column, then the second table and column, set **Cardinality: Many to one (\*:1)**,
   **Cross-filter direction: Single**, tick **Make this relationship active**, and save.

   | From (many side) | To (one side) |
   |---|---|
   | `mart_city_daily[order_date]` | `dim_date[date]` |
   | `mart_city_daily[city]` | `dim_city[city]` |
   | `mart_daily_kpis[order_date]` | `dim_date[date]` |
   | `forecast_vs_actual[date]` | `dim_date[date]` |

5. Close the dialog. Drag the tables into a star: `dim_date` in the middle, `dim_city` to its
   left, the three fact tables around them.

`forecast_vs_actual[scope]` has no relationship on purpose: it holds `National` as well as city
names, so page 2 gets its own scope slicer.

## 4. Mark the date table and sort the labels

1. Click **Table view** (second icon on the left bar). In the **Data** pane on the right, click
   `dim_date`.
2. **Table tools → Mark as date table**. Turn on **Mark as a date table**, choose `date` as the
   date column, and click **Save**. Power BI checks that the dates are unique and have no gaps;
   `make bi-export` already checked both.
3. Still in `dim_date`, set three sort orders so months and days don't sort alphabetically. Click
   the column, then **Column tools → Sort by column**:
   - `month_label` → sort by `month_start`
   - `month_name` → sort by `month`
   - `day_name` → sort by `iso_day_of_week`

## 5. Add the measures

**Fast way (DAX query view):**

1. Click **DAX query view** (fourth icon on the left bar).
2. Open [measures.dax](measures.dax), copy all of it, and paste it into the query editor.
3. Click **Run**. The result grid shows one row of numbers; keep it for step 9.
4. Click **Update model with changes** in the ribbon (or click each
   **Update model: Add new measure** link above a `MEASURE` line). The measures now appear in the
   Data pane under `mart_city_daily` and `forecast_vs_actual`.

**By hand:** in Report view, right-click the home table in the Data pane → **New measure**, paste
one measure from the block below, press Enter, and repeat.

```dax
Total GMV =
    SUM ( mart_city_daily[gmv] )

Orders =
    SUM ( mart_city_daily[orders] )

Delivered Orders =
    SUM ( mart_city_daily[delivered_orders] )

Cancelled Orders =
    SUM ( mart_city_daily[cancelled_orders] )

AOV =
    DIVIDE ( [Total GMV], [Delivered Orders] )

Cancel Rate =
    DIVIDE ( [Cancelled Orders], [Orders] )

GMV MoM % =
    VAR LatestMonth =
        EOMONTH ( MAX ( mart_city_daily[order_date] ), -1 ) + 1
    VAR MonthDates =
        CALCULATETABLE (
            VALUES ( dim_date[date] ),
            REMOVEFILTERS ( dim_date ),
            dim_date[month_start] = LatestMonth
        )
    VAR ThisMonth =
        CALCULATE ( [Total GMV], MonthDates )
    VAR PreviousMonth =
        CALCULATE ( [Total GMV], DATEADD ( MonthDates, -1, MONTH ) )
    RETURN
        IF (
            NOT ISBLANK ( ThisMonth ) && NOT ISBLANK ( PreviousMonth ),
            DIVIDE ( ThisMonth - PreviousMonth, PreviousMonth )
        )

Actual GMV =
    SUM ( forecast_vs_actual[actual_gmv] )

Forecast GMV =
    SUM ( forecast_vs_actual[forecast_gmv] )

Forecast GMV Lower =
    SUM ( forecast_vs_actual[forecast_gmv_lower] )

Forecast GMV Upper =
    SUM ( forecast_vs_actual[forecast_gmv_upper] )

GMV vs Forecast % =
    VAR Actual =
        CALCULATE ( [Actual GMV], forecast_vs_actual[period] = "backtest" )
    VAR Forecast =
        CALCULATE ( [Forecast GMV], forecast_vs_actual[period] = "backtest" )
    RETURN
        DIVIDE ( Actual - Forecast, Forecast )

MAPE GMV =
    AVERAGEX (
        FILTER (
            forecast_vs_actual,
            forecast_vs_actual[period] = "backtest"
                && forecast_vs_actual[actual_gmv] > 0
        ),
        ABS ( forecast_vs_actual[forecast_gmv] - forecast_vs_actual[actual_gmv] )
            / forecast_vs_actual[actual_gmv]
    )

MAPE Orders =
    AVERAGEX (
        FILTER (
            forecast_vs_actual,
            forecast_vs_actual[period] = "backtest"
                && forecast_vs_actual[actual_orders] > 0
        ),
        ABS ( forecast_vs_actual[forecast_orders] - forecast_vs_actual[actual_orders] )
            / forecast_vs_actual[actual_orders]
    )

Days Inside Band % =
    VAR Banded =
        FILTER (
            forecast_vs_actual,
            forecast_vs_actual[period] = "backtest"
                && NOT ISBLANK ( forecast_vs_actual[forecast_gmv_lower] )
        )
    VAR Inside =
        FILTER (
            Banded,
            forecast_vs_actual[actual_gmv] >= forecast_vs_actual[forecast_gmv_lower]
                && forecast_vs_actual[actual_gmv] <= forecast_vs_actual[forecast_gmv_upper]
        )
    RETURN
        DIVIDE ( COUNTROWS ( Inside ), COUNTROWS ( Banded ) )
```

Then format each one: click it in the Data pane, and on the **Measure tools** tab set the
format. For ₹, choose **Currency**, then pick **₹ English (India)** from the currency symbol list.

| Measure | Home table | Format | What it means |
|---|---|---|---|
| `Total GMV` | mart_city_daily | Currency ₹, 0 decimals | Value of delivered orders (cancelled and refunded excluded) |
| `Orders` | mart_city_daily | Whole number, thousands separator | Orders placed |
| `Delivered Orders` | mart_city_daily | Whole number, thousands separator | Orders delivered |
| `Cancelled Orders` | mart_city_daily | Whole number, thousands separator | Helper for `Cancel Rate` |
| `AOV` | mart_city_daily | Currency ₹, 0 decimals | GMV ÷ delivered orders, as in the dbt mart |
| `Cancel Rate` | mart_city_daily | Percentage, 1 decimal | Cancelled ÷ all orders |
| `GMV MoM %` | mart_city_daily | Percentage, 1 decimal | Latest month in view against the month before |
| `Actual GMV` | forecast_vs_actual | Currency ₹, 0 decimals | Page 2's actual line |
| `Forecast GMV` | forecast_vs_actual | Currency ₹, 0 decimals | Page 2's forecast line |
| `Forecast GMV Lower` | forecast_vs_actual | Currency ₹, 0 decimals | Bottom of the 80% band |
| `Forecast GMV Upper` | forecast_vs_actual | Currency ₹, 0 decimals | Top of the 80% band |
| `GMV vs Forecast %` | forecast_vs_actual | Percentage, 2 decimals | Backtest only: actual above (+) or below (−) forecast |
| `MAPE GMV` | forecast_vs_actual | Percentage, 2 decimals | Average daily miss on GMV, backtest only |
| `MAPE Orders` | forecast_vs_actual | Percentage, 2 decimals | Average daily miss on orders; the number in METRICS.md |
| `Days Inside Band %` | forecast_vs_actual | Percentage, 1 decimal | Backtest days whose actual landed inside the band |

Don't drag the `cancel_rate`, `discount_rate` or `aov` columns onto a visual: Power BI would add
up or average the stored ratios, which gives the wrong answer for more than one row. Use the
measures. Likewise `active_customers` can't be added across days or cities, because the same
customer can appear in both.

## 6. Apply the theme

**View → Themes** (click the drop-down arrow on the theme gallery) **→ Browse for themes**, pick
`bi\theme.json`, and click **Open**. The first two colors are blue and orange, which stay distinct
for the common kinds of color blindness; page 2 uses exactly those two.

If Power BI says the theme file is invalid, delete the `"visualStyles"` block from a copy of the
file and import that: the colors and fonts still apply.

## 7. Page 1: Business Overview

Rename the page: double-click **Page 1** at the bottom → `Business Overview`. In
**Format → Canvas settings**, keep **16:9** (1280 × 720). Sizes below are in pixels (set them in
**Format → General → Properties**); exact values matter less than the alignment.

| Visual | Fields | Position (x, y, width, height) |
|---|---|---|
| Text box (title) | "Business overview" in 20 pt bold; on a second line, 11 pt: "Delivered sales, orders and cancellations by city. Orders are synthetic." | 24, 16, 700, 56 |
| Slicer (city) | `dim_city[city]` | 760, 16, 240, 56 |
| Slicer (date) | `dim_date[date]` | 1016, 16, 240, 56 |
| Card | `Total GMV`, renamed "Delivered sales (GMV)" | 24, 88, 296, 104 |
| Card | `Orders`, renamed "Orders placed" | 336, 88, 296, 104 |
| Card | `AOV`, renamed "Average order value" | 648, 88, 296, 104 |
| Card | `Cancel Rate`, renamed "Orders cancelled" | 960, 88, 296, 104 |
| Line chart | X-axis `dim_date[month_start]`, Y-axis `Total GMV`, Tooltips `GMV MoM %` | 24, 208, 760, 456 |
| Clustered bar chart | Y-axis `dim_city[city]`, X-axis `Total GMV` | 800, 208, 456, 456 |
| Text box (footnote) | "GMV counts delivered orders only. Source: dbt marts, exported by `make bi-export`." in 9 pt | 24, 676, 1232, 32 |

Steps:

1. **Title.** **Insert → Text box**, type the two lines, and set the sizes.
2. **City slicer.** Click an empty part of the canvas, click **Slicer** in the Visualizations
   pane, and tick `city` under `dim_city`. **Format visual → Visual → Slicer settings → Options →
   Style: Dropdown**. Under **Selection**, turn on **Show "Select all" option**. Set the title to
   "City".
3. **Date slicer.** Another Slicer with `dim_date[date]`. **Style: Between**. Title "Dates".
4. **Cards.** Click empty canvas, click **Card**, and tick `Total GMV`. Double-click the field in
   the **Fields** well → **Rename for this visual** → "Delivered sales (GMV)". Repeat for the other
   three. (If your version's Card accepts several fields, you can put all four in one Card.)
   Set display units to **Auto** so ₹441.8 crore reads as "₹4.42bn" rather than ten digits.
5. **GMV trend.** Click **Line chart**. Drag `month_start` (from `dim_date`) to **X-axis**. If it
   shows "Date Hierarchy", click the arrow beside it and choose `month_start`. Drag `Total GMV` to
   **Y-axis** and `GMV MoM %` to **Tooltips**. Title: "Delivered sales by month". In
   **Format visual → Visual → Lines**, set the stroke width to 2 px; turn on **Markers** at size 4.
   Hovering a month now shows its GMV and its change from the month before.
6. **Top cities.** Click **Clustered bar chart**. Drag `dim_city[city]` to **Y-axis** and
   `Total GMV` to **X-axis**. In the **Filters** pane, under *Filters on this visual*, expand
   `city`: **Filter type: Top N**, **Show items: Top 10**, drag `Total GMV` into **By value**, and
   click **Apply filter**. In the chart's **⋯ → Sort axis**, choose `Total GMV` and
   **Sort descending**. Turn on **Data labels** (one color only, so the labels carry the values).
   Title: "Top 10 cities by delivered sales".
7. **Footnote.** **Insert → Text box** at the bottom with the text from the table.

Clicking a bar now filters the cards and the trend to that city, and the slicers filter
everything on the page.

## 8. Page 2: Forecast vs Actual

Add a page (the **+** at the bottom) and name it `Forecast vs Actual`. The page 1 slicers don't
apply here, which is what you want: the forecast runs past the last day of data.

| Visual | Fields | Position (x, y, width, height) |
|---|---|---|
| Text box (title) | "Forecast vs actual" in 20 pt bold; second line, 11 pt: "Blue is what happened. Orange is what the model forecast without seeing those days. After the last day of data, orange is the real forecast." | 24, 16, 976, 56 |
| Slicer (scope) | `forecast_vs_actual[scope]` | 1016, 16, 240, 56 |
| Line chart | X-axis `dim_date[date]`, Y-axis `Actual GMV`, then `Forecast GMV` | 24, 88, 864, 576 |
| Card | `MAPE GMV`, renamed "Average daily forecast miss (GMV)" | 904, 88, 352, 104 |
| Card | `MAPE Orders`, renamed "Average daily forecast miss (orders)" | 904, 208, 352, 104 |
| Text box (note) | the text of `bi/data/forecast_note.txt` | 904, 328, 352, 336 |
| Text box (footnote) | "Walk-forward backtest: the model is refitted every 14 days and scored only on days after its cut-off. Orders are synthetic." in 9 pt | 24, 676, 1232, 32 |

Steps:

1. **Scope slicer.** Slicer with `forecast_vs_actual[scope]`. **Style: Dropdown**. Under
   **Selection**, turn on **Single select**, then choose `National`. Title: "Area". Every number
   on this page is for one scope at a time; adding scopes together would double-count, because
   `National` already includes the cities.
2. **Line chart.** Click **Line chart**. Drag `dim_date[date]` to **X-axis** (choose `date`, not
   the hierarchy), then `Actual GMV` and `Forecast GMV` to **Y-axis**, in that order, so actual is
   blue and the forecast orange. Title: "Daily delivered sales: actual and forecast".
   - **Format visual → Visual → X-axis → Type: Continuous**.
   - **Format visual → Visual → Lines**: under *Apply settings to*, choose `Forecast GMV`, and set
     **Line style: Dashed**. A dashed line reads as "projected", and it keeps the two lines apart
     without relying on color.
   - **Format visual → Visual → Legend**: on, position **Top left**.
3. **The 80% band.** With the line chart selected, open the **Analytics** pane (the magnifying
   glass icon in the Visualizations pane) → **Error bars**:
   - Series: `Forecast GMV`, turn **Enabled** on.
   - Drag `Forecast GMV Upper` to **Upper bound** and `Forecast GMV Lower` to **Lower bound**.
   - **Relationship to measure: Absolute**.
   - Under **Bar**, turn it off. Under **Error band**, turn it on, color orange (theme color 2),
     transparency 80%. (In some versions these switches sit under **Format visual → Error bars**.)

   The first 14 days have no band: it is built from earlier test periods' errors, and the first
   period has none before it.
4. **Optional marker.** In the Analytics pane, **X-axis constant line → Add line**, value: the
   first `future` date (1 July 2026 on the full data), label "Forecast starts".
5. **Cards.** A Card with `MAPE GMV`, renamed "Average daily forecast miss (GMV)", and one with
   `MAPE Orders`, renamed "Average daily forecast miss (orders)". With `National` selected on the
   full data, these show 0.96% and 0.64%; the second is the figure in
   [docs/METRICS.md](../docs/METRICS.md).
6. **The note.** **Insert → Text box**, paste the contents of `bi/data/forecast_note.txt`, make the
   first line bold, and title the box "National forecast, in plain English". The note is static
   text about `National`, so rerun `make bi-export` and paste it again after the data changes.
   On the full data it currently reads:

   > What the forecast says (national GMV, ETS model)
   > Next 14 days (1 Jul 2026 to 14 Jul 2026): about ₹9.83 crore expected, 0.6% more than the
   > previous 14 days.
   > How far to trust it: tested on 8 past 14-day periods, its daily GMV forecast was off by
   > 0.96% on average.
   > The shaded band is where 80% of earlier errors fell; in the test periods, 80.6% of actual
   > days landed inside it.
   > Orders are synthetic, so this describes the dataset, not a real business.

7. **Footnote.** A text box at the bottom with the text from the table.

## 9. Check the numbers

Compare the row of numbers from step 5 (rerun it in DAX query view any time) with
`bi/data/export_manifest.json`:

| DAX result | Manifest field | Full data, exported 2026-11-10 |
|---|---|---|
| Total GMV | `totals_for_checking_the_report.gmv` | 4,418,169,887 |
| Orders | `totals_for_checking_the_report.orders` | 10,000,000 |
| Delivered Orders | `totals_for_checking_the_report.delivered_orders` | 9,150,122 |
| MAPE Orders, National | `forecast.national_mape_pct.orders` ÷ 100 | 0.00639 |
| MAPE GMV, National | `forecast.national_mape_pct.gmv` ÷ 100 | 0.00957 |
| Days Inside Band %, National | `forecast.band_coverage_pct.gmv` ÷ 100 | 0.806 |

They should agree to the rounding shown. If a total is off, a relationship or a type from steps 2
and 3 is wrong; if a MAPE is off, check that the scope slicer is on one value.

## 10. Save and commit the report

1. **File → Save as** → `bi\zomato_platform.pbix` in the repository.
2. Commit it:

   ```bash
   git add bi/zomato_platform.pbix
   git commit -m "feat(bi): Power BI report"
   ```

`.gitattributes` marks `*.pbix` as binary, and the pre-commit hook allows `.pbix` files up to
25 MB (other files stay capped at 800 KB). The file contains the imported tables: city-by-day
totals and counts, with no customer rows or personal data. The `BiDataFolder` parameter holds
your local path; anyone else changes it under **Home → Transform data → Edit parameters**, then
clicks **Refresh**.

After a new `make bi-export`, open the `.pbix` and click **Home → Refresh**.

## 11. Screenshots for the README

Take these at the default 16:9 page size, with the Filters, Visualizations and Data panes
collapsed (the `>>` arrows) and **View → Fit to page**. Press **Win + Shift + S**, drag over the
report page only, and save the image from the notification as a PNG under the exact name. Keep
each under 800 KB (the pre-commit limit); a report page is usually 150–400 KB.

| File | What to show |
|---|---|
| `bi/screenshots/01_business_overview.png` | Page 1, all cities, the full date range |
| `bi/screenshots/02_forecast_vs_actual.png` | Page 2, scope `National`, with the band visible |
| `bi/screenshots/03_star_schema.png` | Model view, all five tables and the four relationships visible |
| `bi/screenshots/04_city_drilldown.png` (optional) | Page 1 with one city chosen in the slicer and the last six months in the date slicer |

Then open [README.md](../README.md), find the commented-out images in the "Power BI dashboard"
section, remove the `<!--` and `-->` lines around them, and remove "(pending .pbix + screenshots)"
from the Power BI bullet in [RESUME_BULLETS.md](../RESUME_BULLETS.md).

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `DataSource.NotFound` or "could not find file" on load | `BiDataFolder` points somewhere else. **Home → Transform data → Edit parameters**. |
| "This column contains duplicate values" when relating | The relationship is the wrong way round: the `dim_` table is always the "one" side. |
| A card shows a strange rate | A rate column was dragged in instead of the measure. Use `Cancel Rate` and `AOV`. |
| `GMV MoM %` is blank | The view holds only the first month of data, so there's no month before it. |
| No band for the first two weeks of page 2 | Expected: that period has no earlier errors to learn from. |
| "Mark as date table" refuses `date` | Step 2's type for `dim_date[date]` isn't **Date**. |
