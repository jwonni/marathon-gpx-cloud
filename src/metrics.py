"""Prometheus 지표 정의.

앱은 숫자를 메모리에만 기록하고 /metrics 로 공개한다. Prometheus 가 주기적으로 읽어 간다(pull).
기록 자체는 네트워크 호출이 없으므로 요청 처리 시간에 영향을 주지 않는다.
"""
import math

from prometheus_client import Counter, Gauge, Histogram

# ── 요청 공통 (app.py 미들웨어에서 기록) ─────────────────────────────────────────
REQUEST_SECONDS = Histogram(
    "marathon_request_seconds", "API 요청 처리 시간", ["path", "status"],
    buckets=(0.1, 0.5, 1, 2, 5, 10, 15, 20, 30, 45, 60, 120, 300),
)
REQUEST_VRAM_PEAK_BYTES = Gauge(
    "marathon_request_vram_peak_bytes", "마지막 요청의 GPU 메모리 최대 사용량", ["path"],
)

# ── 단계별 처리 시간 ────────────────────────────────────────────────────────────
STAGE_SECONDS = Histogram(
    "marathon_stage_seconds", "파이프라인 단계별 처리 시간", ["stage"],
    buckets=(0.5, 1, 2, 5, 10, 15, 20, 30, 60, 120, 300, 600),
)
STAGE_FAILURES = Counter(
    "marathon_stage_failures_total", "단계별 실패 수", ["stage"],
)

# ── 지리좌표 결과 품질: "응답은 성공인데 결과가 틀린" 조용한 실패를 잡기 위한 지표 ────────
GEOREF_ANCHORS = Histogram(
    "marathon_georef_anchors", "최종 앵커 수",
    buckets=(4, 6, 8, 10, 15, 20, 30, 50),
)
GEOREF_REPROJ_ERROR_PX = Histogram(
    "marathon_georef_reprojection_error_px", "최종 평균 재투영 오차(px)",
    buckets=(1, 2, 3, 5, 7, 10, 20, 50),
)
GEOREF_SUSPICIOUS = Counter(
    "marathon_georef_suspicious_total", "성공으로 응답했지만 품질 기준을 벗어난 결과 수", ["reason"],
)
GPX_ROUTE_KM = Histogram(
    "marathon_gpx_route_km", "지리좌표로 변환한 GPX 경로 길이(km)",
    buckets=(1, 3, 5, 10, 21, 42, 60, 100, 1000),
)

# 조용한 실패 판정 기준 (decision-log 22·25번 사례: 앵커 6개·평균 6.5px 인데 다른 도시, 경로 수천 km)
MIN_HEALTHY_ANCHORS = 8
MAX_HEALTHY_ROUTE_KM = 100

# 라벨이 있는 카운터는 처음 발생하는 순간 1 로 생겨나서, Prometheus 의 increase() 가 0→1 증가를 보지 못한다
# (첫 번째 실패에 알람이 안 울림). 가능한 라벨을 미리 0 으로 만들어 둔다.
for _reason in ("few_anchors", "error_over_threshold", "route_too_long"):
    GEOREF_SUSPICIOUS.labels(_reason)
for _stage in ("prepare", "hisam", "ocr", "georef"):
    STAGE_FAILURES.labels(_stage)


def route_length_km(path: list, pixel_to_geo) -> float:
    """픽셀 경로를 위경도로 바꿔 총 길이(km)를 구한다."""
    pts = [pixel_to_geo(x, y) for x, y in path]
    total = 0.0
    for (lat1, lng1), (lat2, lng2) in zip(pts, pts[1:]):
        p1, p2 = math.radians(lat1), math.radians(lat2)
        h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lng2 - lng1) / 2) ** 2
        total += 2 * 6371.0 * math.asin(math.sqrt(h))
    return total
