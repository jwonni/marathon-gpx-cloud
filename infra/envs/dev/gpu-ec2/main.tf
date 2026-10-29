# GPU 드라이버·Docker 가 미리 설치된 AMI 를 이름으로 찾는다.
# 콘솔에서 고를 때 일반 Amazon Linux 를 잘못 골라 드라이버가 없던 실수(2026-10-28)를 막기 위함.
data "aws_ami" "gpu" {
  owners      = ["amazon"]
  most_recent = true

  filter {
    name   = "name"
    values = ["Deep Learning Base OSS Nvidia Driver GPU AMI (Amazon Linux 2023) *"]
  }

  filter {
    name   = "architecture"
    values = ["x86_64"]
  }
}

output "gpu_ami" {
  value = "${data.aws_ami.gpu.id} — ${data.aws_ami.gpu.name}"
}
