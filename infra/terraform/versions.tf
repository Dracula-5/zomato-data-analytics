terraform {
  required_version = ">= 1.9"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region  = var.aws_region
  profile = var.aws_profile

  # Refuse to run against any other account, e.g. if the wrong credentials are active.
  allowed_account_ids = [var.aws_account_id]

  default_tags {
    tags = {
      Project   = "zomato-ai-data-platform"
      ManagedBy = "terraform"
    }
  }
}
