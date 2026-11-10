// Power Query (M) for the five CSV files from `make bi-export`. See bi/POWER_BI_GUIDE.md, step 2.
//
// First create a Text parameter named BiDataFolder whose value is the full path of bi\data,
// for example C:\Users\you\zomato-data-analytics\bi\data\
//
// Then, for EACH block below: Home > New Source > Blank Query, Home > Advanced Editor,
// replace everything with the block (from "let" to the last "Typed"), click Done, and rename
// the query to the name in the block's header. The names matter: the DAX uses them.
//
// Every block sets its types explicitly with the "en-US" culture, so a Windows region that
// writes 1.234,5 can't misread the dots, and empty cells become null rather than errors.


// ==== Query name: mart_city_daily ====
let
    Folder = if Text.EndsWith(BiDataFolder, "\") then BiDataFolder else BiDataFolder & "\",
    Source = Csv.Document(
        File.Contents(Folder & "mart_city_daily.csv"),
        [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]
    ),
    Headers = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),
    Blanks = Table.TransformColumns(Headers, {}, each if _ = "" then null else _),
    Typed = Table.TransformColumnTypes(
        Blanks,
        {
            {"order_date", type date},
            {"city", type text},
            {"orders", Int64.Type},
            {"delivered_orders", Int64.Type},
            {"cancelled_orders", Int64.Type},
            {"gmv", Int64.Type},
            {"aov", type number},
            {"cancel_rate", Percentage.Type},
            {"discount_rate", Percentage.Type},
            {"active_customers", Int64.Type}
        },
        "en-US"
    )
in
    Typed


// ==== Query name: mart_daily_kpis ====
let
    Folder = if Text.EndsWith(BiDataFolder, "\") then BiDataFolder else BiDataFolder & "\",
    Source = Csv.Document(
        File.Contents(Folder & "mart_daily_kpis.csv"),
        [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]
    ),
    Headers = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),
    Blanks = Table.TransformColumns(Headers, {}, each if _ = "" then null else _),
    Typed = Table.TransformColumnTypes(
        Blanks,
        {
            {"order_date", type date},
            {"iso_day_of_week", Int64.Type},
            {"is_weekend", type logical},
            {"orders", Int64.Type},
            {"delivered_orders", Int64.Type},
            {"gmv", Int64.Type},
            {"aov", type number},
            {"cancel_rate", Percentage.Type},
            {"sla_breach_rate", Percentage.Type},
            {"active_customers", Int64.Type}
        },
        "en-US"
    )
in
    Typed


// ==== Query name: dim_city ====
let
    Folder = if Text.EndsWith(BiDataFolder, "\") then BiDataFolder else BiDataFolder & "\",
    Source = Csv.Document(
        File.Contents(Folder & "dim_city.csv"),
        [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]
    ),
    Headers = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),
    Blanks = Table.TransformColumns(Headers, {}, each if _ = "" then null else _),
    Typed = Table.TransformColumnTypes(
        Blanks,
        {
            {"city", type text},
            {"localities", Int64.Type},
            {"restaurants", Int64.Type},
            {"avg_restaurant_rating", type number},
            {"avg_cost_for_two", type number}
        },
        "en-US"
    )
in
    Typed


// ==== Query name: dim_date ====
let
    Folder = if Text.EndsWith(BiDataFolder, "\") then BiDataFolder else BiDataFolder & "\",
    Source = Csv.Document(
        File.Contents(Folder & "dim_date.csv"),
        [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]
    ),
    Headers = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),
    Blanks = Table.TransformColumns(Headers, {}, each if _ = "" then null else _),
    Typed = Table.TransformColumnTypes(
        Blanks,
        {
            {"date", type date},
            {"year", Int64.Type},
            {"month", Int64.Type},
            {"month_name", type text},
            {"month_label", type text},
            {"year_month", type text},
            {"month_start", type date},
            {"week_start", type date},
            {"day_of_month", Int64.Type},
            {"iso_day_of_week", Int64.Type},
            {"day_name", type text},
            {"is_weekend", type logical}
        },
        "en-US"
    )
in
    Typed


// ==== Query name: forecast_vs_actual ====
let
    Folder = if Text.EndsWith(BiDataFolder, "\") then BiDataFolder else BiDataFolder & "\",
    Source = Csv.Document(
        File.Contents(Folder & "forecast_vs_actual.csv"),
        [Delimiter = ",", Encoding = 65001, QuoteStyle = QuoteStyle.Csv]
    ),
    Headers = Table.PromoteHeaders(Source, [PromoteAllScalars = true]),
    Blanks = Table.TransformColumns(Headers, {}, each if _ = "" then null else _),
    Typed = Table.TransformColumnTypes(
        Blanks,
        {
            {"date", type date},
            {"scope", type text},
            {"period", type text},
            {"fold", Int64.Type},
            {"actual_gmv", type number},
            {"forecast_gmv", type number},
            {"forecast_gmv_lower", type number},
            {"forecast_gmv_upper", type number},
            {"actual_orders", type number},
            {"forecast_orders", type number},
            {"forecast_orders_lower", type number},
            {"forecast_orders_upper", type number}
        },
        "en-US"
    )
in
    Typed
