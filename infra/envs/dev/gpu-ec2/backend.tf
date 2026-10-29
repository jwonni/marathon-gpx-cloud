# state 를 로컬 파일 대신 S3 에 저장 (버킷은 infra/bootstrap 에서 생성)
terraform {
  backend "s3" {
    bucket       = "marathon-gpx-tfstate-507138241931"
    key          = "dev/gpu-ec2/terraform.tfstate" # 프로젝트마다 경로를 나눠 한 버킷을 같이 쓴다
    region       = "ap-northeast-2"
    encrypt      = true
    use_lockfile = true # S3 네이티브 락 (Terraform 1.10+). DynamoDB 불필요 — decision-log 6번
  }
}
