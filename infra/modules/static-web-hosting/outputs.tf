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
