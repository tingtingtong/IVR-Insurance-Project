# Shared RDS instance — dev and prod use separate databases on the same instance
resource "aws_db_subnet_group" "main" {
  name       = "${var.project}-db-subnets"
  subnet_ids = aws_subnet.public[*].id
  tags       = { Name = "${var.project}-db-subnets" }
}

resource "aws_db_instance" "main" {
  identifier     = "${var.project}-postgres"
  engine         = "postgres"
  engine_version = "16.4"
  instance_class = var.db_instance_class

  allocated_storage = 20
  storage_type      = "gp3"

  db_name  = "ivr_${var.environment}"
  username = var.db_username
  password = var.db_password

  db_subnet_group_name   = aws_db_subnet_group.main.name
  vpc_security_group_ids = [aws_security_group.rds.id]

  publicly_accessible = false
  skip_final_snapshot  = true # demo — no snapshot on destroy
  deletion_protection  = false

  tags = { Name = "${var.project}-postgres" }
}

output "rds_endpoint" {
  value = aws_db_instance.main.endpoint
}
