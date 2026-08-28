output "environment" {
  value = var.environment
}

output "app_url" {
  description = "Application URL (ALB DNS)"
  value       = "http://${aws_lb.app.dns_name}"
}

output "ecs_cluster" {
  value = aws_ecs_cluster.main.name
}
