output "bucket" {
  value = aws_s3_bucket.lake.bucket
}

output "role_arn" {
  value = aws_iam_role.snowflake.arn
}

output "trust_phase" {
  description = "1 = placeholder trust, 2 = Snowflake is trusted."
  value       = local.trust_ready ? 2 : 1
}
