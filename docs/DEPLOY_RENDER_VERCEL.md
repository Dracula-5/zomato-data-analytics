# Deploy the public demo with Render and Vercel

The public site is a small React/Vite dashboard on Vercel. Its read-only FastAPI service
runs on Render and reads a DuckDB warehouse generated from this repository's synthetic
fixture during the Docker image build.

> **Data and cost:** orders and reviews in the public demo are synthetic. The Render
> service uses the free web-service plan configured in `render.yaml`; free services can
> sleep while idle and have cold starts. The image build runs the local fixture pipeline
> and can take several minutes. No cloud database, customer data, or live LLM key is used.
> Check the providers' current plan limits before publishing.

## What is deployed

| Component | Location | Purpose |
|---|---|---|
| React/Vite | `web/` on Vercel | Portfolio dashboard UI |
| FastAPI | `src/zap/api.py` on Render | Read-only analytics endpoints |
| DuckDB | Bundled inside the Render image | SYNTHETIC fixture warehouse |

The API exposes `/healthz`, `/api/v1/meta`, `/api/v1/overview`, `/api/v1/cities`,
`/api/v1/restaurants`, `/api/v1/delivery-sla`, and `/api/v1/data-quality`. Interactive API
documentation is available at `/docs` on the Render URL. The public API has no write or
LLM-invocation endpoints.

This deployment is a portfolio demo, not a way to publish the ignored local source files.
The existing multi-page Streamlit dashboard remains available for local use with `make app`;
the Vercel interface currently focuses on overview, city performance, restaurants, and data
quality.

## 1. Push the repository to GitHub

Commit and push the project, including `Dockerfile.render`, `render.yaml`, `src/zap/api.py`,
`web/`, `uv.lock`, and `package-lock.json`. Do not add `.env`, source CSVs, DuckDB files,
`data/`, `lake/`, or `warehouse/`.

## 2. Create the Render API

1. In Render, choose **New → Blueprint** and connect this repository.
2. Keep the repository root as the blueprint path. Render reads `render.yaml` and builds
   the API using `Dockerfile.render`.
3. Set `FRONTEND_ORIGINS` to `http://localhost:5173` for the first deployment. It is an
   origin allowlist, not a secret.
4. Deploy. Wait for `/healthz` to become healthy. Save the base URL, such as
   `https://zomato-ai-data-platform-api.onrender.com`.
5. Check `{Render URL}/api/v1/meta`, `{Render URL}/api/v1/overview`, and `{Render URL}/docs`.

The image build generates the fixture warehouse from code, so no persistent disk is needed.
Changing the synthetic fixture or pipeline requires a Render redeploy to rebuild that
warehouse. Do not configure production customer data or secrets in this demo service.

## 3. Create the Vercel site

1. In Vercel, import the same GitHub repository.
2. Set **Root Directory** to `web`.
3. Add the build-time environment variable:

   ```
   VITE_API_BASE_URL=https://<your-render-service>.onrender.com
   ```

   Use the Render origin only: no trailing slash and no `/api` suffix.
4. Deploy. Vite builds the static site from the checked-in `package-lock.json`.
5. Copy the production Vercel origin, for example `https://<project>.vercel.app`.

## 4. Allow the Vercel origin in Render

In the Render service's environment settings, change `FRONTEND_ORIGINS` to the exact
production Vercel origin. If you also use a custom domain or Vercel preview deployment,
list each required origin separated by commas:

```
https://<project>.vercel.app,https://<your-custom-domain>
```

Do not use `*` for a public deployment. Save and redeploy/restart the Render service, then
reload the Vercel site. The API permits only read-only GET requests from configured origins.

## Local full-stack smoke test

Run `make demo` to build `warehouse/fixture.duckdb`, then start each app in a separate
terminal from the repository root:

```powershell
uv run uvicorn zap.api:app --host 127.0.0.1 --port 10000
```

```powershell
cd web
npm ci
npm run dev
```

The frontend defaults to `http://localhost:10000`; the API allows the Vite origin
`http://localhost:5173` by default. Open `http://localhost:5173`.

## Updates and limits

- Frontend edits redeploy through Vercel; API or fixture changes redeploy through Render.
- `VITE_API_BASE_URL` is public build-time configuration; never put secrets in `VITE_*`.
- Render's free service can sleep, so the first request after inactivity may be slow.
- This read-only API uses one bundled demo warehouse. It is not a multi-user data service and
  does not support uploads, scheduled refreshes, authentication, or source CSV ingestion.
- For private real datasets, add authentication and private managed storage before deployment;
  do not put real customer or restaurant data into the public fixture image.
