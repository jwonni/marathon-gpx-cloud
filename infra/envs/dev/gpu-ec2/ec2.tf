# GPU EC2 한 대. 손으로 만들 때 놓쳤던 값(AMI, 디스크 100GiB, IAM 역할)을 전부 코드로 고정한다
resource "aws_instance" "gpu" {
  ami                    = data.aws_ami.gpu.id
  instance_type          = "g4dn.xlarge"
  key_name               = "marathon-key"
  vpc_security_group_ids = [aws_security_group.gpu.id]
  iam_instance_profile   = aws_iam_instance_profile.gpu.name

  # 기본 크기로는 이미지 압축 해제 중 "no space left on device" (decision-log 14번)
  root_block_device {
    volume_size           = 100
    volume_type           = "gp3"
    delete_on_termination = true # destroy 시 디스크도 함께 삭제 → 볼륨 비용이 남지 않음
  }

  # 인스턴스 메타데이터는 토큰 방식(IMDSv2)만 허용 → 웹 취약점(SSRF)으로 역할 자격증명이 새는 경로 차단
  metadata_options {
    http_tokens = "required"
  }

  tags = {
    Name = "marathon-gpu"
  }

  # AMI 가 새로 나올 때마다 서버가 교체되지 않게 한다 (최신 AMI 는 다음 생성 때 반영)
  lifecycle {
    ignore_changes = [ami]
  }
}

output "public_ip" {
  value = aws_instance.gpu.public_ip
}

output "ssh" {
  value = "ssh -i C:\\Users\\roffj\\Downloads\\marathon-key.pem ec2-user@${aws_instance.gpu.public_ip}"
}
