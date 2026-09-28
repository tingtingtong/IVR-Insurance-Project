# Apply with: terraform apply -var-file=prod.tfvars
# Sized for ~3,000 calls/day and 50-80 concurrent Media Streams
# (~20 streams per 2 vCPU / 4 GB task → 4 tasks).

environment = "prod"
aws_region  = "us-east-1"
project     = "ivr"
image_tag   = "latest"
db_username = "ivradmin"

task_cpu          = 2048
task_memory       = 4096
min_tasks         = 4
max_tasks         = 8
cpu_scale_target  = 60
memory_scale_target = 70
db_instance_class = "db.t4g.small"
redis_node_type   = "cache.t4g.small"

# Set these before apply:
# allowed_origins      = "https://ivr.example.com"
# twilio_base_url      = "https://ivr.example.com"
# acm_certificate_arn  = "arn:aws:acm:us-east-1:...:certificate/..."
# cno_api_base_url     = "https://api.example.com"
