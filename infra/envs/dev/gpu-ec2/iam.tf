# EC2 가 "빌려 쓰는" 역할. 서버에 액세스 키를 넣지 않기 위해 사용
# 이름은 marathon- 으로 시작해야 한다 (Terraform 실행 권한이 marathon-* 역할만 허용, decision-log 19번)
resource "aws_iam_role" "gpu" {
  name = "marathon-tf-ec2-role"

  # 신뢰 정책: 이 역할을 빌릴 수 있는 주체 = EC2 서비스만
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

# 권한: S3 의 모델 가중치(models/*) 읽기만. 정책 자체는 관리자가 미리 만든 것을 참조만 한다
resource "aws_iam_role_policy_attachment" "models_read" {
  role       = aws_iam_role.gpu.name
  policy_arn = "arn:aws:iam::507138241931:policy/marathon-s3-models-read"
}

# 역할을 EC2 에 붙이기 위한 연결 고리
resource "aws_iam_instance_profile" "gpu" {
  name = "marathon-tf-ec2-profile"
  role = aws_iam_role.gpu.name
}
