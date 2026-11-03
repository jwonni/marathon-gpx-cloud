"""OCR 방식 변경이 최종 지리좌표 결과에 미치는 영향 측정 (프로젝트 코드는 수정하지 않음).

A = 현재 방식 (PaddleOCR 파이프라인: 크롭마다 검출 + 인식)
C = 인식 전용 배치 (검출 생략, TextRecognition 을 batch 로)

같은 Hi-SAM 결과(bench/eq/hisam.json)를 입력으로 고정하고, 두 OCR 결과를 각각
/api/run_georeference 와 같은 로직(카카오 검색 → MAD → 재투영 정제 → 마커 오프셋 → 호모그래피)에 넣어 비교한다.
대조군으로 A 를 두 번 돌려 카카오 검색 쪽의 실행 간 흔들림도 함께 잰다.

앱 이미지에서 실행 (현재 src 와 bench 를 마운트, 카카오 키는 --env-file):
  docker run --rm --env-file .env -v "<repo>/src:/app/src" -v "<repo>/bench:/work" \
    -v paddlex-cache:/root/.paddlex marathon-gpx:local python /work/ocr_variant_georef.py
결과: bench/eq/georef_compare.json
"""
import json
import math
import os
import sys
import tempfile
import time

os.environ.setdefault("FLAGS_use_mkldnn", "0")
os.environ.setdefault("FLAGS_enable_pir_api", "0")
os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")
sys.path.insert(0, "/app")

from src.config import Config  # noqa: E402
from src.georeferencing import ocr as O  # noqa: E402

WORK = "/work"
IMAGE = f"{WORK}/eq/bench.webp"
HISAM = f"{WORK}/eq/hisam.json"
OCR_A = f"{WORK}/eq/local.json"          # 10-28 동일성 검증에서 만든 현재 방식 결과
REC_BATCH = 32


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def ocr_rec_only():
    """run_ocr 흐름은 그대로 두고, 크롭별 PaddleOCR 호출만 '인식 전용 배치' 결과로 바꿔 끼운다."""
    from paddleocr import TextRecognition
    payload = load(HISAM)
    payload["image_path"] = IMAGE
    out_dir = tempfile.mkdtemp()

    # run_ocr 와 같은 크롭 준비(파일명이 결정적이라 같은 out_dir 이면 같은 경로가 나온다)
    prepared = O.build_all_paddle_inputs(O.build_crop_records_from_json(payload), out_dir)
    paths = [p for r in prepared for p in (r["paddle_base_path"], r["paddle_retry_path"])]

    rec = TextRecognition(model_name="korean_PP-OCRv5_mobile_rec", device="cpu")
    t = time.perf_counter()
    cache = {}
    for p, r in zip(paths, rec.predict(input=paths, batch_size=REC_BATCH)):
        text, score = (r["rec_text"] or ""), float(r["rec_score"])
        cache[p] = {"text": text or None, "score": score,
                    "items": [{"text": text, "score": score, "cx": 0, "cy": 0}] if text else []}
    elapsed = time.perf_counter() - t

    O.load_paddle_ocr = lambda: None                                   # 파이프라인 로드 생략
    O.run_paddle_on_path = lambda path, ocr: cache[path]              # 호출 대신 배치 결과 사용
    results = O.run_ocr(payload, out_dir)
    return results, elapsed, len(paths)


def georeference(ocr_results):
    """app.py run_georeference_endpoint 와 같은 순서 (오버레이 생성만 제외)"""
    import cv2
    from src.georeferencing.anchor_builder import build_anchors, is_good_anchor
    from src.georeferencing.homography import (HomographyTransform, iterative_outlier_removal,
                                               progressive_offset_calibration)
    key = os.environ["KAKAO_API_KEY"]
    candidates = [r for r in ocr_results if is_good_anchor(r.get("text", ""))]
    after_mad = build_anchors(candidates, key)
    pass1 = iterative_outlier_removal(after_mad, max_error_px=Config.HOMOGRAPHY_MAX_ERROR_PX,
                                      min_anchors=Config.HOMOGRAPHY_MIN_ANCHORS)
    (dx, dy), _ = progressive_offset_calibration(pass1, cv2.imread(IMAGE),
                                                 consistency_px=Config.CALIB_CONSISTENCY_PX,
                                                 min_samples=Config.CALIB_MIN_SAMPLES, visualize=False)
    corrected = [(a[0] + dx, a[1] + dy, a[2], a[3], a[4], a[5]) for a in after_mad]
    final = iterative_outlier_removal(corrected, max_error_px=Config.HOMOGRAPHY_MAX_ERROR_PX,
                                      min_anchors=Config.HOMOGRAPHY_MIN_ANCHORS)
    tf = HomographyTransform(final)
    errs = tf.reprojection_errors(final)
    return {
        "ocr_texts": len(ocr_results),
        "anchor_candidates": len(candidates),
        "anchors_after_mad": len(after_mad),
        "anchors_final": len(final),
        "mean_reprojection_error_px": round(sum(errs) / len(errs), 2),
        "offset_px": [round(dx, 2), round(dy, 2)],
        "final_places": sorted(a[5] for a in final),
        "homography": tf.to_dict(),
        "_tf": tf,
    }


