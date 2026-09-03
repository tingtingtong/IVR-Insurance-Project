variable "environment" {
  description = "Environment name (dev or prod)"
  type        = string
  default     = "dev"
  validation {
    condition     = contains(["dev", "prod"], var.environment)
    error_message = "Environment must be 'dev' or 'prod'."
  }
}

variable "aws_region" {
  description = "AWS region"
  type        = string
  default     = "us-east-1"
}

variable "image_tag" {
  description = "Docker image tag to deploy"
  type        = string
  default     = "latest"
}

variable "project" {
  description = "Project name prefix for all resources"
  type        = string
  default     = "ivr"
}

variable "vpc_cidr" {
  description = "VPC CIDR block"
  type        = string
  default     = "10.0.0.0/16"
}

variable "db_username" {
  description = "RDS master username"
  type        = string
  default     = "ivradmin"
  sensitive   = true
}

variable "db_password" {
  description = "RDS master password"
  type        = string
  sensitive   = true
}

variable "llm_provider" {
  description = "LLM provider: groq or bedrock"
  type        = string
  default     = "groq"
}

variable "groq_model" {
  description = "Groq model for service nodes"
  type        = string
  default     = "qwen/qwen3.8-27b"
}

variable "router_model" {
  description = "Groq model for intent router"
  type        = string
  default     = "qwen/qwen3.8-27b"
}

variable "bedrock_model" {
  description = "Bedrock model for service nodes (when llm_provider=bedrock)"
  type        = string
  default     = "amazon.nova-micro-v1:0"
}

variable "router_bedrock_model" {
  description = "Bedrock model for intent router (when llm_provider=bedrock)"
  type        = string
  default     = "amazon.nova-micro-v1:0"
}

variable "app_port" {
  description = "Container application port"
  type        = number
  default     = 8000
}

variable "task_cpu" {
  description = "ECS task CPU (in CPU units: 256 = 0.25 vCPU)"
  type        = number
  default     = 512
}

variable "task_memory" {
  description = "ECS task memory (in MiB)"
  type        = number
  default     = 1024
}

# ── Auto-scaling ──────────────────────────────────────────────────────────────
variable "min_tasks" {
  description = "Minimum number of ECS tasks"
  type        = number
  default     = 1
}

variable "max_tasks" {
  description = "Maximum number of ECS tasks"
  type        = number
  default     = 8
}

variable "cpu_scale_target" {
  description = "Target CPU utilization (%) for auto-scaling"
  type        = number
  default     = 60
}

variable "memory_scale_target" {
  description = "Target memory utilization (%) for auto-scaling"
  type        = number
  default     = 70
}

# ── Instance sizes (configurable per environment) ─────────────────────────────
variable "db_instance_class" {
  description = "RDS instance class (db.t4g.micro for dev, db.t4g.small+ for prod)"
  type        = string
  default     = "db.t4g.micro"
}

variable "redis_node_type" {
  description = "ElastiCache node type (cache.t4g.micro for dev, cache.t4g.small+ for prod)"
  type        = string
  default     = "cache.t4g.micro"
}
