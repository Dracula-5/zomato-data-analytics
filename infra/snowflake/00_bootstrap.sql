-- Run ONCE, by a person, in a Snowsight worksheet, as ACCOUNTADMIN.
-- `make cloud-keys` fills in the public key and writes artifacts/cloud/00_bootstrap.filled.sql:
-- run that file, not this template. Safe to run again.
--
-- After this, the project's tools sign in as ZAP_SVC (key pair, no password) and do
-- everything else from versioned SQL. ACCOUNTADMIN is never used by code.
USE ROLE ACCOUNTADMIN;

-- Cost guard: an X-Small warehouse (1 credit per hour while running) that stops itself after
-- 60 seconds idle, and a monitor that force-stops it at 10 credits in a month.
CREATE WAREHOUSE IF NOT EXISTS ZAP_XS
  WAREHOUSE_SIZE = XSMALL
  AUTO_SUSPEND = 60
  AUTO_RESUME = TRUE
  INITIALLY_SUSPENDED = TRUE;

CREATE RESOURCE MONITOR IF NOT EXISTS ZAP_MONITOR WITH
  CREDIT_QUOTA = 10
  FREQUENCY = MONTHLY
  START_TIMESTAMP = IMMEDIATELY
  TRIGGERS
    ON 75 PERCENT DO NOTIFY
    ON 100 PERCENT DO SUSPEND_IMMEDIATE;
ALTER WAREHOUSE ZAP_XS SET RESOURCE_MONITOR = ZAP_MONITOR;

-- ZAP_DEPLOYER builds everything; ZAP_ANALYST only reads, and sees PII masked.
CREATE ROLE IF NOT EXISTS ZAP_DEPLOYER;
CREATE ROLE IF NOT EXISTS ZAP_ANALYST;
GRANT ROLE ZAP_ANALYST TO ROLE ZAP_DEPLOYER;
GRANT ROLE ZAP_DEPLOYER TO ROLE SYSADMIN;

CREATE DATABASE IF NOT EXISTS ZOMATO;
GRANT OWNERSHIP ON DATABASE ZOMATO TO ROLE ZAP_DEPLOYER COPY CURRENT GRANTS;
GRANT USAGE, OPERATE ON WAREHOUSE ZAP_XS TO ROLE ZAP_DEPLOYER;
GRANT USAGE ON WAREHOUSE ZAP_XS TO ROLE ZAP_ANALYST;
GRANT CREATE INTEGRATION ON ACCOUNT TO ROLE ZAP_DEPLOYER;

-- A service user cannot sign in with a password or to the web UI.
CREATE USER IF NOT EXISTS ZAP_SVC
  TYPE = SERVICE
  DEFAULT_ROLE = ZAP_DEPLOYER
  DEFAULT_WAREHOUSE = ZAP_XS
  COMMENT = 'zomato-ai-data-platform tools; key-pair sign-in only';
ALTER USER ZAP_SVC SET RSA_PUBLIC_KEY = '__RSA_PUBLIC_KEY__';
GRANT ROLE ZAP_DEPLOYER TO USER ZAP_SVC;

-- Paste this result row back to Claude: account, region, and that the setup finished.
SELECT 'bootstrap finished' AS status, CURRENT_ORGANIZATION_NAME() AS org,
       CURRENT_ACCOUNT_NAME() AS account_name, CURRENT_ACCOUNT() AS locator, CURRENT_REGION() AS region;
