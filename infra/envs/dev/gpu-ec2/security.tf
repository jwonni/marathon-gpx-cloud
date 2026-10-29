# 계정에 기본으로 있는 VPC 를 사용 (손으로 만들 때와 같은 네트워크)
data "aws_vpc" "default" {
  default = true
}

# 손으로 만든 marathon-sg 와 이름이 겹치지 않게 별도 이름 사용
resource "aws_security_group" "gpu" {
  name        = "marathon-tf-sg"
  description = "GPU EC2: SSH and app from my IP only"
  vpc_id      = data.aws_vpc.default.id
}

# 인바운드: 여기 적은 규칙만 존재한다. 콘솔에서 누가 규칙을 추가하면 다음 plan 에서 차이로 드러난다
resource "aws_vpc_security_group_ingress_rule" "ssh" {
  security_group_id = aws_security_group.gpu.id
  description       = "SSH"
  ip_protocol       = "tcp"
  from_port         = 22
  to_port           = 22
  cidr_ipv4         = var.my_ip_cidr
}

resource "aws_vpc_security_group_ingress_rule" "app" {
  security_group_id = aws_security_group.gpu.id
  description       = "marathon app (FastAPI)"
  ip_protocol       = "tcp"
  from_port         = 8010
  to_port           = 8010
  cidr_ipv4         = var.my_ip_cidr
}

# 아웃바운드: Terraform 으로 만든 보안 그룹은 기본 "전체 허용" 규칙이 없으므로 직접 적어야 한다.
# 없으면 S3 가중치 다운로드·pip·docker pull 이 전부 막힌다
resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.gpu.id
  description       = "all outbound (S3, pip, docker pull)"
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}
