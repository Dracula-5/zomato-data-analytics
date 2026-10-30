-- Role-based masking, on top of the masked and hashed columns the marts already hold: analysts
-- can't see even those, only ZAP_DEPLOYER can. Needs Enterprise edition. Safe to run again.
-- dbt rebuilds tables, which removes a policy, so a post-hook re-applies it (macros/pii.sql).
USE DATABASE {{ database }};

CREATE MASKING POLICY IF NOT EXISTS GOVERNANCE.PII_TEXT AS (val VARCHAR) RETURNS VARCHAR ->
  CASE WHEN IS_ROLE_IN_SESSION('ZAP_DEPLOYER') THEN val ELSE '***MASKED***' END;
