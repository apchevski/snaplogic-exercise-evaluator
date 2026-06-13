# Production environment — the only environment (prod-only by design).
#
# Apply order across the build-out (modules already applied stay no-op):
#   Phase 1: data, secrets, ecr            (no dependencies)
#   Phase 2: worker, api                   (need the image pushed to ECR first
#                                           — set var.image_tag after the
#                                           first manual `docker push`)
#   Phase 3: cognito                       (callback_urls can stay localhost
#                                           until Phase 5)
#   Phase 5: web_hosting, budget           (then re-apply: the CloudFront URL
#                                           feeds Cognito callbacks + CORS via
#                                           the wiring below — second apply
#                                           closes the loop)
#   Phase 6: github_oidc

terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region
  default_tags {
    tags = local.tags
  }
}

locals {
  tags = {
    Project = "snaplogic-exercise-evaluator"
    Env     = "prod"
  }
  image_uri = "${module.ecr.repository_url}:${var.image_tag}"
  # CloudFront URL feeds Cognito callbacks + API CORS. On the very first
  # apply (before web_hosting exists in state) these resolve to the
  # localhost defaults; the post-Phase-5 re-apply wires the real URL.
  cloudfront_url = module.web_hosting.cloudfront_url
}

module "data" {
  source           = "../../modules/data"
  name_prefix      = var.name_prefix
  data_bucket_name = var.data_bucket_name
  tags             = local.tags
}

module "secrets" {
  source      = "../../modules/secrets"
  name_prefix = var.name_prefix
  tags        = local.tags
}

module "ecr" {
  source      = "../../modules/ecr"
  name_prefix = var.name_prefix
  tags        = local.tags
}

module "worker" {
  source      = "../../modules/worker"
  name_prefix = var.name_prefix
  image_uri   = local.image_uri
  table_name  = module.data.table_name
  table_arn   = module.data.table_arn
  bucket_name = module.data.bucket_name
  bucket_arn  = module.data.bucket_arn
  secret_arn  = module.secrets.secret_arn
  judge_model = var.judge_model
  tags        = local.tags
}

module "cognito" {
  source        = "../../modules/cognito"
  name_prefix   = var.name_prefix
  domain_prefix = var.cognito_domain_prefix
  callback_urls = concat(["${local.cloudfront_url}/"], var.extra_callback_urls)
  logout_urls   = concat(["${local.cloudfront_url}/"], var.extra_callback_urls)
  tags          = local.tags
}

module "api" {
  source             = "../../modules/api"
  name_prefix        = var.name_prefix
  image_uri          = local.image_uri
  table_name         = module.data.table_name
  table_arn          = module.data.table_arn
  bucket_name        = module.data.bucket_name
  bucket_arn         = module.data.bucket_arn
  queue_url          = module.worker.queue_url
  queue_arn          = module.worker.queue_arn
  jwt_issuer         = module.cognito.issuer
  jwt_audience       = module.cognito.client_id
  cors_allow_origins = concat([local.cloudfront_url], var.extra_cors_origins)
  allowed_cidrs      = var.allowed_cidrs
  tags               = local.tags
}

module "web_hosting" {
  source          = "../../modules/web_hosting"
  name_prefix     = var.name_prefix
  spa_bucket_name = var.spa_bucket_name
  allowed_cidrs   = var.allowed_cidrs
  tags            = local.tags
}

module "budget" {
  source      = "../../modules/budget"
  limit_usd   = 10
  alert_email = var.budget_alert_email
}

module "github_oidc" {
  source      = "../../modules/github_oidc"
  name_prefix = var.name_prefix
  github_repo = var.github_repo
  tags        = local.tags
}
