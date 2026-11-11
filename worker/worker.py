"""작업 큐(SQS)에서 지도 작업을 꺼내 같은 서버의 앱 API 로 처리하고, 결과를 S3 에 올린다 (decision-log 30·31번).

로컬과 클라우드(GPU EC2)가 같은 코드로 같은 큐를 나눠 처리한다. 누가 처리할지는 큐가 정한다(먼저 꺼낸 쪽).

환경 변수
  APP_URL      같은 서버의 앱 주소 (compose 안에서는 http://app:8010)
  QUEUE_URL    작업 큐
  JOBS_BUCKET  입력 이미지·결과 버킷
  WORKER_NAME  결과에 남길 작업자 이름 (local / ec2 등) — 버스팅 때 누가 처리했는지 비교용

실패 처리
  - 앱이 4xx: 지도 자체 문제(경로·앵커 부족 등). 다시 해도 같으므로 실패로 기록하고 메시지 삭제
  - 앱이 5xx·연결 오류: 일시적일 수 있음(Hi-SAM 무작위 실패 등). 30초 뒤 다시 보이게 해 재시도,
    3번째 수신에서도 실패하면 실패로 기록 → SQS 가 다음 수신 때 실패 큐(DLQ)로 옮긴다
"""
import base64
import json
import logging
import os
import signal
import socket
import time
from datetime import datetime, timezone

import boto3
import requests

APP = os.environ.get("APP_URL", "http://localhost:8010")
QUEUE_URL = os.environ.get("QUEUE_URL", "https://sqs.ap-northeast-2.amazonaws.com/507138241931/marathon-jobs")
BUCKET = os.environ.get("JOBS_BUCKET", "marathon-gpx-jobs-507138241931")
WORKER = os.environ.get("WORKER_NAME", socket.gethostname())
MAX_RECEIVES = 3            # 큐의 redrive maxReceiveCount 와 같아야 한다 (infra/envs/dev/queue)
RETRY_DELAY_S = 30

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("worker")

sqs = boto3.client("sqs", region_name="ap-northeast-2")
s3 = boto3.client("s3", region_name="ap-northeast-2")

# 종료 신호(docker stop, 축소 시 인스턴스 종료)를 받으면 지금 작업까지만 끝내고 멈춘다.
# 처리 중에 끊기면 그 작업은 숨김 시간(30분)이 지나야 다른 작업자에게 넘어가기 때문
stopping = False


def _stop(signum, _frame):
    global stopping
    stopping = True
    log.info("종료 신호(%s) — 현재 작업을 마치고 멈춤", signum)


signal.signal(signal.SIGTERM, _stop)
signal.signal(signal.SIGINT, _stop)


class PermanentError(Exception):
    """다시 시도해도 결과가 같은 실패 (앱 4xx)"""


def now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def call(path, times, **kw):
    t = time.perf_counter()
    r = requests.post(APP + path, timeout=900, **kw)
    times[path.rsplit("/", 1)[-1]] = round(time.perf_counter() - t, 2)
    if r.status_code >= 500:
        raise RuntimeError(f"{path} HTTP {r.status_code}: {r.text[:200]}")
    if r.status_code >= 400:
        raise PermanentError(f"{path} HTTP {r.status_code}: {r.text[:200]}")
    return r.json()


def run_pipeline(raw: bytes, filename: str, times: dict):
    """브라우저와 같은 순서: 경로 분할 → 후처리 → 경로 추출 → OCR → 지리좌표 → GPX"""
    files = lambda: {"file": (filename, raw, "application/octet-stream")}  # noqa: E731
    data_url = "data:image/png;base64," + base64.b64encode(raw).decode()

    d = call("/api/predict", times, files=files())
    d = call("/api/postprocess", times, json={"mask_b64": d["mask_b64"]})
    p = call("/api/auto_extract_path", times, json={"skeleton_b64": d["skeleton_b64"], "input_img_b64": data_url})
    ocr = call("/api/run_ocr", times, files=files())
    geo = call("/api/run_georeference", times, files=files(),
               data={"ocr_results_json": json.dumps(ocr.get("ocr_results", []), ensure_ascii=False)})
    gpx = call("/api/convert_gpx", times, json={
        "start": p["start"], "end": p["end"], "path": p["path"],
        "homography_params": geo["homography_params"], "category_pixels": ocr.get("category_pixels")})
    return geo, gpx["gpx_content"]


