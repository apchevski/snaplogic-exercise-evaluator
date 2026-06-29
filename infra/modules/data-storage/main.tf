# Data layer: the DynamoDB single table + the S3 bucket holding rendered
# reports (students/<slug>/<version>/) and prep-generated exercise artifacts
# (exercises/<slug>/).
#
# Item types (PK / SK):
#   STUDENT#<slug>  / META            student summary + latest_version pointer
#   STUDENT#<slug>  / REPORT#<iso-ts> one row per grading run (history kept)
#   JOB#<id>        / META            grade/prep job lifecycle + usage
#   LOCK#<key>      / META            conditional-put dedupe lock, TTL 30 min
#   EXERCISE#<slug> / META            prep status per exercise
# GSI gsi1 (entity, slug) powers the "list all students / exercises" queries.

# --- DynamoDB single table -------------------------------------------------

resource "aws_dynamodb_table" "main" {
  name                        = "${var.name_prefix}-main"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "pk"
  range_key                   = "sk"
  deletion_protection_enabled = true

  attribute {
    name = "pk"
    type = "S"
  }
  attribute {
    name = "sk"
    type = "S"
  }
  attribute {
    name = "entity"
    type = "S"
  }
  attribute {
    name = "slug"
    type = "S"
  }

  global_secondary_index {
    name            = "gsi1"
    hash_key        = "entity"
    range_key       = "slug"
    projection_type = "ALL"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = var.tags
}

# --- S3 bucket: reports + exercise artifacts --------------------------------

resource "aws_s3_bucket" "data" {
  bucket = var.data_bucket_name
  tags   = var.tags
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
