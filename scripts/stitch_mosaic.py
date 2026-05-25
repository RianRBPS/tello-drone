#!/usr/bin/env python3
"""
stitch_mosaic.py
================
Offline mosaic stitcher for images saved by the mosaic_capture node.

Usage
-----
    python3 scripts/stitch_mosaic.py                         # uses data/images/
    python3 scripts/stitch_mosaic.py --images path/to/imgs   # custom folder
    python3 scripts/stitch_mosaic.py --method pose           # pose-guided (needs poses.csv)

Methods
-------
  feature (default)
      Pure OpenCV feature-based stitcher — no pose data needed.
      Robust when there is enough texture overlap between frames.

  pose
      Pose-guided homography using poses.csv saved by mosaic_capture.
      More reliable when overlap is low or frames are nearly textureless.
      Requires rtabmap odometry to have been running during capture.
"""

import argparse
import os
import sys
import csv
import math

import cv2
import numpy as np


# ── helpers ──────────────────────────────────────────────────────────────────

def load_images(folder: str) -> list[tuple[str, np.ndarray]]:
    exts = {'.jpg', '.jpeg', '.png'}
    files = sorted(
        f for f in os.listdir(folder)
        if os.path.splitext(f)[1].lower() in exts and f.startswith('frame_')
    )
    if not files:
        sys.exit(f'[ERROR] No frame_*.jpg files found in {folder}')
    imgs = []
    for f in files:
        img = cv2.imread(os.path.join(folder, f))
        if img is not None:
            imgs.append((f, img))
    print(f'Loaded {len(imgs)} images from {folder}')
    return imgs


def load_poses(folder: str) -> dict[str, dict]:
    path = os.path.join(folder, 'poses.csv')
    if not os.path.isfile(path):
        sys.exit(f'[ERROR] poses.csv not found in {folder} — run with --method feature or capture with mosaic_capture node')
    poses = {}
    with open(path) as f:
        for row in csv.DictReader(f):
            if row['x'] != 'manual':
                poses[row['frame']] = {k: float(row[k]) for k in ('x', 'y', 'z', 'yaw')}
    return poses


# ── method A: feature-based ──────────────────────────────────────────────────

def stitch_feature(images: list[tuple[str, np.ndarray]], output: str):
    imgs = [img for _, img in images]
    stitcher = cv2.Stitcher_create(cv2.Stitcher_SCANS)
    print('Running OpenCV feature stitcher...')
    status, mosaic = stitcher.stitch(imgs)
    if status != cv2.Stitcher_OK:
        codes = {
            cv2.Stitcher_ERR_NEED_MORE_IMGS: 'ERR_NEED_MORE_IMGS — not enough overlap between frames',
            cv2.Stitcher_ERR_HOMOGRAPHY_EST_FAIL: 'ERR_HOMOGRAPHY_EST_FAIL — could not estimate homography',
            cv2.Stitcher_ERR_CAMERA_PARAMS_ADJUST_FAIL: 'ERR_CAMERA_PARAMS_ADJUST_FAIL',
        }
        msg = codes.get(status, f'unknown error code {status}')
        sys.exit(f'[ERROR] Stitching failed: {msg}\nTip: try --method pose, or capture with more overlap (smaller trigger_dist)')
    cv2.imwrite(output, mosaic)
    print(f'Saved mosaic → {output}  ({mosaic.shape[1]}×{mosaic.shape[0]} px)')


# ── method B: pose-guided ────────────────────────────────────────────────────

def stitch_pose(images: list[tuple[str, np.ndarray]], poses: dict, output: str):
    """
    Simple pose-guided placement:
    1. Treat the first frame as origin.
    2. For each subsequent frame compute the (dx, dy) displacement in metres
       and convert to pixels using altitude as scale hint.
    3. Warp all frames onto a shared canvas.
    """
    if not images:
        sys.exit('[ERROR] No images to stitch')

    # --- pick a pixels-per-metre scale ----------------------------------------
    # Use median altitude from poses; fall back to 1 m if poses missing altitude.
    altitudes = [p['z'] for p in poses.values() if p['z'] > 0.1]
    altitude = float(np.median(altitudes)) if altitudes else 1.0
    h, w = images[0][1].shape[:2]
    # Tello camera ~82° horizontal FOV → fov_rad ≈ 1.43 rad
    fov_rad = math.radians(82.0)
    ppm = w / (2.0 * altitude * math.tan(fov_rad / 2.0))   # pixels per metre
    print(f'Pose-guided: altitude={altitude:.2f} m  ppm={ppm:.1f} px/m')

    # --- build per-frame translation offsets ------------------------------------
    first_name = images[0][0]
    origin = poses.get(first_name, {'x': 0.0, 'y': 0.0})

    # canvas size: bounding box of all translated frames + 1 frame margin
    offsets = []
    for fname, _ in images:
        p = poses.get(fname, {'x': 0.0, 'y': 0.0})
        dx = (p['x'] - origin['x']) * ppm
        dy = (p['y'] - origin['y']) * ppm
        offsets.append((dx, dy))

    xs = [o[0] for o in offsets]
    ys = [o[1] for o in offsets]
    min_x, max_x = min(xs), max(xs)
    min_y, max_y = min(ys), max(ys)
    canvas_w = int(max_x - min_x + w) + 1
    canvas_h = int(max_y - min_y + h) + 1
    shift_x = int(-min_x)
    shift_y = int(-min_y)

    canvas = np.zeros((canvas_h, canvas_w, 3), dtype=np.uint8)

    for (fname, img), (dx, dy) in zip(images, offsets):
        tx = int(dx) + shift_x
        ty = int(dy) + shift_y
        x1, y1 = max(tx, 0), max(ty, 0)
        x2, y2 = min(tx + w, canvas_w), min(ty + h, canvas_h)
        sx1, sy1 = x1 - tx, y1 - ty
        sx2, sy2 = sx1 + (x2 - x1), sy1 + (y2 - y1)
        roi = canvas[y1:y2, x1:x2]
        src = img[sy1:sy2, sx1:sx2]
        # simple alpha blend: only overwrite black pixels
        mask = np.all(roi == 0, axis=2)
        roi[mask] = src[mask]
        canvas[y1:y2, x1:x2] = roi

    cv2.imwrite(output, canvas)
    print(f'Saved mosaic → {output}  ({canvas_w}×{canvas_h} px)')


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    parser = argparse.ArgumentParser(description='Stitch mosaic from captured drone frames')
    parser.add_argument('--images', default=os.path.join(repo_root, 'data', 'images'),
                        help='Folder containing frame_*.jpg and poses.csv')
    parser.add_argument('--output', default=os.path.join(repo_root, 'data', 'mosaic.png'),
                        help='Output mosaic file path')
    parser.add_argument('--method', choices=['feature', 'pose'], default='feature',
                        help='Stitching method (default: feature)')
    args = parser.parse_args()

    images = load_images(args.images)

    if args.method == 'feature':
        stitch_feature(images, args.output)
    else:
        poses = load_poses(args.images)
        stitch_pose(images, poses, args.output)


if __name__ == '__main__':
    main()
