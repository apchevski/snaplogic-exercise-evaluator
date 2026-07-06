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

module "data" {
  source           = "../../modules/data-storage"
  name_prefix      = var.name_prefix
  data_bucket_name = local.data_bucket_name
  # Same origins as the API's CORS: presigned-PUT uploads from the SPA.
  # References web_hosting, so on a greenfield build-out the CORS rule only
  # lands in the Phase-5 re-apply (fine — nothing uploads before then).
  cors_allow_origins = concat([local.cloudfront_url], var.extra_cors_origins)
  tags               = local.tags
}

module "secrets" {
  source      = "../../modules/secrets-manager"
  name_prefix = var.name_prefix
  tags        = local.tags
}

module "ecr" {
  source      = "../../modules/elastic-container-registry"
  name_prefix = var.name_prefix
  tags        = local.tags
}

module "worker" {
  source      = "../../modules/sqs-worker"
  name_prefix = var.name_prefix
  image_uri   = local.image_uri
  table_name  = module.data.table_name
  table_arn   = module.data.table_arn
  bucket_name = local.data_bucket_name
  bucket_arn  = module.data.bucket_arn
  secret_arn  = module.secrets.secret_arn
  judge_model = var.judge_model
  tags        = local.tags
}

module "cognito" {
  source        = "../../modules/cognito-auth"
  name_prefix   = var.name_prefix
  domain_prefix = local.cognito_domain_prefix
  callback_urls = concat(["${local.cloudfront_url}/"], var.extra_callback_urls)
  logout_urls   = concat(["${local.cloudfront_url}/"], var.extra_callback_urls)
  tags          = local.tags
}

module "api" {
  source             = "../../modules/api-gateway"
  name_prefix        = var.name_prefix
  image_uri          = local.image_uri
  table_name         = module.data.table_name
  table_arn          = module.data.table_arn
  bucket_name        = local.data_bucket_name
  bucket_arn         = module.data.bucket_arn
  queue_url          = module.worker.queue_url
  queue_arn          = module.worker.queue_arn
  secret_arn         = module.secrets.secret_arn
  user_pool_id       = module.cognito.user_pool_id
  user_pool_arn      = module.cognito.user_pool_arn
  jwt_issuer         = module.cognito.issuer
  jwt_audience       = module.cognito.client_id
  cors_allow_origins = concat([local.cloudfront_url], var.extra_cors_origins)
  allowed_cidrs      = var.allowed_cidrs
  tags               = local.tags
}

module "web_hosting" {
  source          = "../../modules/static-web-hosting"
  name_prefix     = var.name_prefix
  spa_bucket_name = local.spa_bucket_name
  allowed_cidrs   = var.allowed_cidrs
  tags            = local.tags
}

module "budget" {
  source      = "../../modules/billing-budget"
  name_prefix = var.name_prefix
  limit_usd   = var.budget_limit_usd
  alert_email = var.budget_alert_email
}

module "github_oidc" {
  source      = "../../modules/github-oidc"
  name_prefix = var.name_prefix
  github_repo = var.github_repo
  tags        = local.tags
}
