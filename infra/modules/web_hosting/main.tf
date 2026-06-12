# SPA hosting: private S3 bucket + CloudFront (OAC) + a CloudFront Function
# enforcing the IP allowlist at the edge + SPA fallback for client routing.

variable "name_prefix" {
  type = string
}

variable "spa_bucket_name" {
  description = "Globally unique bucket name for the built SPA."
  type        = string
}

variable "allowed_cidrs" {
  description = "CIDRs allowed through the CloudFront Function (VPN/office). Empty list = allow all (NOT recommended)."
  type        = list(string)
  default     = []
}

variable "tags" {
  type    = map(string)
  default = {}
}

resource "aws_s3_bucket" "spa" {
  bucket = var.spa_bucket_name
  tags   = var.tags
}

resource "aws_s3_bucket_public_access_block" "spa" {
  bucket                  = aws_s3_bucket.spa.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_cloudfront_origin_access_control" "spa" {
  name                              = "${var.name_prefix}-spa-oac"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# Edge IP allowlist. CIDR matching is implemented with the integer-prefix
# trick because CloudFront Functions run a restricted JS runtime (no
# ipaddr libraries). IPv6 viewers are rejected outright when an allowlist
# is configured — the office/VPN ranges are IPv4.
resource "aws_cloudfront_function" "ip_allowlist" {
  name    = "${var.name_prefix}-ip-allowlist"
  runtime = "cloudfront-js-2.0"
  comment = "Reject viewers outside the office/VPN CIDRs before anything loads."
  publish = true
  code = templatefile("${path.module}/ip_allowlist.js.tftpl", {
    cidrs_json = jsonencode(var.allowed_cidrs)
  })
}

resource "aws_cloudfront_distribution" "spa" {
  enabled             = true
  comment             = "${var.name_prefix} SPA"
  default_root_object = "index.html"
  price_class         = "PriceClass_100"

  origin {
    domain_name              = aws_s3_bucket.spa.bucket_regional_domain_name
    origin_id                = "spa-s3"
    origin_access_control_id = aws_cloudfront_origin_access_control.spa.id
  }

  default_cache_behavior {
    target_origin_id       = "spa-s3"
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    # AWS managed CachingOptimized policy.
    cache_policy_id = "658327ea-f89d-4fab-a63d-7e88639e58f6"

    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.ip_allowlist.arn
    }
  }

  # SPA fallback: client-side routes (/students/foo) resolve to index.html.
  custom_error_response {
    error_code         = 403
    response_code      = 200
    response_page_path = "/index.html"
  }
  custom_error_response {
    error_code         = 404
    response_code      = 200
    response_page_path = "/index.html"
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }

  tags = var.tags
}

data "aws_iam_policy_document" "spa_bucket" {
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.spa.arn}/*"]
    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.spa.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "spa" {
  bucket = aws_s3_bucket.spa.id
  policy = data.aws_iam_policy_document.spa_bucket.json
}

output "bucket_name" {
  value = aws_s3_bucket.spa.bucket
}

output "distribution_id" {
  value = aws_cloudfront_distribution.spa.id
}

output "cloudfront_url" {
  value = "https://${aws_cloudfront_distribution.spa.domain_name}"
}

output "cloudfront_domain" {
  value = aws_cloudfront_distribution.spa.domain_name
}
