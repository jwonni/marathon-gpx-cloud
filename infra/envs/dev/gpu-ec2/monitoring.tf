# 온프레미스 Prometheus 의 수집 대상 파일을 EC2 와 함께 만들고 지운다 (decision-log 34번)
#
# apply → monitoring/targets/aws-<job>.json 생성 → Prometheus 가 30초 안에 읽어 EC2 수집 시작
# destroy → 파일 삭제 → 수집 대상에서 빠짐 (지운 서버 때문에 ServiceDown 알람이 울리지 않음)
# EC2 를 만들 때마다 공인 IP 가 바뀌므로 손으로 고치지 않기 위함

locals {
  scrape_ports = {
    app = 8010 # FastAPI /metrics
    ocr = 8020 # OCR 서비스 /metrics
    gpu = 9400 # DCGM exporter
  }
}

resource "local_file" "prometheus_targets" {
  for_each = local.scrape_ports

  filename = "${path.module}/../../../../monitoring/targets/aws-${each.key}.json"
  content = jsonencode([{
    targets = ["${aws_instance.gpu.public_ip}:${each.value}"]
    labels  = { site = "aws", instance_id = aws_instance.gpu.id }
  }])
}
