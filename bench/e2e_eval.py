"""지도 1장을 브라우저와 같은 순서로 끝까지 처리하고, 결과 GPS 를 정답 위치와 비교한다.

  docker compose up -d                       # 앱 + OCR 서비스
  python bench/e2e_eval.py <이미지> "<출발 장소명>" "<도착 장소명>"

정답 좌표는 카카오 장소 검색으로 얻는다 (지도에 적힌 출발·도착 장소).
결과: bench/e2e/<이미지 이름>/ (OCR·앵커·GPX·요약) — 지도 캡처에서 나온 데이터라 git 제외
"""
import base64
import json
import math
import os
import re
import sys
import time
from pathlib import Path

import requests

APP = "http://localhost:8010"
ROOT = Path(__file__).resolve().parents[1]


def kakao_key():
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if line.startswith("KAKAO_API_KEY="):
            return line.split("=", 1)[1].strip()
    sys.exit(".env 에 KAKAO_API_KEY 없음")


def place(name):
    r = requests.get("https://dapi.kakao.com/v2/local/search/keyword.json",
                     params={"query": name, "size": 1},
                     headers={"Authorization": f"KakaoAK {kakao_key()}"}, timeout=10)
    d = r.json()["documents"][0]
    return float(d["y"]), float(d["x"]), d["place_name"]


def dist_m(a, b):
    lat1, lng1, lat2, lng2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def post(path, timeout=900, **kw):
    t = time.perf_counter()
    r = requests.post(APP + path, timeout=timeout, **kw)
    el = time.perf_counter() - t
    try:
        body = r.json()
    except ValueError:
        body = {"raw": r.text[:300]}
    print(f"  {path:<24} HTTP {r.status_code}  {el:6.1f}s")
    return r.status_code, body, el


def gpx_points(gpx):
    return [(float(a), float(b)) for a, b in re.findall(r'lat="([-\d.]+)"\s+lon="([-\d.]+)"', gpx)]


