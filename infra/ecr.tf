# Shared ECR repository — both dev and prod pull from the same repo
resource "aws_ecr_repository" "app" {
  name                 = "${var.project}-app"
  image_tag_mutability = "MUTABLE"
  force_delete         = true # demo — allows terraform destroy to delete images

  image_scanning_configuration {
    scan_on_push = false
  }

  tags = { Name = "${var.project}-ecr" }
}

output "ecr_repository_url" {
  value = aws_ecr_repository.app.repository_url
}
