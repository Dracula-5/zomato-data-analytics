variable "aws_account_id" {
  description = "The only AWS account this may run against."
  type        = string
}

variable "aws_region" {
  description = "Same region as the Snowflake account, so S3 to Snowflake has no transfer charge."
  type        = string
  default     = "ap-southeast-7"
}

variable "aws_profile" {
  description = "Named AWS CLI profile. Explicit, so stray AWS_* keys in the environment are not used."
  type        = string
  default     = "zomato"
}

variable "snowflake_iam_user_arn" {
  description = "STORAGE_AWS_IAM_USER_ARN from DESC INTEGRATION. Empty in phase 1."
  type        = string
  default     = ""
}

variable "snowflake_external_id" {
  description = "STORAGE_AWS_EXTERNAL_ID from DESC INTEGRATION. Empty in phase 1."
  type        = string
  default     = ""
}

variable "force_destroy" {
  description = "Allow `terraform destroy` to delete a bucket that still holds objects."
  type        = bool
  default     = true
}
