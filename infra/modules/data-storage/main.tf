# Data layer: the DynamoDB single table + the S3 bucket holding rendered
# reports (students/<slug>/<version>/) and sync-generated exercise artifacts
# (exercises/<slug>/).
#
# Item types (PK / SK):
#   STUDENT#<slug>  / META            student summary + latest_version pointer
#   STUDENT#<slug>  / REPORT#<iso-ts> one row per grading run (history kept)
#   JOB#<id>        / META            grade/sync job lifecycle + usage
#   LOCK#<key>      / META            conditional-put dedupe lock, TTL 30 min
#   EXERCISE#<slug> / META            sync status per exercise
# GSI gsi1 (entity, slug) powers the "list all students / exercises" queries.

# --- DynamoDB single table -------------------------------------------------

resource "aws_dynamodb_table" "main" {
  name                        = "${var.name_prefix}-main"
  billing_mode                = "PAY_PER_REQUEST"
  hash_key                    = "pk"
  range_key                   = "sk"
  deletion_protection_enabled = true

  # This table + the data bucket ARE the product's data (exercises are
  # authored in AWS, not in git) — infra is cattle, data is sacred.
  lifecycle {
    prevent_destroy = true
  }

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

  # Sole home of authored exercises + all grading history. A careless
  # `terraform destroy` must not be able to take it along.
  lifecycle {
    prevent_destroy = true
  }
}

# Authored exercise content is edited in place from the web UI; versioning
# turns an accidental overwrite/delete into a non-event. Noncurrent versions
# are kept 90 days so the insurance stays ~free at this scale.
resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "data" {
  bucket     = aws_s3_bucket.data.id
  depends_on = [aws_s3_bucket_versioning.data]

  rule {
    id     = "expire-noncurrent"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }
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

# The SPA uploads new-exercise input files straight to S3 via presigned PUT
# URLs (POST /v1/exercises returns them) — a cross-origin XHR, so the bucket
# must answer the browser's preflight. Presigned GET downloads are plain
# navigations and never needed CORS.
resource "aws_s3_bucket_cors_configuration" "data" {
  count  = length(var.cors_allow_origins) > 0 ? 1 : 0
  bucket = aws_s3_bucket.data.id

  cors_rule {
    allowed_methods = ["PUT"]
    allowed_origins = var.cors_allow_origins
    allowed_headers = ["*"]
    max_age_seconds = 3600
  }
}
