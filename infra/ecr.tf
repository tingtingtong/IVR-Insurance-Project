# ECR repository is managed outside Terraform so images persist across
# destroy/apply cycles.  Created once via:
#   aws ecr create-repository --repository-name ivr-app --region us-east-1

variable "ecr_repo_url" {
  description = "ECR repository URL (managed outside Terraform)"
  type        = string
  default     = "348452968516.dkr.ecr.us-east-1.amazonaws.com/ivr-app"
}

output "ecr_repository_url" {
  value = var.ecr_repo_url
}
