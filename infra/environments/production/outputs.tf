output "api_endpoint" {
  value = module.api.api_endpoint
}

output "cloudfront_url" {
  value = module.web_hosting.cloudfront_url
}

output "spa_bucket" {
  value = module.web_hosting.bucket_name
}

output "cloudfront_distribution_id" {
  value = module.web_hosting.distribution_id
}

output "cognito_user_pool_id" {
  value = module.cognito.user_pool_id
}

output "cognito_client_id" {
  value = module.cognito.client_id
}

output "cognito_hosted_ui_domain" {
  value = module.cognito.hosted_ui_domain
}

output "cognito_issuer" {
  value = module.cognito.issuer
}

output "ecr_repository_url" {
  value = module.ecr.repository_url
}

output "jobs_queue_url" {
  value = module.worker.queue_url
}

output "data_bucket" {
  value = module.data.bucket_name
}

output "dynamodb_table" {
  value = module.data.table_name
}

output "secret_name" {
  value = module.secrets.secret_name
}

output "github_deploy_role_arn" {
  value = module.github_oidc.deploy_role_arn
}
