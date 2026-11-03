"""저장된 OCR 결과로 지리좌표 단계만 다시 돌려 결과를 평가한다 (OCR·Hi-SAM 무작위성과 무관하게 같은 입력으로 비교).

  docker run --rm --env-file .env -v "<repo>/src:/app/src" -v "<repo>/bench:/work" marathon-gpx:local \
    python /work/georef_offline.py <ocr.json> <image> [start_x,start_y end_x,end_y "<출발 장소>" "<도착 장소>"]

출발·도착을 주면 해당 픽셀을 변환한 GPS 가 카카오 장소 검색 좌표(정답)에서 몇 m 떨어졌는지 잰다.
"""
import json
import math
import os
import sys
import urllib.parse
import urllib.request

sys.path.insert(0, "/app")
sys.path.insert(0, os.path.dirname(__file__))
from ocr_variant_georef import georeference  # noqa: E402  (app.py run_georeference 와 같은 순서)


def place(name):
    q = urllib.parse.urlencode({"query": name, "size": 1})
    req = urllib.request.Request(f"https://dapi.kakao.com/v2/local/search/keyword.json?{q}",
                                 headers={"Authorization": f"KakaoAK {os.environ['KAKAO_API_KEY']}"})
    d = json.load(urllib.request.urlopen(req, timeout=10))["documents"][0]
    return float(d["y"]), float(d["x"]), d["place_name"]


def dist_m(a, b):
    lat1, lng1, lat2, lng2 = map(math.radians, (a[0], a[1], b[0], b[1]))
    h = math.sin((lat2 - lat1) / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin((lng2 - lng1) / 2) ** 2
    return 2 * 6_371_000 * math.asin(math.sqrt(h))


def main(ocr_path, image, start=None, end=None, start_name=None, end_name=None):
    import ocr_variant_georef as V
    V.IMAGE = image                                   # 마커 오프셋 보정에 쓰는 원본 이미지
    from PIL import Image
    w, h = Image.open(image).size

    g = georeference(json.load(open(ocr_path, encoding="utf-8")))
    tf = g.pop("_tf")
    if os.environ.get("HOMOGRAPHY_OUT"):                # /api/convert_gpx 에 넘길 변환식 저장
        with open(os.environ["HOMOGRAPHY_OUT"], "w", encoding="utf-8") as f:
            json.dump(g["homography"], f)
    print(f"OCR {g['ocr_texts']} → 후보 {g['anchor_candidates']} → 영역 필터 후 {g['anchors_after_mad']} "
          f"→ 최종 앵커 {g['anchors_final']}, 평균 재투영 오차 {g['mean_reprojection_error_px']}px")
    print(f"최종 앵커: {g['final_places']}")
    c = tf.pixel_to_geo(w / 2, h / 2)
    corners = [tf.pixel_to_geo(x, y) for x, y in ((0, 0), (w, 0), (0, h), (w, h))]
    print(f"지도 중심 → ({c[0]:.5f}, {c[1]:.5f}),  모서리 → " + ", ".join(f"({a:.3f},{b:.3f})" for a, b in corners))
    span = max(dist_m(a, b) for a in corners for b in corners)
    print(f"지도 네 모서리 사이 최대 거리 {span / 1000:.1f}km (지도 한 장이 덮는 범위로 상식적인지 확인용)")

    if start:
        sx, sy = map(float, start.split(","))
        ex, ey = map(float, end.split(","))
        ts, te = place(start_name), place(end_name)
        ps, pe = tf.pixel_to_geo(sx, sy), tf.pixel_to_geo(ex, ey)
        print(f"출발 픽셀({sx:.0f},{sy:.0f}) → ({ps[0]:.5f},{ps[1]:.5f}), 정답 {ts[2]}까지 {dist_m(ps, ts):,.0f}m")
        print(f"도착 픽셀({ex:.0f},{ey:.0f}) → ({pe[0]:.5f},{pe[1]:.5f}), 정답 {te[2]}까지 {dist_m(pe, te):,.0f}m")


if __name__ == "__main__":
    main(*sys.argv[1:])
