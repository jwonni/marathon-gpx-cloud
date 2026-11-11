# 작업 큐 + 작업 이미지 저장소 (decision-log 30번)
#
# 업로드 → 이미지는 S3, 작업은 SQS 에 넣는다. 로컬 작업자와 클라우드(GPU EC2) 작업자가 같은 큐에서 꺼내 처리한다.
# GPU EC2 와 상태(state)를 나눈 이유: 큐는 계속 켜 두고, GPU 서버만 만들고 지우기 위해서

# 3번 실패한 작업이 옮겨지는 큐. 무한 재시도를 막고, 실패한 작업을 따로 확인할 수 있게 한다
resource "aws_sqs_queue" "jobs_dlq" {
  name                      = "marathon-jobs-dlq"
  message_retention_seconds = 1209600 # 14일 (최대값) — 원인을 볼 시간을 넉넉히
}

resource "aws_sqs_queue" "jobs" {
  name = "marathon-jobs"

  # 작업자가 꺼낸 뒤 이 시간 안에 삭제하지 않으면 다시 보이게 된다(= 다른 작업자가 재처리).
  # 로컬 CPU 는 지도 1장에 수 분이 걸리므로 넉넉히 30분
  visibility_timeout_seconds = 1800

  # 롱 폴링: 큐가 비어 있으면 최대 20초 기다렸다가 응답 → 빈 요청 수(=비용)를 줄인다
  receive_wait_time_seconds = 20

  message_retention_seconds = 345600 # 4일

  redrive_policy = jsonencode({
    deadLetterTargetArn = aws_sqs_queue.jobs_dlq.arn
    maxReceiveCount     = 3
  })
}

# 작업 입력 이미지와 결과(GPX) 저장소. 지도 이미지는 재배포하지 않으므로 공개 차단 + 7일 뒤 자동 삭제
resource "aws_s3_bucket" "jobs" {
  bucket        = "marathon-gpx-jobs-507138241931"
  force_destroy = true # 임시 작업 데이터라 destroy 때 객체가 남아 있어도 함께 지운다
}

resource "aws_s3_bucket_public_access_block" "jobs" {
  bucket                  = aws_s3_bucket.jobs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "jobs" {
  bucket = aws_s3_bucket.jobs.id

  rule {
    id     = "expire-jobs-7d"
    status = "Enabled"
    filter {}
    expiration {
      days = 7
    }
  }
}

output "queue_url" {
  value = aws_sqs_queue.jobs.url
}

output "dlq_url" {
  value = aws_sqs_queue.jobs_dlq.url
}

output "jobs_bucket" {
  value = aws_s3_bucket.jobs.bucket
}