def main(image, start_name, end_name):
    img = Path(image)
    out = ROOT / "bench" / "e2e" / img.stem
    out.mkdir(parents=True, exist_ok=True)
    raw = img.read_bytes()
    data_url = "data:image/png;base64," + base64.b64encode(raw).decode()
    files = lambda: {"file": ("image.png", raw, "image/png")}  # noqa: E731

    for _ in range(150):
        try:
            if requests.get(APP + "/api/health", timeout=2).ok:
                break
        except requests.RequestException:
            pass
        time.sleep(2)

    print(f"[1] 파이프라인 실행: {img.name}")
    times = {}
    _, d2, times["predict"] = post("/api/predict", files=files())
    _, d3a, times["postprocess"] = post("/api/postprocess", json={"mask_b64": d2["mask_b64"]})
    _, d3b, times["auto_extract_path"] = post("/api/auto_extract_path",
                                              json={"skeleton_b64": d3a["skeleton_b64"], "input_img_b64": data_url})
    path, start_px, end_px = d3b["path"], d3b["start"], d3b["end"]
    print(f"     경로 픽셀 {len(path)}개, 시작 {start_px}, 끝 {end_px}")
    if d3b.get("overlay_b64"):
        (out / "path_overlay.jpg").write_bytes(base64.b64decode(d3b["overlay_b64"].split(",", 1)[1]))

    # Hi-SAM 무작위성으로 실패할 수 있어 측정용으로만 재시도 (서비스 코드는 재시도하지 않음)
    for attempt in range(1, int(os.environ.get("OCR_TRIES", "1")) + 1):
        code5, d5, times["run_ocr"] = post("/api/run_ocr", files=files())
        if code5 == 200:
            break
    summary_ocr_attempts = attempt
    ocr = d5.get("ocr_results", [])
    (out / "ocr.json").write_text(json.dumps(ocr, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"     OCR 텍스트 {len(ocr)}개: {[o['text'] for o in ocr][:25]}")

    code6, d6, times["run_georeference"] = post("/api/run_georeference", files=files(),
                                                data={"ocr_results_json": json.dumps(ocr, ensure_ascii=False)})
    summary = {"image": img.name, "times_s": {k: round(v, 2) for k, v in times.items()},
               "path_pixels": len(path), "start_px": start_px, "end_px": end_px, "ocr_texts": len(ocr),
               "ocr_attempts": summary_ocr_attempts,
               "georef_http": code6}
    if code6 != 200:
        summary["georef_error"] = d6
        print(f"     지리참조 실패: {d6}")
    else:
        anchors = d6["anchors"]
        print(f"     응답: status={d6['status']}, 앵커 {d6['num_anchors']}개, 평균 오차 {d6['mean_reprojection_error_px']}px")
        if d6.get("anchor_overlay_b64"):
            (out / "anchor_overlay.jpg").write_bytes(base64.b64decode(d6["anchor_overlay_b64"].split(",", 1)[1]))
        _, dg, times["convert_gpx"] = post("/api/convert_gpx", json={
            "start": start_px, "end": end_px, "path": path,
            "homography_params": d6["homography_params"], "category_pixels": d5.get("category_pixels")})
        gpx = dg["gpx_content"]
        (out / "route.gpx").write_text(gpx, encoding="utf-8")
        pts = gpx_points(gpx)

        print("\n[2] 정답 비교 (카카오 장소 검색 좌표 기준)")
        truth_s, truth_e = place(start_name), place(end_name)
        ds_s, de_e = dist_m(pts[0], truth_s), dist_m(pts[-1], truth_e)
        ds_e, de_s = dist_m(pts[0], truth_e), dist_m(pts[-1], truth_s)   # 방향이 뒤집혔을 가능성
        print(f"     출발 정답 {truth_s[2]} ({truth_s[0]:.5f},{truth_s[1]:.5f})")
        print(f"     도착 정답 {truth_e[2]} ({truth_e[0]:.5f},{truth_e[1]:.5f})")
        print(f"     GPX 첫 점 ({pts[0][0]:.5f},{pts[0][1]:.5f}) → 출발 정답까지 {ds_s:,.0f}m")
        print(f"     GPX 끝 점 ({pts[-1][0]:.5f},{pts[-1][1]:.5f}) → 도착 정답까지 {de_e:,.0f}m")
        if ds_e + de_s < ds_s + de_e:
            print(f"     ⚠ 방향이 뒤집힌 것으로 보임: 첫 점→도착 {ds_e:,.0f}m, 끝 점→출발 {de_s:,.0f}m")
        route_len = sum(dist_m(a, b) for a, b in zip(pts, pts[1:]))
        print(f"     GPX 총 길이 {route_len / 1000:.2f}km (점 {len(pts)}개)")
        print("\n[3] 최종 앵커 (정답 출발점과의 거리)")
        for a in anchors:
            print(f"     {a['place']:<28} 픽셀({a['pixel_x']},{a['pixel_y']}) 오차 {a['reprojection_error_px']}px, "
                  f"출발점에서 {dist_m((a['lat'], a['lng']), truth_s) / 1000:.1f}km")
        summary.update({"status": d6["status"], "num_anchors": d6["num_anchors"],
                        "mean_reprojection_error_px": d6["mean_reprojection_error_px"],
                        "anchors": [{k: a[k] for k in ("text", "place", "lat", "lng", "reprojection_error_px")} for a in anchors],
                        "truth": {"start": truth_s, "end": truth_e},
                        "gpx_first": pts[0], "gpx_last": pts[-1],
                        "start_error_m": round(ds_s), "end_error_m": round(de_e),
                        "route_km": round(route_len / 1000, 2)})
    summary["times_s"] = {k: round(v, 2) for k, v in times.items()}
    print(f"\n[4] 구간 시간: {summary['times_s']}  합계 {sum(times.values()):.1f}s")
    (out / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"저장: {out}")


if __name__ == "__main__":
    main(*sys.argv[1:4])
