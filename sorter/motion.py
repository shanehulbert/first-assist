"""First Assist - measured camera movement.

Tracks feature points between frames (optical flow) and fits the overall
image motion: sideways drift = pan, vertical = tilt, the image growing =
push in, shrinking = pull out, small fast jitter = handheld.

Limits: a zoom and a dolly push look the same; strong subject motion or
animated LED screens can mislead it, so results carry a confidence and can
always be overridden in the app.

Needs OpenCV:  pip3 install --user opencv-python-headless
    python3 motion.py "/path/to/clip.mov"
"""
import math, sys

import cv2
import numpy as np

STEP_S = 1 / 6      # analyse ~6 frames per second
WIDTH = 320         # working resolution


def _pairs(path):
    cap = cv2.VideoCapture(path)
    fps = cap.get(cv2.CAP_PROP_FPS) or 24
    step = max(1, int(round(fps * STEP_S)))
    prev, i = None, 0
    while True:
        ok = cap.grab()
        if not ok:
            break
        if i % step == 0:
            ok, frame = cap.retrieve()
            if not ok:
                break
            h, w = frame.shape[:2]
            g = cv2.cvtColor(cv2.resize(frame, (WIDTH, int(h * WIDTH / w))), cv2.COLOR_BGR2GRAY)
            if prev is not None:
                yield prev, g, step / fps
            prev = g
        i += 1
    cap.release()


def _estimate(a, b):
    pts = cv2.goodFeaturesToTrack(a, maxCorners=300, qualityLevel=0.01, minDistance=8)
    if pts is None or len(pts) < 12:
        return None
    nxt, st, _ = cv2.calcOpticalFlowPyrLK(a, b, pts, None)
    good = st.reshape(-1) == 1
    if good.sum() < 12:
        return None
    m, inl = cv2.estimateAffinePartial2D(pts[good], nxt[good], method=cv2.RANSAC, ransacReprojThreshold=2.0)
    if m is None:
        return None
    scale = math.hypot(m[0, 0], m[1, 0])
    return m[0, 2], m[1, 2], scale, float(inl.mean()) if inl is not None else 0.0


def _label(dx, dy, zoom, jitter, w, h):
    """dx/dy: total image shift as a fraction of frame size; zoom: total log scale."""
    moves = []
    if zoom > 0.10:
        moves.append("Push in")
    elif zoom < -0.10:
        moves.append("Pull out")
    if abs(dx) > 0.25:
        moves.append("Pan right" if dx < 0 else "Pan left")   # image drifts left when the camera pans right
    if abs(dy) > 0.20:
        moves.append("Tilt up" if dy > 0 else "Tilt down")
    if not moves:
        moves.append("Handheld" if jitter > 0.006 else "Static")
    return moves


def analyse(path):
    frames = [r for r in (_estimate(a, b) for a, b, _ in _pairs(path)) if r]
    if len(frames) < 3:
        return {"movement": "", "detail": "not enough detail to measure", "confidence": 0}
    dx = np.array([f[0] for f in frames]) / WIDTH
    dy = np.array([f[1] for f in frames]) / (WIDTH * 9 / 16)
    lz = np.log(np.array([f[2] for f in frames]))
    inl = float(np.mean([f[3] for f in frames]))
    # jitter = frame-to-frame wobble left after removing the smooth trend
    k = 5
    smooth = np.convolve(dx, np.ones(k) / k, mode="same") if len(dx) >= k else dx
    resid = (dx - smooth)[k // 2: len(dx) - k // 2] if len(dx) > k else dx - smooth
    jitter = float(np.std(resid)) if len(resid) else 0.0

    # whole-clip and per-2-second segments
    seg_n = 12
    segs = []
    for s in range(0, len(frames), seg_n):
        sl = slice(s, s + seg_n)
        if len(dx[sl]) >= 4:
            segs.append(_label(dx[sl].sum(), dy[sl].sum(), lz[sl].sum(), float(np.std((dx - smooth)[sl][k // 2:-(k // 2)])) if len(dx[sl]) > k else 0.0, 1, 1)[0])
    seq = [m for i, m in enumerate(segs) if i == 0 or m != segs[i - 1]]
    overall = _label(dx.sum(), dy.sum(), lz.sum(), jitter, 1, 1)
    moving = [m for m in seq if m not in ("Static",)]
    if len(set(moving)) > 1:
        movement = "Mixed"
    elif moving:
        movement = moving[0] if moving[0] != "Handheld" or overall[0] == "Handheld" else overall[0]
    elif overall[0] not in ("Static", "Handheld"):
        movement = overall[0]  # slow, steady move: small in every 2 s window but adds up over the clip
    else:
        movement = "Static"
    conf =round(min(1.0, inl) * (0.6 if movement == "Mixed" else 1.0), 2)
    return {"movement": movement, "detail": " → ".join(seq[:6]), "confidence": conf,
            "pan": round(float(dx.sum()), 3), "tilt": round(float(dy.sum()), 3), "zoom": round(float(lz.sum()), 3),
            "jitter": round(jitter, 4)}


if __name__ == "__main__":
    for p in sys.argv[1:]:
        print(p, analyse(p))
