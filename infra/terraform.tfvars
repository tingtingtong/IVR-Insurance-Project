environment = "dev"
aws_region  = "us-east-1"
project     = "ivr"
image_tag   = "latest"
db_username = "ivradmin"
# db_password — set via TF_VAR_db_password or -var="db_password=..."

# ── Scaling profile: dev (low-cost) ──────────────────────────────────────────
# Current: 1 task, micro instances — handles ~5-10 concurrent calls
task_cpu          = 512    # 0.5 vCPU
task_memory       = 1024   # 1 GB
min_tasks         = 1
max_tasks         = 2
db_instance_class = "db.t4g.micro"
redis_node_type   = "cache.t4g.micro"

# ── Scaling profile: prod (3,000 calls/day, 50-80 parallel) ──────────────────
# Use infra/prod.tfvars — do not uncomment here (this file is the cheap dev profile).
#   terraform apply -var-file=prod.tfvars
# task_cpu=2048  task_memory=4096  min_tasks=4  max_tasks=8
# db.t4g.small + cache.t4g.small
