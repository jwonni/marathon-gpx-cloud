"""
Hi-SAM 텍스트 영역 탐지 래퍼 (polygon JSON 출력).

Hi-SAM 저장소가 Config.HISAM_REPO_DIR에 존재해야 하며,
체크포인트가 Config.HISAM_CHECKPOINT 경로에 있어야 한다.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from types import SimpleNamespace

import cv2
import numpy as np
import torch
from shapely.geometry import Polygon

logger = logging.getLogger(__name__)

try:
    import pyclipper
except ModuleNotFoundError:
    raise ImportError("pyclipper가 필요합니다: pip install pyclipper")

from src.config import Config


_hisam_amg = None
_hisam_model = None
_hisam_cached_type: str | None = None
# set_image + predict 는 AMG 내부 상태를 공유하므로 동시 호출 금지
_hisam_lock = threading.Lock()


def _get_hisam_amg():
    """Hi-SAM 모델 + AutoMaskGenerator 싱글톤.

    HISAM_MODEL_TYPE 이 바뀌면 캐시를 무효화하고 다시 로드한다.
    """
    global _hisam_amg, _hisam_model, _hisam_cached_type

    if _hisam_amg is not None and _hisam_cached_type == Config.HISAM_MODEL_TYPE:
        return _hisam_amg

    hisam_dir = str(Config.HISAM_REPO_DIR)
    if hisam_dir not in sys.path:
        sys.path.insert(0, hisam_dir)

    from hi_sam.modeling.build import model_registry
    from hi_sam.modeling.auto_mask_generator import AutoMaskGenerator

    args = SimpleNamespace(
        checkpoint   = str(Config.HISAM_CHECKPOINT),
        model_type   = Config.HISAM_MODEL_TYPE,
        device       = str(Config.DEVICE),
        hier_det     = True,
        input_size   = list(Config.HISAM_INPUT_SIZE),
        attn_layers  = 1,
        prompt_len   = 12,
        layout_thresh= 0.5,
    )

    original_cwd = os.getcwd()
    t = time.perf_counter()
    try:
        os.chdir(hisam_dir)
        _hisam_model = model_registry[Config.HISAM_MODEL_TYPE](args)
        _hisam_model.eval().to(Config.DEVICE)
        _hisam_amg = AutoMaskGenerator(
            _hisam_model,
            efficient_hisam=(Config.HISAM_MODEL_TYPE in ["vit_s", "vit_t"]),
        )
        if torch.cuda.is_available() and Config.DEVICE.type == "cuda":
            torch.cuda.synchronize()
    finally:
        os.chdir(original_cwd)

    _hisam_cached_type = Config.HISAM_MODEL_TYPE
    logger.info("[Hi-SAM][timing] model_load=%.3fs (type=%s, device=%s) [cache miss]",
                time.perf_counter() - t, Config.HISAM_MODEL_TYPE, Config.DEVICE)
    return _hisam_amg


def unclip(p: np.ndarray, unclip_ratio: float = 3.5) -> list:
    """polygon을 unclip_ratio 비율만큼 바깥으로 확장한다."""
    poly = Polygon(p)
    distance = poly.area * unclip_ratio / max(poly.length, 1e-6)
    offset = pyclipper.PyclipperOffset()
    offset.AddPath(p.astype(np.int32), pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
    return np.array(offset.Execute(distance))


def mask_to_polygon_list(
    mask: np.ndarray,
    img_h: int,
    img_w: int,
    min_area: int = 32,
) -> list[np.ndarray]:
    """binary mask → 유효 polygon 목록."""
    mask = mask.astype(np.uint8)
    polygons: list[np.ndarray] = []
    contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)

    for cont in contours:
        epsilon = 0.002 * cv2.arcLength(cont, True)
        approx  = cv2.approxPolyDP(cont, epsilon, True)
        points  = approx.reshape((-1, 2))
        if points.shape[0] < 4:
            continue
        try:
            pts = unclip(points)
            if len(pts) != 1:
                continue
            pts = pts[0].astype(np.int32)
        except Exception:
            pts = points.astype(np.int32)
        if len(pts) < 4:
            continue
        if Polygon(pts).area < min_area:
            continue
        pts[:, 0] = np.clip(pts[:, 0], 0, img_w - 1)
        pts[:, 1] = np.clip(pts[:, 1], 0, img_h - 1)
        polygons.append(pts)

    return polygons


def run_hisam_to_json(image_path: str, json_path: str | None = None) -> dict:
    """Hi-SAM으로 이미지에서 word polygon JSON payload를 반환한다.

    Args:
        image_path: 원본 이미지 경로
        json_path:  결과 JSON 저장 경로 (None이면 저장 생략)

    Returns:
        {image_path, image_width, image_height, num_words,
         words: [{id, vertices}, ...]}
    """
    hisam_dir    = str(Config.HISAM_REPO_DIR)
    original_cwd = os.getcwd()

    is_cuda = torch.cuda.is_available() and Config.DEVICE.type == "cuda"

    def _sync():
        if is_cuda:
            torch.cuda.synchronize()

    t_total = time.perf_counter()

    with _hisam_lock:
        # 모델은 모듈 레벨 싱글톤으로 캐싱 — 첫 호출에서만 로드, 이후엔 재사용
        t = time.perf_counter()
        cache_hit = _hisam_amg is not None and _hisam_cached_type == Config.HISAM_MODEL_TYPE
        amg = _get_hisam_amg()
        _sync()
        if cache_hit:
            logger.info("[Hi-SAM][timing] model_load=%.3fs [cache hit]",
                        time.perf_counter() - t)

        try:
            os.chdir(hisam_dir)

            t = time.perf_counter()
            image_bgr = cv2.imread(image_path)
            if image_bgr is None:
                raise RuntimeError(f"이미지를 읽을 수 없습니다: {image_path}")
            image_rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
            img_h, img_w = image_rgb.shape[:2]
            logger.info("[Hi-SAM][timing] image_read=%.3fs (size=%dx%d)",
                        time.perf_counter() - t, img_w, img_h)

            t = time.perf_counter()
            amg.set_image(image_rgb)
            _sync()
            logger.info("[Hi-SAM][timing] set_image=%.3fs", time.perf_counter() - t)

            t = time.perf_counter()
            with torch.inference_mode():
                masks, _scores, _affinity = amg.predict(
                    from_low_res    = False,
                    fg_points_num   = Config.HISAM_TOTAL_POINTS,
                    batch_points_num= Config.HISAM_BATCH_POINTS,
                    score_thresh    = Config.HISAM_SCORE_THRESH,
                    nms_thresh      = Config.HISAM_NMS_THRESH,
                )
            _sync()
            n_masks = 0 if masks is None else len(masks)
            logger.info("[Hi-SAM][timing] predict=%.3fs (masks=%d, fg_points=%d, batch=%d)",
                        time.perf_counter() - t, n_masks,
                        Config.HISAM_TOTAL_POINTS, Config.HISAM_BATCH_POINTS)
        finally:
            os.chdir(original_cwd)

    if masks is None:
        raise RuntimeError("Hi-SAM 마스크 예측 실패")

    t = time.perf_counter()
    word_masks = masks[:, 0, :, :].astype(np.uint8)

    all_word_polygons: list[dict] = []
    for wm in word_masks:
        for poly in mask_to_polygon_list(wm, img_h, img_w,
                                         min_area=Config.HISAM_MIN_POLYGON_AREA):
            all_word_polygons.append({
                "id":       len(all_word_polygons),
                "vertices": poly.tolist(),
            })
    logger.info("[Hi-SAM][timing] mask_to_polygon=%.3fs (polygons=%d)",
                time.perf_counter() - t, len(all_word_polygons))

    payload = {
        "image_path":   image_path,
        "image_width":  img_w,
        "image_height": img_h,
        "num_words":    len(all_word_polygons),
        "words":        all_word_polygons,
    }

    if json_path is not None:
        os.makedirs(os.path.dirname(json_path), exist_ok=True)
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)

    # 모델은 캐시에 유지하고 중간 텐서만 정리
    del masks, word_masks
    if torch.cuda.is_available():
        torch.cuda.empty_cache()

    logger.info("[Hi-SAM][timing] TOTAL=%.3fs (image=%dx%d, words=%d)",
                time.perf_counter() - t_total, img_w, img_h, len(all_word_polygons))

    return payload