def put_result(job_id, result, gpx=None):
    prefix = f"results/{job_id}/"
    s3.put_object(Bucket=BUCKET, Key=prefix + "result.json", ContentType="application/json",
                  Body=json.dumps(result, ensure_ascii=False, indent=1).encode())
    if gpx:
        s3.put_object(Bucket=BUCKET, Key=prefix + "route.gpx", ContentType="application/gpx+xml", Body=gpx.encode())


def handle(msg):
    job = json.loads(msg["Body"])
    job_id = job["job_id"]
    receives = int(msg["Attributes"]["ApproximateReceiveCount"])
    started = time.time()
    result = {"job_id": job_id, "worker": WORKER, "attempt": receives,
              "submitted_at": job["submitted_at"], "started_at": now(),
              "queue_wait_s": round(started - job["submitted_ts"], 1)}
    times = {}
    log.info("작업 %s 시작 (시도 %d, 대기 %.0fs)", job_id, receives, result["queue_wait_s"])

    try:
        raw = s3.get_object(Bucket=BUCKET, Key=job["image_key"])["Body"].read()
        geo, gpx = run_pipeline(raw, job["filename"], times)
        result.update(status="success", num_anchors=geo["num_anchors"],
                      mean_reprojection_error_px=geo["mean_reprojection_error_px"])
        delete = True
    except PermanentError as e:
        result.update(status="failed", error=str(e), retryable=False)
        gpx, delete = None, True
    except Exception as e:                              # 일시적일 수 있는 실패
        result.update(status="failed", error=f"{type(e).__name__}: {e}", retryable=True)
        gpx, delete = None, False

    result.update(finished_at=now(), process_s=round(time.time() - started, 1), times_s=times)

    if delete:
        put_result(job_id, result, gpx)
        sqs.delete_message(QueueUrl=QUEUE_URL, ReceiptHandle=msg["ReceiptHandle"])
        log.info("작업 %s %s (%.0fs)", job_id, result["status"], result["process_s"])
    elif receives >= MAX_RECEIVES:
        put_result(job_id, result)                      # 메시지는 남겨 두면 SQS 가 실패 큐로 옮긴다
        log.warning("작업 %s %d회 실패 → 실패 큐로 이동 예정: %s", job_id, receives, result["error"])
    else:
        sqs.change_message_visibility(QueueUrl=QUEUE_URL, ReceiptHandle=msg["ReceiptHandle"],
                                      VisibilityTimeout=RETRY_DELAY_S)
        log.warning("작업 %s 실패(%d/%d), %ds 뒤 재시도: %s", job_id, receives, MAX_RECEIVES,
                    RETRY_DELAY_S, result["error"])


def app_ready():
    try:
        return requests.get(APP + "/api/health", timeout=3).ok
    except requests.RequestException:
        return False


def main():
    log.info("작업자 %s 시작 — 앱 %s, 큐 %s", WORKER, APP, QUEUE_URL)
    while not stopping:
        # 앱이 준비되지 않았으면 작업을 꺼내지 않는다 (꺼냈다가 실패하면 재시도 횟수만 소모)
        if not app_ready():
            log.info("앱 준비 대기")
            time.sleep(5)
            continue
        resp = sqs.receive_message(QueueUrl=QUEUE_URL, MaxNumberOfMessages=1, WaitTimeSeconds=20,
                                   MessageSystemAttributeNames=["ApproximateReceiveCount"])
        for msg in resp.get("Messages", []):
            handle(msg)
    log.info("작업자 종료")


if __name__ == "__main__":
    main()
