# Cognito: one user pool, admin-created users only (invite-based, no
# self-signup), groups admin/mentor/student, and the SPA app client
# (Authorization Code + PKCE via the Hosted UI).

data "aws_region" "current" {}

resource "aws_cognito_user_pool" "main" {
  name = "${var.name_prefix}-users"

  # Optional TOTP (authenticator app) MFA. With OPTIONAL MFA the hosted UI does
  # NOT prompt users to enroll (that only happens when MFA is "ON"), so the SPA
  # drives enrollment itself from the in-app Settings dialog: associate -> verify
  # -> set-preference, via the Cognito self-service API authorized by the user's
  # access token (needs the aws.cognito.signin.user.admin scope on the client
  # below). Software-token MFA must be enabled here before any user can register
  # it. Flip to "ON" to require a second factor for everyone.
  mfa_configuration = "OPTIONAL"
  software_token_mfa_configuration {
    enabled = true
  }

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

# Members are created by the API Lambda when a student is registered with an
# email (POST /v1/students) — never by hand alongside admin/mentor invites.
resource "aws_cognito_user_group" "student" {
  name         = "student"
  user_pool_id = aws_cognito_user_pool.main.id
  description  = "View only: exercises and grades. No grading, no edits."
  precedence   = 3
}

resource "aws_cognito_user_pool_client" "spa" {
  name         = "${var.name_prefix}-spa"
  user_pool_id = aws_cognito_user_pool.main.id

  generate_secret = false # public SPA client: PKCE instead of a secret

  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_flows                  = ["code"]
  # aws.cognito.signin.user.admin lets the SPA call the Cognito self-service API
  # (change password, update attributes, associate/verify a TOTP authenticator,
  # set MFA preference) with the signed-in user's access token — powers the
  # in-app Settings dialog. Existing sessions must sign out and back in once
  # after this is deployed to receive an access token carrying the new scope.
  allowed_oauth_scopes         = ["openid", "email", "profile", "aws.cognito.signin.user.admin"]
  supported_identity_providers = ["COGNITO"]

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
