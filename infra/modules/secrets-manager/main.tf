# One Secrets Manager secret holding both the SnapLogic admin credentials and
# the Anthropic API key (~$0.40/month). Terraform creates the container only;
# the VALUE is set out-of-band so it never lands in Terraform state:
#
#   aws secretsmanager put-secret-value \
#     --secret-id <name> \
#     --secret-string '{
#       "SNAPLOGIC_BASE_URL": "https://elastic.snaplogic.com",
#       "SNAPLOGIC_ADMIN_USERNAME": "...",
#       "SNAPLOGIC_ADMIN_PASSWORD": "...",
#       "SNAPLOGIC_ORG_NAME": "...",
#       "SNAPLOGIC_SOLUTION_PROJECT_SPACE": "...",
#       "SNAPLOGIC_SOLUTION_PROJECT": "...",
#       "SNAPLOGIC_STUDENT_PROJECT_SPACE": "...",
#       "ANTHROPIC_API_KEY": "sk-ant-..."
#     }'

resource "aws_secretsmanager_secret" "app" {
  name        = "${var.name_prefix}-app-secrets"
  description = "SnapLogic admin credentials + Anthropic API key for the cloud evaluator."
  # Immediate delete on destroy keeps re-creates painless for this hobby-scale
  # stack; bump to 7+ days if the secret ever becomes hard to reproduce.
  recovery_window_in_days = 0

  tags = var.tags
}
