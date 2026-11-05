"""장애를 일부러 일으켜 알람이 실제로 울리는지, 몇 초 걸리는지 잰다 (로컬 docker compose + 모니터링 스택).

  python bench/alert_test.py

시나리오
  1. OCR 컨테이너 정지 → ServiceDown(job=ocr) firing 까지 시간 → 재시작 → 해제까지 시간
  2. 품질 기준 밖 결과 주입 → /api/run_georeference 는 200 인데 GeorefQualityLow 가 firing 되는지
     (입력: Hi-SAM 없이 만든 순천 OCR 결과 — decision-log 28번에서 앵커 6개·오차 19px 로 무너졌던 것)
"""
import json
import subprocess
import time
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
APP, PROM, AM = "http://localhost:8010", "http://localhost:9090", "http://localhost:9093"
COMPOSE = ["docker", "compose", "-f", "docker-compose.yml", "-f", "docker-compose.dev.yml", "-f", "docker-compose.monitoring.yml"]


def compose(*args):
    subprocess.run([*COMPOSE, *args], cwd=ROOT, capture_output=True)


def alert_state(name, **labels):
    for a in requests.get(f"{PROM}/api/v1/alerts", timeout=5).json()["data"]["alerts"]:
        if a["labels"].get("alertname") == name and all(a["labels"].get(k) == v for k, v in labels.items()):
            return a["state"]                     # pending / firing
    return "inactive"


def in_alertmanager(name):
    return any(a["labels"].get("alertname") == name for a in requests.get(f"{AM}/api/v2/alerts", timeout=5).json())


def wait_for(cond, limit):
    t0 = time.monotonic()
    while time.monotonic() - t0 < limit:
        if cond():
            return time.monotonic() - t0
        time.sleep(2)
    return None


def fmt(sec):
    return f"{sec:.0f}s" if sec is not None else "시간 초과"


print("[1] OCR 컨테이너 정지 → ServiceDown")
compose("stop", "ocr")
t_pending = wait_for(lambda: alert_state("ServiceDown", job="ocr") in ("pending", "firing"), 120)
t_firing = wait_for(lambda: alert_state("ServiceDown", job="ocr") == "firing", 180)
t_am = wait_for(lambda: in_alertmanager("ServiceDown"), 60)
print(f"    pending 까지 {fmt(t_pending)}, firing 까지 {fmt(t_firing and t_pending and t_firing + t_pending)} (규칙의 for: 1m 포함), Alertmanager 도착 +{fmt(t_am)}")
compose("start", "ocr")
t_resolved = wait_for(lambda: alert_state("ServiceDown", job="ocr") == "inactive", 300)
print(f"    재시작 후 해제까지 {fmt(t_resolved)} (OCR 모델 로드 시간 포함)")

print("[2] 품질 기준 밖 결과 주입 → GeorefQualityLow")
ocr = (ROOT / "bench/e2e/paddle_only/suncheon_ocr.json").read_text(encoding="utf-8")
with open(ROOT / "bench/e2e/paddle_only/suncheon.png", "rb") as f:
    r = requests.post(f"{APP}/api/run_georeference", files={"file": ("image.png", f, "image/png")},
                      data={"ocr_results_json": ocr}, timeout=120)
body = r.json()
print(f"    API 응답: HTTP {r.status_code}, status={body.get('status')}, 앵커 {body.get('num_anchors')}개, "
      f"평균 오차 {body.get('mean_reprojection_error_px')}px")
t_silent = wait_for(lambda: alert_state("GeorefQualityLow") == "firing", 120)
reasons = sorted({a["labels"].get("reason") for a in requests.get(f"{PROM}/api/v1/alerts", timeout=5).json()["data"]["alerts"]
                  if a["labels"].get("alertname") == "GeorefQualityLow"})
print(f"    GeorefQualityLow firing 까지 {fmt(t_silent)}, 원인 {reasons}")
