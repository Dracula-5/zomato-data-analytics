-- The keyless link to S3. Snowflake assumes an IAM role, so no AWS key is ever stored here.
-- IF NOT EXISTS matters: re-creating an integration changes its IAM user and external ID,
-- which would break the role's trust policy.
CREATE STORAGE INTEGRATION IF NOT EXISTS ZAP_S3
  TYPE = EXTERNAL_STAGE
  STORAGE_PROVIDER = 'S3'
  ENABLED = TRUE
  STORAGE_AWS_ROLE_ARN = '{{ role_arn }}'
  STORAGE_ALLOWED_LOCATIONS = ('s3://{{ bucket }}/');
