import { useEffect, useMemo, useState } from "react";
import {
  Activity,
  ArrowDownRight,
  ArrowUpRight,
  BarChart3,
  CircleAlert,
  Clock3,
  MapPin,
  RefreshCw,
  ShieldCheck,
  Utensils,
} from "lucide-react";
import { api, type City, type Overview, type QualityRule, type Restaurant } from "./api";

type LoadState<T> = { value: T | null; error: string | null; loading: boolean };

function useLoad<T>(loader: () => Promise<T>, dependencies: unknown[] = []) {
  const [state, setState] = useState<LoadState<T>>({
    value: null,
    error: null,
    loading: true,
  });
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    let active = true;
    setState((current) => ({ ...current, loading: true, error: null }));
    loader()
      .then((value) => {
        if (active) setState({ value, error: null, loading: false });
      })
      .catch((error: unknown) => {
        if (active) {
          setState({
            value: null,
            error: error instanceof Error ? error.message : "The request failed.",
            loading: false,
          });
        }
      });
    return () => {
      active = false;
    };
    // The dependency list is controlled by each caller.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...dependencies, refresh]);

  return { ...state, reload: () => setRefresh((value) => value + 1) };
}

function money(value: number | null | undefined) {
  if (value == null || !Number.isFinite(value)) return "—";
  return new Intl.NumberFormat("en-IN", {
    style: "currency",
    currency: "INR",
    maximumFractionDigits: 0,
  }).format(value);
}

function number(value: number | null | undefined) {
  return value == null ? "—" : new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 }).format(value);
}

function percent(value: number | null | undefined, asPercent = false) {
  if (value == null || !Number.isFinite(value)) return "—";
  return `${(asPercent ? value : value * 100).toFixed(1)}%`;
}

function dateLabel(value: string) {
  return new Date(`${value.slice(0, 10)}T00:00:00`).toLocaleDateString("en-IN", {
    month: "short",
    day: "numeric",
  });
}

function TrendChart({ rows }: { rows: Overview["daily"] }) {
  const points = useMemo(() => {
    const subset = rows.slice(-42);
    if (!subset.length) return "";
    const values = subset.map((row) => row.orders);
    const min = Math.min(...values);
    const max = Math.max(...values);
    const span = max - min || 1;
    return values
      .map((value, index) => {
        const x = (index / Math.max(values.length - 1, 1)) * 100;
        const y = 88 - ((value - min) / span) * 72;
        return `${x},${y}`;
      })
      .join(" ");
  }, [rows]);

  return (
    <div className="trend-chart">
      <div className="chart-caption">
        <span>Daily order volume</span>
        <span className="chart-period">Last {Math.min(rows.length, 42)} days</span>
      </div>
      <svg viewBox="0 0 100 100" preserveAspectRatio="none" role="img" aria-label="Daily order trend">
        <defs>
          <linearGradient id="area-fill" x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%" stopColor="#f15a3b" stopOpacity=".25" />
            <stop offset="100%" stopColor="#f15a3b" stopOpacity="0" />
          </linearGradient>
        </defs>
        {[20, 42, 64, 88].map((y) => (
          <line key={y} x1="0" x2="100" y1={y} y2={y} className="chart-grid" />
        ))}
        {points && <polygon points={`0,100 ${points} 100,100`} fill="url(#area-fill)" />}
        {points && <polyline points={points} className="chart-line" />}
      </svg>
      <div className="chart-dates">
        <span>{rows.length ? dateLabel(rows[Math.max(rows.length - 42, 0)].order_date) : "—"}</span>
        <span>{rows.length ? dateLabel(rows[rows.length - 1].order_date) : "—"}</span>
      </div>
    </div>
  );
}

