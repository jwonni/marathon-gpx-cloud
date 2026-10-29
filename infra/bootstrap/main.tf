# Terraform state 를 보관할 S3 버킷.
# 이 폴더만 예외적으로 state 를 로컬에 둔다 (버킷이 생기기 전에는 S3 에 저장할 수 없으므로).
terraform {
  required_version = ">= 1.10"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = "ap-northeast-2"
  default_tags {
    tags = {
      Project   = "marathon-gpx"
      ManagedBy = "terraform"
    }
  }
}

resource "aws_s3_bucket" "tfstate" {
  bucket = "marathon-gpx-tfstate-507138241931" # 버킷 이름은 전 세계에서 유일해야 해서 계정 ID 를 붙임

  # 실수로 destroy 해도 state 를 잃지 않도록 삭제를 막는다
  lifecycle {
    prevent_destroy = true
  }
}

# 버전 관리: state 가 망가져도 이전 버전으로 되돌릴 수 있다
resource "aws_s3_bucket_versioning" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  versioning_configuration {
    status = "Enabled"
  }
}

# 암호화: S3 기본 키(SSE-S3). 무료. KMS 키는 월 $1 이라 쓰지 않음
resource "aws_s3_bucket_server_side_encryption_configuration" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# 외부 공개 차단: state 에는 리소스 ID·IP 등 인프라 정보가 다 들어 있다
resource "aws_s3_bucket_public_access_block" "tfstate" {
  bucket                  = aws_s3_bucket.tfstate.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# 이전 버전은 90일 뒤 자동 삭제 (계속 쌓이지 않게)
resource "aws_s3_bucket_lifecycle_configuration" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  rule {
    id     = "expire-old-state-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 90
    }
  }
}

output "state_bucket" {
  value = aws_s3_bucket.tfstate.bucket
}
