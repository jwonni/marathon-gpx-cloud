"""marathon.json(Grafana 대시보드)을 생성한다. 패널을 바꿀 때는 이 파일을 고치고 다시 실행한다.

  python monitoring/grafana/build_dashboard.py
"""
import json
from pathlib import Path

DS = {"type": "prometheus", "uid": "prometheus"}
W = "[$__range]"          # 트래픽이 드문 서비스라 rate 대신 화면 시간 범위 전체의 증가량으로 계산


def target(expr, legend=""):
    return {"datasource": DS, "expr": expr, "legendFormat": legend, "refId": "A"}


def panel(pid, title, kind, x, y, w, h, targets, unit=None, desc="", thresholds=None, decimals=None):
    p = {
        "id": pid, "type": kind, "title": title, "description": desc, "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": [dict(t, refId=chr(65 + i)) for i, t in enumerate(targets)],
        "fieldConfig": {"defaults": {}, "overrides": []},
        "options": {},
    }
    d = p["fieldConfig"]["defaults"]
    if unit:
        d["unit"] = unit
    if decimals is not None:
        d["decimals"] = decimals
    if thresholds:
        d["thresholds"] = {"mode": "absolute", "steps": thresholds}
        d["color"] = {"mode": "thresholds"}
    if kind == "stat":
        p["options"] = {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
                        "colorMode": "background", "graphMode": "none", "textMode": "value_and_name"}
    if kind == "timeseries":
        d["custom"] = {"drawStyle": "line", "lineWidth": 2, "pointSize": 5, "showPoints": "always", "spanNulls": True}
    return p


GOOD, WARN, BAD = "green", "orange", "red"
panels = [
    # ── 1행: 서비스 상태 한눈에 ──────────────────────────────────────────────────
    panel(1, "서비스 상태", "stat", 0, 0, 6, 4,
          [target('up{job="app"}', "앱"), target('up{job="ocr"}', "OCR")],
          desc="Prometheus 가 /metrics 를 읽지 못하면 0 (다운)",
          thresholds=[{"color": BAD, "value": None}, {"color": GOOD, "value": 1}]),
    panel(2, "품질 기준 미달 결과", "stat", 6, 0, 6, 4,
          [target(f'sum(increase(marathon_georef_suspicious_total{W})) or vector(0)', "건수")],
          desc="앵커 8개 미만, 재투영 오차 기준 초과, GPX 경로 100km 초과 (decision-log 22·25번 사례 기준)",
          thresholds=[{"color": GOOD, "value": None}, {"color": BAD, "value": 1}], decimals=0),
    panel(3, "단계별 실패", "stat", 12, 0, 6, 4,
          [target(f'sum(increase(marathon_stage_failures_total{W})) or vector(0)', "앱"),
           target(f'sum(increase(marathon_ocr_service_failures_total{W})) or vector(0)', "OCR 서비스")],
          thresholds=[{"color": GOOD, "value": None}, {"color": BAD, "value": 1}], decimals=0),
    panel(4, "처리한 지도 수", "stat", 18, 0, 6, 4,
          [target(f'sum(increase(marathon_request_seconds_count{{path="/api/run_ocr"}}{W}))', "run_ocr")],
          thresholds=[{"color": "blue", "value": None}], decimals=0),

    # ── 2행: 속도 ───────────────────────────────────────────────────────────────
    panel(5, "단계별 평균 처리 시간", "timeseries", 0, 4, 12, 8,
          [target('sum by (stage) (increase(marathon_stage_seconds_sum[15m])) / sum by (stage) (increase(marathon_stage_seconds_count[15m]))', "{{stage}}"),
           target('sum(increase(marathon_ocr_service_seconds_sum[15m])) / sum(increase(marathon_ocr_service_seconds_count[15m]))', "ocr_service (서비스 내부)")],
          unit="s", desc="최근 15분 평균. stage=ocr 과 ocr_service 의 차이 = 컨테이너 간 HTTP 비용"),
    panel(6, "API 처리 시간 p95", "timeseries", 12, 4, 12, 8,
          [target('histogram_quantile(0.95, sum by (le, path) (increase(marathon_request_seconds_bucket{path=~"/api/(run_ocr|run_georeference|predict)"}[15m])))', "{{path}}")],
          unit="s"),

    # ── 3행: 결과 품질 ──────────────────────────────────────────────────────────
    panel(7, "최종 앵커 수 (평균)", "stat", 0, 12, 6, 5,
          [target(f'sum(increase(marathon_georef_anchors_sum{W})) / sum(increase(marathon_georef_anchors_count{W}))', "앵커")],
          desc="8개 미만이면 변환식이 흔들릴 위험",
          thresholds=[{"color": BAD, "value": None}, {"color": WARN, "value": 6}, {"color": GOOD, "value": 8}], decimals=1),
    panel(8, "평균 재투영 오차", "stat", 6, 12, 6, 5,
          [target(f'sum(increase(marathon_georef_reprojection_error_px_sum{W})) / sum(increase(marathon_georef_reprojection_error_px_count{W}))', "오차")],
          unit="px", desc="기준 7px",
          thresholds=[{"color": GOOD, "value": None}, {"color": WARN, "value": 5}, {"color": BAD, "value": 7}], decimals=2),
    panel(9, "GPX 경로 길이 (평균)", "stat", 12, 12, 6, 5,
          [target(f'sum(increase(marathon_gpx_route_km_sum{W})) / sum(increase(marathon_gpx_route_km_count{W}))', "경로")],
          unit="lengthkm", desc="풀코스도 약 42km. 100km 를 넘으면 변환식 붕괴 의심",
          thresholds=[{"color": GOOD, "value": None}, {"color": WARN, "value": 60}, {"color": BAD, "value": 100}], decimals=2),
    panel(10, "품질 기준 미달 원인별", "timeseries", 18, 12, 6, 5,
          [target('sum by (reason) (increase(marathon_georef_suspicious_total[15m]))', "{{reason}}")]),

    # ── 4행: 자원·용량 ──────────────────────────────────────────────────────────
    panel(11, "API별 GPU 메모리 최대 사용량 (마지막 요청)", "timeseries", 0, 17, 12, 8,
          [target('marathon_request_vram_peak_bytes{path=~"/api/(run_ocr|predict|run_georeference)"}', "{{path}}")],
          unit="bytes", desc="T4 16GB. 90%(약 14GB)를 넘으면 동시 요청 시 메모리 부족 위험"),
    panel(12, "OCR 서비스 대기 시간 p95", "timeseries", 12, 17, 12, 8,
          [target('histogram_quantile(0.95, sum by (le) (increase(marathon_ocr_service_queue_wait_seconds_bucket[15m])))', "대기 p95")],
          unit="s", desc="OCR 은 한 번에 하나씩 처리한다. 이 값이 커지면 요청이 줄을 서는 것 → OCR 컨테이너를 늘릴 신호"),
]

dashboard = {
    "uid": "marathon-gpx", "title": "marathon-gpx 서비스 운영", "tags": ["marathon-gpx"],
    "timezone": "browser", "schemaVersion": 39, "version": 1, "refresh": "30s",
    "time": {"from": "now-3h", "to": "now"},
    "panels": panels,
}

out = Path(__file__).parent / "dashboards" / "marathon.json"
out.parent.mkdir(parents=True, exist_ok=True)
out.write_text(json.dumps(dashboard, ensure_ascii=False, indent=1), encoding="utf-8")
print(f"생성: {out} (패널 {len(panels)}개)")