def haversine_m(a, b):
    lat1, lng1, lat2, lng2 = map(math.radians, (*a, *b))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def transform_gap(tf1, tf2, w, h, n=20):
    """지도 전체를 n×n 격자로 찍어 두 변환이 내놓는 GPS 좌표의 거리(m) — GPX 결과가 얼마나 달라지나"""
    d = [haversine_m(tf1.pixel_to_geo(x, y), tf2.pixel_to_geo(x, y))
         for x in [w * (i + 0.5) / n for i in range(n)] for y in [h * (j + 0.5) / n for j in range(n)]]
    d.sort()
    return {"mean_m": round(sum(d) / len(d), 1), "p95_m": round(d[int(len(d) * 0.95)], 1), "max_m": round(d[-1], 1)}


def summary(name, g):
    print(f"[{name}] OCR {g['ocr_texts']}개 → 후보 {g['anchor_candidates']} → MAD 후 {g['anchors_after_mad']} "
          f"→ 최종 앵커 {g['anchors_final']}, 평균 재투영 오차 {g['mean_reprojection_error_px']}px, "
          f"오프셋 {g['offset_px']}")


def main():
    from PIL import Image
    w, h = Image.open(IMAGE).size

    ocr_a = load(OCR_A)
    ocr_c, t_c, n = ocr_rec_only()
    print(f"[C] 인식 전용 배치: {n}장 {t_c:.1f}s → 최종 텍스트 {len(ocr_c)}개 (A: {len(ocr_a)}개)")
    with open(f"{WORK}/eq/rec_only.json", "w", encoding="utf-8") as f:
        json.dump(ocr_c, f, ensure_ascii=False, indent=1, default=float)

    geo_a1 = georeference(ocr_a); summary("A 1회", geo_a1)
    geo_a2 = georeference(ocr_a); summary("A 2회", geo_a2)
    geo_c = georeference(ocr_c);  summary("C   ", geo_c)

    noise = transform_gap(geo_a1["_tf"], geo_a2["_tf"], w, h)
    gap = transform_gap(geo_a1["_tf"], geo_c["_tf"], w, h)
    print(f"[대조] A 1회 vs A 2회 GPS 차이: 평균 {noise['mean_m']}m, p95 {noise['p95_m']}m, 최대 {noise['max_m']}m")
    print(f"[비교] A vs C         GPS 차이: 평균 {gap['mean_m']}m, p95 {gap['p95_m']}m, 최대 {gap['max_m']}m")

    pa, pc = set(geo_a1["final_places"]), set(geo_c["final_places"])
    print(f"[앵커] 공통 {len(pa & pc)}개 / A에만 {sorted(pa - pc)} / C에만 {sorted(pc - pa)}")

    for g in (geo_a1, geo_a2, geo_c):
        g.pop("_tf")
    with open(f"{WORK}/eq/georef_compare.json", "w", encoding="utf-8") as f:
        json.dump({"image_size": [w, h], "C_rec_seconds_cpu": round(t_c, 2),
                   "A_run1": geo_a1, "A_run2": geo_a2, "C": geo_c,
                   "gps_gap_A1_vs_A2": noise, "gps_gap_A_vs_C": gap,
                   "anchors_common": len(pa & pc), "anchors_only_A": sorted(pa - pc),
                   "anchors_only_C": sorted(pc - pa)}, f, ensure_ascii=False, indent=1, default=float)
    print("저장: bench/eq/georef_compare.json")


if __name__ == "__main__":
    main()
