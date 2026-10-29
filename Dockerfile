# torch 의 PyPI 휠이 CUDA 런타임을 자체 포함하므로 CUDA 베이스 이미지가 필요 없다.
# 호스트에 NVIDIA 드라이버가 있으면 GPU, 없으면 CPU 로 동작한다.
FROM python:3.12-slim

# opencv(libGL, glib), paddle(OpenMP) 이 요구하는 시스템 라이브러리
RUN apt-get update \
 && apt-get install -y --no-install-recommends libgl1 libglib2.0-0 libgomp1 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 레이어는 "잘 안 바뀌는 것 → 자주 바뀌는 것" 순서로 쌓는다.
# --mount=type=cache : 레이어가 무효화돼도 이미 받은 패키지는 다시 받지 않는다 (이미지에는 안 들어감).

# 1) torch + CUDA 라이브러리 — 수 GB, 거의 안 바뀜
COPY requirements-torch.txt .
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements-torch.txt

# 2) 나머지 의존성 — requirements.txt 를 고쳐도 1) 레이어는 그대로 재사용된다
COPY requirements.txt .
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt

# 3) 코드 — 가장 자주 바뀜
COPY . .

EXPOSE 8010
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8010"]
