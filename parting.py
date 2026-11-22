from collections import deque

import numpy as np
from manifold3d import Manifold, Mesh64, OpType


# Rotate the chosen positive split axis to +Z for the height field.
TO_LOCAL = {"X": (0, -90, 0), "Y": (90, 0, 0), "Z": (0, 0, 0)}


def contoured_parting(cavity, axis, split_at, wall, guide=None):
    rotation = TO_LOCAL[axis]
    local_cavity = cavity.rotate(rotation)
    solid = local_cavity if guide is None else guide.rotate(rotation)
    lower, upper = np.asarray(local_cavity.bounding_box()).reshape(2, 3)
    padding = wall + 1
    # ponytail: a regular, bounded ray grid keeps the operation practical on
    # dense imports. Adaptive sampling could track finer edges.
    counts = np.clip(np.ceil((upper[:2] - lower[:2] + 2 * padding) / 0.8).astype(int) + 1, 16, 256)
    xs, ys = [np.linspace(lo - padding, hi + padding, count)
              for lo, hi, count in zip(lower, upper, counts)]
    heights = np.full((len(ys), len(xs)), np.nan)
    hidden = False
    queue = deque()
    distance = np.full(heights.shape, -1, dtype=int)
    tolerance = solid.get_tolerance()
    for j, y in enumerate(ys):
        for i, x in enumerate(xs):
            hits = solid.ray_cast((x, y, lower[2] - 1), (x, y, upper[2] + 1))
            if len(hits) < 2:
                continue
            levels = np.array([hit.position[2] for hit in hits])
            levels = levels[np.r_[True, np.diff(levels) > tolerance]]
            if len(levels) < 2 or len(levels) % 2:
                continue
            hidden |= len(levels) > 2
            intervals = levels.reshape(-1, 2)
            heights[j, i] = intervals[np.diff(intervals, axis=1).argmax()].mean()
            distance[j, i] = 0
            queue.append((j, i))
    if not queue:
        raise ValueError("No contour could be sampled. Use a flat split or change the front direction.")

    # Extend the parting face beyond the silhouette and blend to the flat
    # seam near the outside faces of the rectangular mold.
    ny, nx = heights.shape
    while queue:
        j, i = queue.popleft()
        for row, col in ((j - 1, i), (j + 1, i), (j, i - 1), (j, i + 1)):
            if 0 <= row < ny and 0 <= col < nx and distance[row, col] < 0:
                heights[row, col] = heights[j, i]
                distance[row, col] = distance[j, i] + 1
                queue.append((row, col))
    step = min(xs[1] - xs[0], ys[1] - ys[0])
    blend = np.minimum(1, distance * step / wall)
    heights += blend * (split_at - heights)
    heights[[0, -1], :] = split_at
    heights[:, [0, -1]] = split_at

    x_grid, y_grid = np.meshgrid(xs, ys)
    bottom = np.column_stack((x_grid.ravel(), y_grid.ravel(), heights.ravel()))
    top = bottom.copy()
    top[:, 2] = upper[2] + padding
    indices = np.arange(len(bottom), dtype=np.uint64).reshape(heights.shape)
    a, b = indices[:-1, :-1].ravel(), indices[:-1, 1:].ravel()
    c, d = indices[1:, 1:].ravel(), indices[1:, :-1].ravel()
    cap = np.concatenate((np.column_stack((a, b, c)), np.column_stack((a, c, d))))
    rim = np.concatenate((indices[0, :-1], indices[:-1, -1], indices[-1, :0:-1], indices[:0:-1, 0]))
    next_rim = np.roll(rim, -1)
    n = len(bottom)
    triangles = np.concatenate((cap[:, ::-1], cap + n,
                                np.column_stack((rim, next_rim, next_rim + n)),
                                np.column_stack((rim, next_rim + n, rim + n))))
    cutter = Manifold(Mesh64(np.vstack((bottom, top)), np.ascontiguousarray(triangles))).simplify()
    if cutter.is_empty():
        raise ValueError("The contour did not produce a usable parting surface. Try a flat split.")
    return cutter.rotate(tuple(-angle for angle in rotation)), hidden


def split_contoured(solid, cutter):
    halves = solid.split(cutter)
    groups = [sorted(half.decompose(), key=lambda part: part.volume(), reverse=True) for half in halves]
    if any(not group or any(part.volume() <= 0 for part in group) for group in groups):
        raise ValueError("The contoured seam could not divide the mold into two pieces. Try another direction or a flat split.")
    # A sampled seam can isolate islands at small overhangs. Attach each to
    # the opposing half it touches without changing the assembled cavity.
    return tuple(Manifold.batch_boolean([group[0], *groups[1 - index][1:]], OpType.Add)
                 for index, group in enumerate(groups))
