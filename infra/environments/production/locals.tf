locals {
  tags = {
    Project = var.name_prefix
    Env     = var.environment
  }

  image_uri             = "${module.ecr.repository_url}:${var.image_tag}"
  cloudfront_url        = module.web_hosting.cloudfront_url
  data_bucket_name      = "${var.name_prefix}-data"
  spa_bucket_name       = "${var.name_prefix}-spa"
  cognito_domain_prefix = var.name_prefix
}
