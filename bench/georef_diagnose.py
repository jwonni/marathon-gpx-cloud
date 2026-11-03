"""지리좌표 변환 단계별 추적 — 엉뚱한 도시의 앵커가 왜 필터를 통과하는지 확인 (프로젝트 코드는 수정하지 않음).

  docker run --rm --env-file .env -v "<repo>/src:/app/src" -v "<repo>/bench:/work" \
    marathon-gpx:local python /work/georef_diagnose.py /work/eq/local.json
"""
import json
import os
import sys

import numpy as np

sys.path.insert(0, "/app")
from src.config import Config  # noqa: E402
from src.georeferencing import anchor_builder as AB  # noqa: E402
from src.georeferencing.homography import HomographyTransform  # noqa: E402


def city(lat, lng):
    if 34.85 <= lat <= 35.05 and 127.35 <= lng <= 127.65:
        return "순천"
    if 35.05 <= lat <= 35.30 and 126.65 <= lng <= 127.05:
        return "광주"
    return f"기타({lat:.2f},{lng:.2f})"


def count(anchors):
    c = {}
    for a in anchors:
        k = city(a[2], a[3])[:2]
        c[k] = c.get(k, 0) + 1
    return c


def main(ocr_path):
    key = os.environ["KAKAO_API_KEY"]
    ocr = json.load(open(ocr_path, encoding="utf-8"))
    cands = [r for r in ocr if AB.is_good_anchor(r.get("text", ""))]

    # 1. 1차 검색 (지역 제한 없음)
    raw = AB._parallel_kakao_search(cands, key, rect=None)
    print(f"\n[1] 1차 카카오 검색: 후보 {len(cands)} → 결과 {len(raw)}  도시 분포 {count(raw)}")
    for r in sorted(raw, key=lambda r: city(r[2], r[3])):
        print(f"    {city(r[2], r[3]):<6} {r[4]!r:<16} → {r[5]}")

    # 2. MAD
    lats, lngs = np.array([r[2] for r in raw]), np.array([r[3] for r in raw])
    lat_med, lng_med = np.median(lats), np.median(lngs)
    lat_mad = np.median(np.abs(lats - lat_med)) + 1e-9
    lng_mad = np.median(np.abs(lngs - lng_med)) + 1e-9
    print(f"\n[2] MAD: 중앙값 ({lat_med:.4f}, {lng_med:.4f}) = {city(lat_med, lng_med)}, "
          f"MAD lat {lat_mad:.4f}° (약 {lat_mad * 111:.1f}km) / lng {lng_mad:.4f}° (약 {lng_mad * 91:.1f}km), "
          f"임계 {Config.KAKAO_MAD_THRESH}배 → 허용 반경 약 lat ±{lat_mad * Config.KAKAO_MAD_THRESH * 111:.1f}km, "
          f"lng ±{lng_mad * Config.KAKAO_MAD_THRESH * 91:.1f}km")
    anchors = AB.build_anchors(cands, key)
    print(f"    MAD + 재검색 후: {len(anchors)}개  도시 분포 {count(anchors)}")

    # 3. 재투영 오차 반복 제거 추적 (iterative_outlier_removal 과 같은 규칙)
    print(f"\n[3] 재투영 반복 제거 (기준 {Config.HOMOGRAPHY_MAX_ERROR_PX}px, 최소 {Config.HOMOGRAPHY_MIN_ANCHORS}개에서 멈춤)")
    cur = list(anchors)
    step = 0
    while len(cur) > Config.HOMOGRAPHY_MIN_ANCHORS:
        tf = HomographyTransform(cur)
        e = tf.reprojection_errors(cur)
        if max(e) <= Config.HOMOGRAPHY_MAX_ERROR_PX:
            print(f"    기준 충족으로 종료 ({len(cur)}개)")
            break
        i = e.index(max(e))
        step += 1
        if step <= 8 or len(cur) <= Config.HOMOGRAPHY_MIN_ANCHORS + 6:
            print(f"    {step:02d}. 남은 {len(cur):2d}개, 최대 오차 {max(e):8.1f}px → 제거 [{city(cur[i][2], cur[i][3])}] {cur[i][5]}  "
                  f"(남은 분포 {count(cur[:i] + cur[i + 1:])})")
        cur.pop(i)
    else:
        print(f"    최소 개수({Config.HOMOGRAPHY_MIN_ANCHORS})에 도달해 강제 종료 — 오차 기준은 충족하지 못했을 수 있음")
    tf = HomographyTransform(cur)
    e = tf.reprojection_errors(cur)
    print(f"    최종 {len(cur)}개, 평균 {np.mean(e):.2f}px, 최대 {max(e):.2f}px")
    for a, err in zip(cur, e):
        print(f"      [{city(a[2], a[3])}] {a[5]:<24} 픽셀({a[0]:.0f},{a[1]:.0f}) 오차 {err:.1f}px")

    # 4. 변환식 건전성: 6점에 8자유도 호모그래피 → 과적합 여부
    H = np.array(tf.to_dict()["H"]) if "H" in tf.to_dict() else None
    print(f"\n[4] 변환식: 앵커 {len(cur)}개로 자유도 8인 호모그래피를 맞춤 (여유 {len(cur) * 2 - 8}개 방정식)")
    if H is not None:
        print(f"    H 원근 성분 h31={H[2][0]:.4f}, h32={H[2][1]:.4f} (0에 가까울수록 평면 지도에 맞는 정상 변환)")


if __name__ == "__main__":
    main(sys.argv[1])
