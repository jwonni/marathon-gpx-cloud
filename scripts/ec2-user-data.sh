#!/bin/bash
# EC2 시작 시 1회 자동 실행되는 스크립트 (사용자 데이터).
# 셸 접속 없이도 컨테이너가 뜨도록, 가중치 다운로드 → 이미지 pull → 실행까지 수행한다.
# 출력은 /var/log/user-data.log 와 콘솔 시스템 로그에 남는다.
#
# 사용법: EC2 시작 화면 "고급 세부 정보 → 사용자 데이터"에 이 파일 내용을 붙여넣고
#         KAKAO_API_KEY 값만 실제 키로 바꾼다.
#
# 전제: 루트 볼륨 100GiB 이상.
#   AMI 기본 크기로는 이미지 압축 해제(8GB 이상) 중 "no space left on device" 로 실패한다.
#   (2026-09-16 실제 실패. docs/decision-log.md 14번 참고)
set -xuo pipefail
exec > >(tee -a /var/log/user-data.log) 2>&1

REGION=ap-northeast-2
ACCOUNT=507138241931
BUCKET=marathon-gpx-artifacts-507138241931
IMAGE=$ACCOUNT.dkr.ecr.$REGION.amazonaws.com/marathon-gpx:v1

ts() { echo "=== [$(date +%H:%M:%S)] $* ==="; }

# 부팅 직후에는 Docker 데몬이 아직 안 떴을 수 있으므로 최대 150초 대기
ts "docker 기동 대기"
for _ in $(seq 1 30); do docker info >/dev/null 2>&1 && break; sleep 5; done

ts "GPU 확인"
nvidia-smi || echo "!!! nvidia-smi 실패 — 드라이버 문제"

ts "가중치 다운로드 시작"
mkdir -p /opt/marathon/weights /opt/marathon/hisam
aws s3 cp "s3://$BUCKET/models/weights/" /opt/marathon/weights/ --recursive --region "$REGION"
aws s3 cp "s3://$BUCKET/models/hisam/"   /opt/marathon/hisam/   --recursive --region "$REGION"
ts "가중치 다운로드 완료"
du -sh /opt/marathon/weights /opt/marathon/hisam

# 임시 방편: 키가 인스턴스 메타데이터에 남는다. 이후 단계에서 SSM Parameter Store 로 옮길 것
echo 'KAKAO_API_KEY=여기에_카카오_키를_넣으세요' > /opt/marathon/.env
chmod 600 /opt/marathon/.env

ts "ECR 로그인 및 이미지 pull 시작"
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "$ACCOUNT.dkr.ecr.$REGION.amazonaws.com"
docker pull "$IMAGE"
ts "이미지 pull 완료"

ts "컨테이너 실행"
docker run -d --name marathon --restart unless-stopped --gpus all -p 8010:8010 \
  --env-file /opt/marathon/.env \
  -v /opt/marathon/weights:/app/weights \
  -v /opt/marathon/hisam:/app/Hi-SAM/pretrained_checkpoint \
  -v paddlex-cache:/root/.paddlex \
  "$IMAGE"

ts "완료 — 컨테이너 상태"
docker ps
