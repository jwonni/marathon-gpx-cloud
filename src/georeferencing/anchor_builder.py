"""
카카오 Local API를 이용한 앵커 구축 (병렬 검색 + 밀집 영역 필터 + 동적 bbox 재검색).

앵커 튜플 형식: (pixel_x, pixel_y, lat, lng, text, place)
"""
from __future__ import annotations

import json
import logging
import random
import string
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlencode

import numpy as np

from src.config import Config

log = logging.getLogger("anchor_builder")

# 괄호 () 는 허용하고 나머지 ASCII 기호는 블랙리스트
SYMBOL_BLACKLIST: set[str] = set(string.punctuation) - set("()")


def is_good_anchor(text: str) -> bool:
    """앵커 후보로 사용 가능한 텍스트인지 판단."""
    text       = str(text).strip()
    text_lower = text.lower()
    if len(text) < Config.ANCHOR_MIN_TEXT_LEN:
        return False
    if any(ch in SYMBOL_BLACKLIST for ch in text):
        return False
    if any(bw in text_lower for bw in Config.ANCHOR_TEXT_BLACKLIST):
        return False
    return True


def kakao_search(
    query:   str,
    api_key: str,
    rect:    str | None = None,
    size:    int = 1,
) -> tuple[float, float, str] | None:
    """카카오 키워드 검색 API 호출 (재시도 + 지수 백오프).

    Args:
        query:   검색어
        api_key: 카카오 REST API 키
        rect:    검색 bbox (lng_min,lat_min,lng_max,lat_max 문자열)
        size:    반환 결과 수

    Returns:
        (lat, lng, place_name) 또는 None
    """
    base_url = "https://dapi.kakao.com/v2/local/search/keyword.json"
    params: dict = {"query": str(query).strip(), "size": size}
    if rect is not None:
        params["rect"] = rect

    for attempt in range(Config.KAKAO_MAX_RETRIES):
        try:
            full_url = f"{base_url}?{urlencode(params, encoding='utf-8')}"
            req  = urllib.request.Request(
                full_url.encode("utf-8").decode("ascii"),
                headers={
                    "Authorization": f"KakaoAK {api_key}",
                    "Accept":        "application/json; charset=utf-8",
                },
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                status = resp.status
                data   = json.loads(resp.read().decode("utf-8"))
            log.debug("[Kakao] query=%r  status=%d", query, status)

            docs = data.get("documents", [])
            if not docs:
                log.debug("[Kakao] query=%r  결과 없음", query)
                return None
            log.debug("[Kakao] query=%r  → place=%r  lat=%s  lng=%s",
                      query, docs[0]["place_name"], docs[0]["y"], docs[0]["x"])
            return float(docs[0]["y"]), float(docs[0]["x"]), docs[0]["place_name"]

        except urllib.request.HTTPError as e:
            data = {}
            try:
                data = json.loads(e.read().decode("utf-8"))
            except Exception:
                pass
            code = data.get("code")
            msg  = str(data.get("msg", ""))
            log.warning("[Kakao] query=%r  HTTP %d: code=%s  msg=%s", query, e.code, code, msg)
            if e.code == 429 or code == -10 or "limit" in msg.lower() or "quota" in msg.lower():
                sleep_s = Config.KAKAO_BACKOFF_BASE * (2 ** attempt) + random.uniform(0, 0.3)
                time.sleep(sleep_s)
                continue
            return None

        except Exception as e:
            log.warning("[Kakao] query=%r  예외: %s", query, e)
            sleep_s = Config.KAKAO_BACKOFF_BASE * (2 ** attempt) + random.uniform(0, 0.3)
            time.sleep(sleep_s)

    return None


def _search_one_anchor(
    item:    dict,
    api_key: str,
    rect:    str | None = None,
) -> tuple | None:
    text = str(item.get("text", "")).strip()
    if not text:
        return None
    result = kakao_search(text, api_key, rect=rect)
    if not result:
        return None
    lat, lng, place = result
    return (
        int(round(float(item["x"]))),
        int(round(float(item["y"]))),
        lat,
        lng,
        text,
        place,
    )


def _parallel_kakao_search(
    items:       list[dict],
    api_key:     str,
    rect:        str | None = None,
    max_workers: int | None = None,
) -> list[tuple]:
    if max_workers is None:
        max_workers = Config.KAKAO_MAX_WORKERS
    results: list[tuple] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_item = {
            executor.submit(_search_one_anchor, item, api_key, rect): item
            for item in items
        }
        for future in as_completed(future_to_item):
            try:
                result = future.result()
            except Exception:
                result = None
            if result:
                results.append(result)
    return results


def _distance_km(lat1, lng1, lat2, lng2):
    """두 위경도 사이 거리(km). numpy 배열끼리도 동작한다."""
    lat1, lng1, lat2, lng2 = map(np.radians, (lat1, lng1, lat2, lng2))
    h = np.sin((lat2 - lat1) / 2) ** 2 + np.cos(lat1) * np.cos(lat2) * np.sin((lng2 - lng1) / 2) ** 2
    return 2 * 6371.0 * np.arcsin(np.sqrt(h))


def _dense_center(raw: list[tuple]) -> tuple[float, float, int]:
    """검색 결과가 가장 촘촘하게 모인 곳의 중심점 → (lat, lng, 그 주변의 서로 다른 장소 수).

    같은 장소가 여러 번 검색된 경우(예: '아파트' 여러 개 → 모두 같은 서울 아파트)는
    한 번만 세어, 흔한 단어 하나가 밀집도를 부풀리지 않게 한다.
    """
    unique: dict[str, tuple[float, float]] = {}
    for r in raw:
        unique.setdefault(r[5], (r[2], r[3]))
    lats = np.array([v[0] for v in unique.values()])
    lngs = np.array([v[1] for v in unique.values()])

    dist = _distance_km(lats[:, None], lngs[:, None], lats[None, :], lngs[None, :])
    near = dist <= Config.ANCHOR_DENSITY_RADIUS_KM
    counts = near.sum(axis=1)
    # 이웃 수가 같으면 이웃까지 평균 거리가 짧은(더 촘촘한) 쪽
    spread = np.where(near, dist, 0).sum(axis=1) / counts
    best = min(range(len(counts)), key=lambda i: (-counts[i], spread[i]))
    members = near[best]
    return float(np.median(lats[members])), float(np.median(lngs[members])), int(counts[best])


def build_anchors(candidates: list[dict], api_key: str) -> list[tuple]:
    """OCR 후보 텍스트 → 카카오 API 병렬 검색 → 밀집 영역 필터 → 동적 bbox 재검색.

    Args:
        candidates: [{text, x, y, ...}, ...] — is_good_anchor 통과 항목
        api_key:    카카오 REST API 키

    Returns:
        영역 필터 통과 앵커 목록 [(pixel_x, pixel_y, lat, lng, text, place), ...]
        최소 4개 미만이면 RuntimeError
    """
    log.info("[Anchor] 카카오 검색 시작: 후보 %d개", len(candidates))
    for c in candidates:
        log.info("[Anchor]   후보: text=%r  x=%s  y=%s", c.get("text"), c.get("x"), c.get("y"))

    # Step 1. 1차 병렬 검색
    raw = _parallel_kakao_search(candidates, api_key, rect=None)
    log.info("[Anchor] 1차 검색 결과: %d개", len(raw))
    for r in raw:
        log.info("[Anchor]   검색 성공: text=%r  place=%r  lat=%.6f  lng=%.6f", r[4], r[5], r[2], r[3])
    if len(raw) < 4:
        raise RuntimeError(
            f"유효 카카오 검색 결과 부족: {len(raw)}개 (최소 4개 필요)"
        )

    # Step 2. 밀집 영역 기반 이상치 분류
    # 중앙값·MAD 는 다른 도시 결과가 절반을 넘으면 중심이 두 도시 사이에 찍히고 반경도 부풀어
    # 다른 도시가 통과한다 (decision-log 22번). 가장 촘촘한 곳을 중심으로, 고정 반경 안만 인정한다.
    center_lat, center_lng, density = _dense_center(raw)
    log.info("[Anchor] 밀집 중심 (%.5f, %.5f), 반경 %.0fkm 안 장소 %d개 → 인정 반경 %.0fkm",
             center_lat, center_lng, Config.ANCHOR_DENSITY_RADIUS_KM, density, Config.ANCHOR_REGION_RADIUS_KM)

    def in_region(r: tuple) -> bool:
        return _distance_km(r[2], r[3], center_lat, center_lng) <= Config.ANCHOR_REGION_RADIUS_KM

    inliers:  list[tuple] = []
    outliers: list[tuple] = []
    for r in raw:
        (inliers if in_region(r) else outliers).append(r)
    log.info("[Anchor] 영역 안 %d개 / 밖 %d개", len(inliers), len(outliers))

    # Step 3. 이상치에 대해 동적 bbox 안에서 재검색
    if inliers and outliers:
        i_lats  = np.array([r[2] for r in inliers])
        i_lngs  = np.array([r[3] for r in inliers])
        lng_min, lng_max = np.percentile(i_lngs, 5), np.percentile(i_lngs, 95)
        lat_min, lat_max = np.percentile(i_lats, 5), np.percentile(i_lats, 95)
        lng_margin = max((lng_max - lng_min) * 0.08, 0.0015)
        lat_margin = max((lat_max - lat_min) * 0.08, 0.0015)
        rect = (
            f"{lng_min - lng_margin},{lat_min - lat_margin},"
            f"{lng_max + lng_margin},{lat_max + lat_margin}"
        )
        retry_items = [{"x": r[0], "y": r[1], "text": r[4]} for r in outliers]
        retry_results = _parallel_kakao_search(
            retry_items, api_key, rect=rect,
            max_workers=max(2, Config.KAKAO_MAX_WORKERS // 2),
        )
        for r in retry_results:
            if in_region(r):
                inliers.append(r)

    if len(inliers) < 4:
        raise RuntimeError(
            f"호모그래피 계산에는 최소 4개 앵커가 필요합니다. 현재 {len(inliers)}개"
        )

    return inliers
