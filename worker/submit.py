"""작업 큐에 지도를 넣고, 큐 상태와 결과를 본다 (decision-log 31번).

  python worker/submit.py send <이미지> [반복 횟수]   # 이미지를 S3 에 올리고 작업을 큐에 넣는다
  python worker/submit.py status                     # 대기 / 처리 중 / 실패 큐 개수
  python worker/submit.py results [개수]             # 최근 결과 요약 (기본 20개)
  python worker/submit.py gpx <job_id> [저장 경로]    # 결과 GPX 다운로드

AWS 자격 증명은 프로필 marathon-local (작업 넣기·결과 읽기 권한만, infra/iam/marathon-queue-submit.json)
"""
import json
import os
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import boto3

QUEUE_URL = os.environ.get("QUEUE_URL", "https://sqs.ap-northeast-2.amazonaws.com/507138241931/marathon-jobs")
DLQ_URL = os.environ.get("DLQ_URL", "https://sqs.ap-northeast-2.amazonaws.com/507138241931/marathon-jobs-dlq")
BUCKET = os.environ.get("JOBS_BUCKET", "marathon-gpx-jobs-507138241931")

session = boto3.Session(profile_name=os.environ.get("AWS_PROFILE", "marathon-local"), region_name="ap-northeast-2")
sqs, s3 = session.client("sqs"), session.client("s3")


def send(image, repeat="1"):
    img = Path(image)
    raw = img.read_bytes()
    for _ in range(int(repeat)):
        job_id = datetime.now().strftime("%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        key = f"inputs/{job_id}{img.suffix.lower()}"
        s3.put_object(Bucket=BUCKET, Key=key, Body=raw)
        sqs.send_message(QueueUrl=QUEUE_URL, MessageBody=json.dumps({
            "job_id": job_id, "image_key": key, "filename": img.name,
            "submitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "submitted_ts": time.time(),
        }))
        print(f"넣음 {job_id}  ({img.name})")


def counts(url):
    a = sqs.get_queue_attributes(QueueUrl=url, AttributeNames=[
        "ApproximateNumberOfMessages", "ApproximateNumberOfMessagesNotVisible"])["Attributes"]
    return int(a["ApproximateNumberOfMessages"]), int(a["ApproximateNumberOfMessagesNotVisible"])


def status():
    waiting, in_flight = counts(QUEUE_URL)
    dead, _ = counts(DLQ_URL)
    print(f"대기 {waiting}  처리 중 {in_flight}  실패 큐 {dead}   (SQS 개수는 근사값, 수 초 늦게 반영)")


def results(n="20"):
    objs = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix="results/"):
        objs += [o for o in page.get("Contents", []) if o["Key"].endswith("result.json")]
    objs = sorted(objs, key=lambda o: o["LastModified"])[-int(n):]
    print(f"{'job_id':<22} {'작업자':<8} {'상태':<8} {'대기s':>6} {'처리s':>6} {'앵커':>4} {'오차px':>6}  오류")
    for o in objs:
        r = json.loads(s3.get_object(Bucket=BUCKET, Key=o["Key"])["Body"].read())
        print(f"{r['job_id']:<22} {r['worker']:<8} {r['status']:<8} {r['queue_wait_s']:>6} {r['process_s']:>6} "
              f"{r.get('num_anchors', '-'):>4} {r.get('mean_reprojection_error_px', '-'):>6}  {r.get('error', '')[:60]}")


def gpx(job_id, out=None):
    out = Path(out or f"{job_id}.gpx")
    out.write_bytes(s3.get_object(Bucket=BUCKET, Key=f"results/{job_id}/route.gpx")["Body"].read())
    print(f"저장 {out}")


if __name__ == "__main__":
    cmds = {"send": send, "status": status, "results": results, "gpx": gpx}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        sys.exit(__doc__)
    cmds[sys.argv[1]](*sys.argv[2:])
