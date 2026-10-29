#!/bin/bash
# EC2(GPU) 에서 실제 크롭으로 PaddleOCR GPU vs CPU 비교.
# 사용: /home/ec2-user/work 에 crops/, prepared.json, ocr_profile.py 를 올린 뒤 `bash run_gpu_ocr_bench.sh`
set -uo pipefail
IMAGE=507138241931.dkr.ecr.ap-northeast-2.amazonaws.com/marathon-gpx:v1

sudo docker run --rm --gpus all \
  -v /home/ec2-user/work:/work \
  -v paddlex-cache:/root/.paddlex \
  "$IMAGE" bash -c '
    pip uninstall -y -q paddlepaddle torch torchvision >/dev/null 2>&1
    echo "INSTALL_START $(date +%T)"
    pip install -q --root-user-action=ignore --retries 10 --timeout 60 \
      paddlepaddle-gpu==3.2.2 -i https://www.paddlepaddle.org.cn/packages/stable/cu126/ >/dev/null 2>&1
    echo "INSTALL_DONE  $(date +%T)"
    python -c "import paddle; print(\"paddle\", paddle.__version__, \"cuda\", paddle.is_compiled_with_cuda())"
    for dev in gpu cpu; do
      echo "RUN_START $dev $(date +%T)"
      STUB_TORCH=1 DEVICE=$dev python /work/ocr_profile.py ocronly 2>&1 \
        | grep -E "DEVICE=|ms/call|OCR|Error|error" || true
    done
  '
