# Cognito: one user pool, admin-created users only (invite-based, no
# self-signup), groups admin/mentor, and the SPA app client (Authorization
# Code + PKCE via the Hosted UI).

variable "name_prefix" {
  type = string
}

variable "domain_prefix" {
  description = "Globally unique Cognito hosted-UI domain prefix."
  type        = string
}

variable "callback_urls" {
  description = "Allowed OAuth callback URLs (the CloudFront URL once known; localhost for token testing in Phase 3)."
  type        = list(string)
  default     = ["http://localhost:5173/"]
}

variable "logout_urls" {
  type    = list(string)
  default = ["http://localhost:5173/"]
}

variable "tags" {
  type    = map(string)
  default = {}
}

resource "aws_cognito_user_pool" "main" {
  name = "${var.name_prefix}-users"

  admin_create_user_config {
    allow_admin_create_user_only = true # invite-based; no self-signup
  }

  username_attributes      = ["email"]
  auto_verified_attributes = ["email"]

  password_policy {
    minimum_length    = 12
    require_lowercase = true
    require_numbers   = true
    require_symbols   = false
    require_uppercase = true
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  schema {
    name                = "email"
    attribute_data_type = "String"
    required            = true
    mutable             = true
    string_attribute_constraints {
      min_length = 3
      max_length = 254
    }
  }

  tags = var.tags
}

resource "aws_cognito_user_pool_domain" "main" {
  domain       = var.domain_prefix
  user_pool_id = aws_cognito_user_pool.main.id
}

resource "aws_cognito_user_group" "admin" {
  name         = "admin"
  user_pool_id = aws_cognito_user_pool.main.id
  description  = "Prep + grade + view."
  precedence   = 1
}

resource "aws_cognito_user_group" "mentor" {
  name         = "mentor"
  user_pool_id = aws_cognito_user_pool.main.id
  description  = "Grade + view."
  precedence   = 2
}

resource "aws_cognito_user_pool_client" "spa" {
  name         = "${var.name_prefix}-spa"
  user_pool_id = aws_cognito_user_pool.main.id

  generate_secret = false # public SPA client: PKCE instead of a secret

  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  allowed_oauth_scopes                 = ["openid", "email", "profile"]
  supported_identity_providers         = ["COGNITO"]

  callback_urls = var.callback_urls
  logout_urls   = var.logout_urls

  access_token_validity  = 60
  id_token_validity      = 60
  refresh_token_validity = 12
  token_validity_units {
    access_token  = "minutes"
    id_token      = "minutes"
    refresh_token = "hours"
  }

  prevent_user_existence_errors = "ENABLED"
}

data "aws_region" "current" {}

output "user_pool_id" {
  value = aws_cognito_user_pool.main.id
}

output "client_id" {
  value = aws_cognito_user_pool_client.spa.id
}

output "issuer" {
  description = "OIDC issuer URL for the API Gateway JWT authorizer + react-oidc-context."
  value       = "https://cognito-idp.${data.aws_region.current.name}.amazonaws.com/${aws_cognito_user_pool.main.id}"
}

output "hosted_ui_domain" {
  value = "https://${aws_cognito_user_pool_domain.main.domain}.auth.${data.aws_region.current.name}.amazoncognito.com"
}