function App() {
  const overview = useLoad<Overview>(() => api.overview());
  const cities = useLoad<{ items: City[] }>(() => api.cities());
  const quality = useLoad<{ rules: QualityRule[] }>(() => api.quality());
  const [city, setCity] = useState("");
  const restaurants = useLoad<{ items: Restaurant[] }>(
    () => (city ? api.restaurants(city) : Promise.resolve({ items: [] })),
    [city],
  );

  useEffect(() => {
    if (!city && cities.value?.items.length) setCity(cities.value.items[0].city);
  }, [city, cities.value]);

  const summary = overview.value?.summary;
  const ruleHits = quality.value?.rules.reduce((total, rule) => total + rule.rows_failed, 0) ?? 0;
  const activeErrors = [overview.error, cities.error, quality.error].filter(Boolean);

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <a className="brand" href="#" aria-label="Zomato platform home">
          <span className="brand-mark">z</span>
          <span>
            <strong>zomato</strong>
            <small>AI DATA PLATFORM</small>
          </span>
        </a>
        <div className="nav-label">WORKSPACE</div>
        <nav>
          <a className="nav-item active" href="#overview"><BarChart3 size={17} /> Overview</a>
          <a className="nav-item" href="#cities"><MapPin size={17} /> City performance</a>
          <a className="nav-item" href="#restaurants"><Utensils size={17} /> Restaurants</a>
          <a className="nav-item" href="#quality"><ShieldCheck size={17} /> Data quality</a>
        </nav>
        <div className="sidebar-bottom">
          <div className="source-card">
            <span className="live-dot" />
            <div><strong>Demo environment</strong><small>SYNTHETIC ORDERS & REVIEWS</small></div>
          </div>
          <a className="github-link" href="https://github.com/Dracula-5/zomato-data-analytics" target="_blank" rel="noreferrer">
            Project repository <ArrowUpRight size={14} />
          </a>
        </div>
      </aside>

      <main className="main-content" id="overview">
        <header className="topbar">
          <div>
            <div className="breadcrumb">Workspace <span>/</span> Overview</div>
            <h1>Food delivery, in focus.</h1>
            <p className="subtitle">A data and AI platform for understanding marketplace performance.</p>
          </div>
          <button className="refresh-button" onClick={() => { overview.reload(); cities.reload(); quality.reload(); }}>
            <RefreshCw size={15} /> Refresh
          </button>
        </header>

        <div className="demo-banner">
          <CircleAlert size={16} />
          <span><strong>Portfolio demo:</strong> orders and reviews are synthetic; results are not real-world operating claims.</span>
        </div>
        {activeErrors.length > 0 && (
          <div className="error-banner" role="alert">
            <CircleAlert size={17} />
            <span>Some live data could not be loaded: {[...new Set(activeErrors)].join(" · ")}</span>
          </div>
        )}

        <section className="kpi-grid" aria-label="Marketplace key performance indicators">
          <KpiCard label="Gross merchandise value" value={money(summary?.gmv)} icon={<BarChart3 />} hint="Delivered orders only" />
          <KpiCard label="Orders" value={number(summary?.orders)} icon={<Activity />} hint={`${number(summary?.delivered)} delivered`} />
          <KpiCard label="Average order value" value={money(summary?.aov)} icon={<ArrowUpRight />} hint="GMV / delivered orders" />
          <KpiCard label="Cancellation rate" value={percent(summary?.cancel_rate)} icon={<ArrowDownRight />} hint={`${number(summary?.customers)} active customers`} />
        </section>

        <section className="content-grid">
          <article className="panel trend-panel">
            <div className="panel-heading">
              <div><span className="eyebrow">MARKETPLACE</span><h2>Order activity</h2></div>
              <span className="date-range">{summary ? `${dateLabel(summary.first_day)} — ${dateLabel(summary.last_day)}` : "Loading dates…"}</span>
            </div>
            {overview.loading && !overview.value ? <Skeleton height={230} /> : <TrendChart rows={overview.value?.daily ?? []} />}
          </article>
          <article className="panel health-panel">
            <div className="panel-heading">
              <div><span className="eyebrow">PIPELINE STATUS</span><h2>Data health</h2></div>
              <ShieldCheck className="heading-icon" size={19} />
            </div>
            <div className="health-score">
              <div className="score-ring"><strong>{quality.value?.rules.length ?? "—"}</strong><span>rules</span></div>
              <div><strong>{number(ruleHits)}</strong><small>rule-level hits</small></div>
            </div>
            <div className="health-row"><span><span className="status-dot green" /> dbt marts</span><strong>Ready</strong></div>
            <div className="health-row"><span><span className="status-dot amber" /> Quality rule hits</span><strong>{number(ruleHits)}</strong></div>
            <div className="health-note"><Clock3 size={14} /> Rule hits can include the same record more than once.</div>
          </article>
        </section>

        <section className="content-grid lower-grid">
          <article className="panel" id="cities">
            <div className="panel-heading">
              <div><span className="eyebrow">GEOGRAPHY</span><h2>City performance</h2></div>
              <span className="panel-count">{cities.value?.items.length ?? 0} cities</span>
            </div>
            <div className="city-list">
              {cities.loading && !cities.value ? <Skeleton height={260} /> : cities.value?.items.slice(0, 7).map((item, index) => {
                const maxGmv = cities.value?.items[0]?.gmv || 1;
                return (
                  <button className={`city-row ${city === item.city ? "selected" : ""}`} key={item.city} onClick={() => setCity(item.city)}>
                    <span className="city-rank">{String(index + 1).padStart(2, "0")}</span>
                    <span className="city-name">{item.city}<small>{number(item.orders)} orders</small></span>
                    <span className="city-bar"><i style={{ width: `${Math.max(3, (item.gmv / maxGmv) * 100)}%` }} /></span>
                    <strong>{money(item.gmv)}</strong>
                  </button>
                );
              })}
            </div>
          </article>

          <article className="panel" id="restaurants">
            <div className="panel-heading">
              <div><span className="eyebrow">TOP PERFORMERS</span><h2>Restaurants</h2></div>
              <select aria-label="Select city" value={city} onChange={(event) => setCity(event.target.value)}>
                {(cities.value?.items ?? []).map((item) => <option value={item.city} key={item.city}>{item.city}</option>)}
              </select>
            </div>
            <div className="table-wrap">
              <table>
                <thead><tr><th>Restaurant</th><th>Orders</th><th>GMV</th><th>Rating</th></tr></thead>
                <tbody>
                  {restaurants.loading && !restaurants.value ? (
                    <tr><td colSpan={4}><Skeleton height={210} /></td></tr>
                  ) : restaurants.value?.items.slice(0, 6).map((item) => (
                    <tr key={`${item.rank}-${item.restaurant_name}`}>
                      <td><strong>{item.restaurant_name}</strong><small>{item.locality || item.primary_cuisine || "—"}</small></td>
                      <td>{number(item.orders)}</td>
                      <td>{money(item.gmv)}</td>
                      <td><span className="rating">★ {item.avg_review_rating?.toFixed(1) ?? "—"}</span></td>
                    </tr>
                  ))}
                  {!restaurants.loading && !restaurants.error && !restaurants.value?.items.length && (
                    <tr><td colSpan={4} className="empty-row">No restaurant rows for this city.</td></tr>
                  )}
                  {restaurants.error && <tr><td colSpan={4} className="empty-row">{restaurants.error}</td></tr>}
                </tbody>
              </table>
            </div>
          </article>
        </section>

        <section className="panel quality-panel" id="quality">
          <div className="panel-heading">
            <div><span className="eyebrow">GOVERNANCE</span><h2>Quality rules with findings</h2></div>
            <span className="panel-count">{quality.value?.rules.filter((rule) => rule.rows_failed > 0).length ?? 0} triggered</span>
          </div>
          <div className="quality-grid">
            {(quality.value?.rules ?? []).filter((rule) => rule.rows_failed > 0).slice(0, 6).map((rule) => (
              <QualityCard rule={rule} key={rule.rule_id} />
            ))}
            {!quality.loading && !quality.error && !quality.value?.rules.length && (
              <div className="empty-row">Quality rule results are not available in this build.</div>
            )}
          </div>
        </section>

        <footer>
          <span>ZOMATO AI DATA PLATFORM <i>•</i> BUILT FOR EXPLORATION</span>
          <span>Orders & reviews are synthetic <i>•</i> {overview.value?.summary.first_day ? `Data window ${dateLabel(overview.value.summary.first_day)} – ${dateLabel(overview.value.summary.last_day)}` : "Data window loading"}</span>
        </footer>
      </main>
    </div>
  );
}

function KpiCard({ label, value, icon, hint }: { label: string; value: string; icon: React.ReactNode; hint: string }) {
  return (
    <article className="kpi-card">
      <div className="kpi-top"><span>{label}</span><span className="kpi-icon">{icon}</span></div>
      <strong className="kpi-value">{value}</strong>
      <span className="kpi-hint">{hint}</span>
    </article>
  );
}

function QualityCard({ rule }: { rule: QualityRule }) {
  return (
    <div className="quality-card">
      <div className="quality-card-top"><span className={`severity ${rule.severity.toLowerCase()}`}>{rule.severity}</span><span>{rule.table_name}</span></div>
      <strong>{rule.rule_id}</strong>
      <p>{rule.description}</p>
      <div className="quality-foot"><span>{number(rule.rows_failed)} failed</span><span>{percent(rule.failed_pct, true)}</span></div>
    </div>
  );
}

function Skeleton({ height }: { height: number }) {
  return <div className="skeleton" style={{ height }} aria-label="Loading data" />;
}

export default App;
