# Terraform 과 AWS provider 버전 고정
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

  # 이 코드로 만든 리소스에 자동으로 붙는 태그 → 콘솔에서 "Terraform 이 만든 것"을 구분
  default_tags {
    tags = {
      Project   = "marathon-gpx"
      ManagedBy = "terraform"
    }
  }
}
