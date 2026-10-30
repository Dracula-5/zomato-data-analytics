locals {
  bucket_name = "zomato-ai-dp-${var.aws_account_id}"
  role_name   = "zomato-snowflake-s3-read"

  # Two-phase trust. A storage integration needs the role's ARN, and the role's trust policy
  # needs values Snowflake only generates once the integration exists.
  #   Phase 1: the account itself is trusted (a placeholder nobody assumes).
  #   Phase 2: Snowflake's user, bound to the external ID. Re-apply with both variables set.
  trust_ready     = var.snowflake_iam_user_arn != "" && var.snowflake_external_id != ""
  trust_principal = local.trust_ready ? var.snowflake_iam_user_arn : "arn:aws:iam::${var.aws_account_id}:root"
  trust_external  = local.trust_ready ? var.snowflake_external_id : "phase-1-placeholder"
}

resource "aws_s3_bucket" "lake" {
  bucket        = local.bucket_name
  force_destroy = var.force_destroy
}

resource "aws_s3_bucket_public_access_block" "lake" {
  bucket                  = aws_s3_bucket.lake.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "lake" {
  bucket = aws_s3_bucket.lake.id
  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

# SSE-S3: encrypted at rest with no key to manage and no KMS request charges.
resource "aws_s3_bucket_server_side_encryption_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Interrupted multipart uploads are invisible in a listing but still billed.
resource "aws_s3_bucket_lifecycle_configuration" "lake" {
  bucket = aws_s3_bucket.lake.id
  rule {
    id     = "abort-incomplete-uploads"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

data "aws_iam_policy_document" "tls_only" {
  statement {
    sid       = "DenyInsecureTransport"
    effect    = "Deny"
    actions   = ["s3:*"]
    resources = [aws_s3_bucket.lake.arn, "${aws_s3_bucket.lake.arn}/*"]
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "lake" {
  bucket = aws_s3_bucket.lake.id
  policy = data.aws_iam_policy_document.tls_only.json
  # S3 rejects a bucket policy while the public access block is still being applied.
  depends_on = [aws_s3_bucket_public_access_block.lake]
}

data "aws_iam_policy_document" "snowflake_trust" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "AWS"
      identifiers = [local.trust_principal]
    }
    condition {
      test     = "StringEquals"
      variable = "sts:ExternalId"
      values   = [local.trust_external]
    }
  }
}

resource "aws_iam_role" "snowflake" {
  name               = local.role_name
  description        = "Assumed by the Snowflake storage integration. Read-only on the lake."
  assume_role_policy = data.aws_iam_policy_document.snowflake_trust.json
}

# Read-only: Snowflake can load from the lake but can never change it.
data "aws_iam_policy_document" "lake_read" {
  statement {
    actions   = ["s3:GetObject", "s3:GetObjectVersion"]
    resources = ["${aws_s3_bucket.lake.arn}/*"]
  }
  statement {
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = [aws_s3_bucket.lake.arn]
  }
}

resource "aws_iam_role_policy" "lake_read" {
  name   = "lake-read"
  role   = aws_iam_role.snowflake.id
  policy = data.aws_iam_policy_document.lake_read.json
}
