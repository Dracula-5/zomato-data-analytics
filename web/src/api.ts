const apiBase = (import.meta.env.VITE_API_BASE_URL ?? "http://localhost:10000").replace(
  /\/+$/,
  "",
);

export interface Overview {
  summary: {
    first_day: string;
    last_day: string;
    orders: number;
    delivered: number;
    gmv: number;
    aov: number | null;
    cancel_rate: number | null;
    sla_breach_rate: number | null;
    customers: number;
  };
  daily: Array<{
    order_date: string;
    orders: number;
    gmv: number;
  }>;
}

export interface City {
  city: string;
  orders: number;
  delivered_orders: number;
  gmv: number;
  aov: number | null;
  cancel_rate_pct: number | null;
}

export interface Restaurant {
  rank: number;
  restaurant_name: string;
  locality: string;
  primary_cuisine: string;
  listed_rating: number | null;
  cost_for_two: number | null;
  orders: number;
  gmv: number;
  aov: number | null;
  cancel_rate_pct: number | null;
  avg_review_rating: number | null;
  reviews: number;
}

export interface QualityRule {
  rule_id: string;
  table_name: string;
  severity: string;
  action: string;
  rows_checked: number;
  rows_failed: number;
  failed_pct: number | null;
  description: string;
}

async function get<T>(path: string): Promise<T> {
  const response = await fetch(`${apiBase}${path}`);
  if (!response.ok) {
    const body = (await response.json().catch(() => null)) as { detail?: string } | null;
    throw new Error(body?.detail ?? `API request failed (${response.status})`);
  }
  return (await response.json()) as T;
}

export const api = {
  overview: () => get<Overview>("/api/v1/overview"),
  cities: () => get<{ items: City[] }>("/api/v1/cities"),
  restaurants: (city: string) =>
    get<{ items: Restaurant[] }>(`/api/v1/restaurants?city=${encodeURIComponent(city)}`),
  quality: () => get<{ rules: QualityRule[] }>("/api/v1/data-quality"),
};
