# 작업자: 모델 없이 큐 ↔ 앱 API ↔ S3 만 잇는 가벼운 컨테이너. 로컬·EC2 에서 같은 이미지를 쓴다
FROM python:3.12-slim
RUN pip install --no-cache-dir "boto3>=1.35" "requests>=2.31"
WORKDIR /app
COPY worker.py .
# print/log 가 버퍼에 쌓이지 않고 docker logs 에 바로 보이게
ENV PYTHONUNBUFFERED=1
CMD ["python", "worker.py"]
