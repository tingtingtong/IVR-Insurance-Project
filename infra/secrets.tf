# Secrets Manager — per-environment secrets
# Actual secret values are set manually in the AWS console or via CLI, not in Terraform.

resource "aws_secretsmanager_secret" "app_secrets" {
  name                    = "${var.environment}/${var.project}/app-secrets"
  recovery_window_in_days = 0 # demo — immediate deletion on destroy
  tags                    = { Name = "${var.environment}-${var.project}-secrets" }
}
