# ── CloudFront Distribution (HTTPS termination for ALB) ──────────────────────
# Provides HTTPS via *.cloudfront.net without needing a custom domain or ACM cert.
# Required for browser softphone (getUserMedia needs secure context).

resource "aws_cloudfront_distribution" "app" {
  enabled         = true
  comment         = "${var.environment}-${var.project} HTTPS frontend"
  price_class     = "PriceClass_100" # US + Europe only (cheapest)
  http_version    = "http2and3"
  is_ipv6_enabled = true

  origin {
    domain_name = aws_lb.app.dns_name
    origin_id   = "alb"

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "http-only" # ALB is HTTP-only
      origin_ssl_protocols   = ["TLSv1.2"]
    }
  }

  default_cache_behavior {
    allowed_methods        = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
    cached_methods         = ["GET", "HEAD"]
    target_origin_id       = "alb"
    viewer_protocol_policy = "redirect-to-https"

    # Forward everything — no caching (real-time app with WebSockets)
    cache_policy_id          = "4135ea2d-6df8-44a3-9df3-4b5a84be39ad" # CachingDisabled
    origin_request_policy_id = "216adef6-5c7f-47e4-b989-5492eafa07d3" # AllViewer
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true # uses *.cloudfront.net cert
  }

  tags = { Name = "${var.environment}-${var.project}-cdn" }
}

output "cloudfront_url" {
  value       = "https://${aws_cloudfront_distribution.app.domain_name}"
  description = "HTTPS URL for the application (browser softphone)"
}
