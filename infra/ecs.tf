# ── ECS Cluster (shared) ─────────────────────────────────────────────────────
resource "aws_ecs_cluster" "main" {
  name = "${var.project}-cluster"
  tags = { Name = "${var.project}-cluster" }
}

# ── CloudWatch log group ────────────────────────────────────────────────────
resource "aws_cloudwatch_log_group" "app" {
  name              = "/ecs/${var.environment}-${var.project}"
  retention_in_days = 7
}

# ── ECS Task Definition ────────────────────────────────────────────────────
locals {
  redis_db  = var.environment == "prod" ? 1 : 0
  redis_url = "redis://${aws_elasticache_cluster.main.cache_nodes[0].address}:6379/${local.redis_db}"
  db_url    = "postgresql://${var.db_username}:${var.db_password}@${aws_db_instance.main.address}:5432/ivr_${var.environment}"
}

resource "aws_ecs_task_definition" "app" {
  family                   = "${var.environment}-${var.project}"
  network_mode             = "awsvpc"
  requires_compatibilities = ["FARGATE"]
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.ecs_task.arn

  container_definitions = jsonencode([
    {
      name  = "${var.environment}-${var.project}"
      image = "${var.ecr_repo_url}:${var.image_tag}"
      portMappings = [{
        containerPort = var.app_port
        protocol      = "tcp"
      }]
      environment = [
        { name = "ENVIRONMENT",      value = var.environment },
        { name = "LLM_PROVIDER",     value = var.llm_provider },
        { name = "GROQ_MODEL",       value = var.groq_model },
        { name = "ROUTER_MODEL",     value = var.router_model },
        { name = "BEDROCK_MODEL",    value = var.bedrock_model },
        { name = "ROUTER_BEDROCK_MODEL", value = var.router_bedrock_model },
        { name = "AWS_REGION",       value = var.aws_region },
        { name = "REDIS_URL",        value = local.redis_url },
        { name = "DATABASE_URL",     value = local.db_url },
        { name = "APP_PORT",         value = tostring(var.app_port) },
        { name = "CNO_API_BASE_URL", value = "http://localhost:8001" },
      ]
      secrets = [
        {
          name      = "OPENAI_API_KEY"
          valueFrom = "${aws_secretsmanager_secret.app_secrets.arn}:OPENAI_API_KEY::"
        },
        {
          name      = "DEEPGRAM_API_KEY"
          valueFrom = "${aws_secretsmanager_secret.app_secrets.arn}:DEEPGRAM_API_KEY::"
        },
        {
          name      = "ELEVENLABS_API_KEY"
          valueFrom = "${aws_secretsmanager_secret.app_secrets.arn}:ELEVENLABS_API_KEY::"
        },
        {
          name      = "TWILIO_ACCOUNT_SID"
          valueFrom = "${aws_secretsmanager_secret.app_secrets.arn}:TWILIO_ACCOUNT_SID::"
        },
        {
          name      = "TWILIO_AUTH_TOKEN"
          valueFrom = "${aws_secretsmanager_secret.app_secrets.arn}:TWILIO_AUTH_TOKEN::"
        },
        {
          name      = "GROQ_API_KEY"
          valueFrom = "${aws_secretsmanager_secret.app_secrets.arn}:GROQ_API_KEY::"
        },
        {
          name      = "TWILIO_API_KEY"
          valueFrom = "${aws_secretsmanager_secret.app_secrets.arn}:TWILIO_API_KEY::"
        },
        {
          name      = "TWILIO_API_SECRET"
          valueFrom = "${aws_secretsmanager_secret.app_secrets.arn}:TWILIO_API_SECRET::"
        },
        {
          name      = "TWILIO_TWIML_APP_SID"
          valueFrom = "${aws_secretsmanager_secret.app_secrets.arn}:TWILIO_TWIML_APP_SID::"
        },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.app.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "ecs"
        }
      }
    },
    {
      name  = "${var.environment}-mock-api"
      image = "${var.ecr_repo_url}:mock-api"
      portMappings = [{
        containerPort = 8001
        protocol      = "tcp"
      }]
      essential = true
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.app.name
          "awslogs-region"        = var.aws_region
          "awslogs-stream-prefix" = "mock-api"
        }
      }
    },
  ])
}

# ── ECS Service ──────────────────────────────────────────────────────────────
resource "aws_ecs_service" "app" {
  name            = "${var.environment}-${var.project}-svc"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.app.arn
  desired_count   = 1
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.app.id]
    assign_public_ip = true # demo — Fargate in public subnet, no NAT Gateway
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.app.arn
    container_name   = "${var.environment}-${var.project}"
    container_port   = var.app_port
  }

  depends_on = [aws_lb_listener.http]
}

output "ecs_service_name" {
  value = aws_ecs_service.app.name
}

# ── ECS Auto-Scaling ─────────────────────────────────────────────────────────
resource "aws_appautoscaling_target" "ecs" {
  max_capacity       = var.max_tasks
  min_capacity       = var.min_tasks
  resource_id        = "service/${aws_ecs_cluster.main.name}/${aws_ecs_service.app.name}"
  scalable_dimension = "ecs:service:DesiredCount"
  service_namespace  = "ecs"
}

# Scale on CPU utilization
resource "aws_appautoscaling_policy" "cpu" {
  name               = "${var.environment}-${var.project}-cpu-scaling"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.ecs.resource_id
  scalable_dimension = aws_appautoscaling_target.ecs.scalable_dimension
  service_namespace  = aws_appautoscaling_target.ecs.service_namespace

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageCPUUtilization"
    }
    target_value       = var.cpu_scale_target
    scale_in_cooldown  = 120
    scale_out_cooldown = 60
  }
}

# Scale on memory utilization
resource "aws_appautoscaling_policy" "memory" {
  name               = "${var.environment}-${var.project}-memory-scaling"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.ecs.resource_id
  scalable_dimension = aws_appautoscaling_target.ecs.scalable_dimension
  service_namespace  = aws_appautoscaling_target.ecs.service_namespace

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ECSServiceAverageMemoryUtilization"
    }
    target_value       = var.memory_scale_target
    scale_in_cooldown  = 120
    scale_out_cooldown = 60
  }
}

# Scale on ALB request count per target (concurrent connections proxy)
resource "aws_appautoscaling_policy" "alb_requests" {
  name               = "${var.environment}-${var.project}-request-scaling"
  policy_type        = "TargetTrackingScaling"
  resource_id        = aws_appautoscaling_target.ecs.resource_id
  scalable_dimension = aws_appautoscaling_target.ecs.scalable_dimension
  service_namespace  = aws_appautoscaling_target.ecs.service_namespace

  target_tracking_scaling_policy_configuration {
    predefined_metric_specification {
      predefined_metric_type = "ALBRequestCountPerTarget"
      resource_label         = "${aws_lb.app.arn_suffix}/${aws_lb_target_group.app.arn_suffix}"
    }
    target_value       = 10   # scale out when >10 active requests per task
    scale_in_cooldown  = 120
    scale_out_cooldown = 60
  }
}
