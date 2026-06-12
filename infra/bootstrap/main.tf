# One-time, local-state bootstrap: creates the S3 bucket that holds the
# Terraform state for envs/prod. Run once with `terraform init && terraform
# apply` from this directory, then never touch it again (state for this tiny
# stack stays local / in git history of the tfvars you used).
#
# Locking uses S3-native lockfiles (Terraform >= 1.10), so no DynamoDB lock
# table is needed — envs/prod/backend.tf sets `use_lockfile = true`.

terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

variable "aws_region" {
  description = "Region for the state bucket (match envs/prod)."
  type        = string
  default     = "eu-central-1"
}

variable "state_bucket_name" {
  description = "Globally unique name for the Terraform state bucket."
  type        = string
}

provider "aws" {
  region = var.aws_region
}

resource "aws_s3_bucket" "tf_state" {
  bucket = var.state_bucket_name

  lifecycle {
    prevent_destroy = true
  }

  tags = {
    Project = "snaplogic-exercise-evaluator"
    Purpose = "terraform-state"
  }
}

resource "aws_s3_bucket_versioning" "tf_state" {
  bucket = aws_s3_bucket.tf_state.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tf_state" {
  bucket = aws_s3_bucket.tf_state.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "tf_state" {
  bucket                  = aws_s3_bucket.tf_state.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

output "state_bucket_name" {
  value = aws_s3_bucket.tf_state.bucket
}
