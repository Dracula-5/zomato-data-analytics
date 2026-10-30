# Cloud setup: S3 + Snowflake

How to run the pipeline on AWS and Snowflake. Everything else in this repo works without
any of this (`make local` is free).

**What you get:** a private, encrypted S3 lake; a Snowflake warehouse that reads it through
an IAM role, with no AWS key stored in Snowflake; and the same dbt project running there.

## What it costs

| Step | Cost | Basis |
|---|---|---|
| Steps 1 to 4 (accounts, IAM user, keys, bootstrap SQL) | free | |
| S3, full lake (0.53 GB, 2,740 files), first month | about $0.03 | AWS public price list for `ap-southeast-7`, 2026-10: $0.0225 per GB-month, $0.0045 per 1,000 uploads, $0.00036 per 1,000 reads |
| S3 afterwards | about $0.012 a month until destroyed | same |
| S3 to Snowflake transfer | none | same region |
| Snowflake compute | 1 credit per hour while the X-Small warehouse runs | stops after 60 s idle; the resource monitor force-stops it at 10 credits a month |

On a Snowflake trial, credits come from the trial balance. `make cloud-destroy` removes
everything on AWS, so S3 charges stop. Prices change: check them for your region.

## Choosing a region

Put the bucket in the **same region as your Snowflake account**, so loads have no
cross-region transfer. The region is in your Snowflake URL:
`https://app.snowflake.com/<region>.aws/<locator>/`. This guide uses `ap-southeast-7`
(Thailand). Set `aws_region` in `infra/terraform/terraform.tfvars` and `AWS_REGION` in `.env`
if yours differs.

Newer AWS regions, including Thailand, are off by default.

## 1. AWS: enable the region

Signed in as an administrator:

1. Open the region dropdown at the top right and choose **Manage Regions** (or account
   menu → **Account** → **AWS Regions**).
2. Select your region, click **Enable**, and wait until it shows *Enabled*.

## 2. AWS: a limited user for this project

Don't use root keys or a key from another project.

1. **IAM → Users → Create user**. Name: `zomato-terraform`. No console access.
2. Open the user: **Permissions → Add permissions → Create inline policy → JSON**. Paste
   `infra/iam/zomato-terraform-policy.json`. Name it `zomato-project-only`.
   It allows only `zomato-ai-dp-*` buckets and `zomato-snowflake-*` roles.
3. **Security credentials → Access keys → Create access key → Command Line Interface**.
4. In a terminal (keys are typed there, never into a file in this repo):

   ```
   aws configure --profile zomato
   ```

   Enter the two keys, your region, and `json`.

All project tools use this named profile explicitly, so other `AWS_*` keys in your
environment are ignored.

## 3. Project settings

In `.env` (gitignored):

```
AWS_REGION=ap-southeast-7
SNOWFLAKE_ACCOUNT=<locator>.<region>.aws
```

In `infra/terraform/terraform.tfvars` (gitignored):

```
aws_account_id = "<your 12-digit account id>"
```

Terraform refuses to run against any other account.

## 4. Snowflake: bootstrap

```
make cloud-keys
```

This creates an RSA key pair at `~/.snowflake/zap_svc_rsa_key.p8` (outside the repo) and
writes `artifacts/cloud/00_bootstrap.filled.sql`.

1. In Snowsight: **Projects → Worksheets → + SQL Worksheet**. Set the role to **ACCOUNTADMIN**.
2. Paste the whole file, select all, and run it.

It creates the X-Small warehouse, the resource monitor, the `ZOMATO` database, the
`ZAP_DEPLOYER` and `ZAP_ANALYST` roles, and the `ZAP_SVC` service user. A service user has
no password and can't use the web UI. ACCOUNTADMIN is not used again.

Check both sides. This changes nothing:

```
make cloud-whoami
```

## 5. Create the infrastructure (costs money from here)

```
make cloud-infra     # phase 1: bucket and IAM role, with a placeholder trust
make cloud-setup     # Snowflake: integration, schemas, stage; saves Snowflake's ARN + external ID
make cloud-infra     # phase 2: the role now trusts Snowflake's user and external ID
```

Why three commands: the storage integration needs the role's ARN, but the role's trust
policy needs values Snowflake generates only once the integration exists.

## 6. Load and build

```
make cloud-upload DATASET=sample_5pct     # try the 5% sample first
make cloud-load   DATASET=sample_5pct
make cloud-upload                         # then the full data
make cloud-load
make cloud-dbt
```

`cloud-upload` mirrors the lake, so re-running it sends only changed files. `cloud-load`
replaces each table in one transaction, so a failed load leaves the old data in place.

Moving from the sample to the full data, build with `--full-refresh` once
(`uv run zap cloud dbt build --full-refresh`). The incremental tables still hold sample rows,
and a normal run would only merge the last three days on top of them.

Set `PII_HASH_SALT` in `.env` first (see `.env.example`). `cloud-dbt` refuses to run without
one, because the fallback value is public.

## 7. Check it

```
make cloud-check
```

`check-access` runs seven statements as `ZAP_ANALYST`, with secondary roles off, and confirms
it can read the marts but not `RAW`, `STAGING` or the stage, can't change anything, and sees
no readable email. `compare` profiles each mart on Snowflake and in the local DuckDB build
(`make local-full`) and reports any measure that differs.

`make cloud-bench` times incremental against full refresh, and tests clustering. It uses
Snowflake credits.

## 8. Airflow on Snowflake (optional)

Add `SNOWFLAKE_ACCOUNT`, `AWS_HOST_DIR` and `SNOWFLAKE_KEY_HOST_DIR` to `airflow/.env`, with the
same `PII_HASH_SALT` as `.env`, then:

```
make airflow-up-snowflake
make airflow-replay-snowflake FROM=2026-06-29 TO=2026-06-30    # uses S3 and credits
make airflow-down
```

Credentials are mounted read-only into the scheduler only, so they never enter the image.
Plain `make airflow-up` doesn't mount them.

## 9. Tear down

```
make cloud-destroy
```

Deletes the bucket, all its objects and the IAM role. In Snowflake, the warehouse stops
itself; drop the `ZOMATO` database and the integration if you want them gone too.

## Security notes

- **No long-lived secret reaches Snowflake.** It assumes a read-only IAM role, bound to an
  external ID.
- **Plain-text passwords never leave the source file.** Columns classified `secret` in
  the contracts are dropped at ingestion, so they aren't in the lake, S3 or any warehouse.
- **Email is protected twice.** The marts hold only a masked form and a salted hash, and a
  masking policy (Enterprise edition) hides both from every role except `ZAP_DEPLOYER`.
- **`ZAP_DEPLOYER` has no account-wide grant rights.** `MARTS` and `GOVERNANCE` are managed
  access schemas, so their owner can set future grants without `MANAGE GRANTS`.
- **The hash salt is compiled into SQL.** dbt inlines `PII_HASH_SALT`, so it appears in
  `target/` (gitignored) and in Snowflake's query history. Treat both as sensitive.
- **The bucket** blocks all public access, is encrypted (SSE-S3) and refuses non-TLS requests.
- **Gitignored:** `.env`, `*.tfvars`, `*.tfvars.json`, Terraform state, `*.p8`.
