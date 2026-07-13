"""Geometry helpers for walls/cover. Pure functions, no state, deterministic.

Walls are axis-aligned rectangles given as (x, y, w, h) with (x, y) the top-left
corner. Used for: robot push-out, rocket impact, and laser line-of-sight.
"""

import math


def _closest_point_on_rect(px, py, rect):
    rx, ry, rw, rh = rect
    cx = min(max(px, rx), rx + rw)
    cy = min(max(py, ry), ry + rh)
    return cx, cy


def point_in_rect(px, py, rect):
    rx, ry, rw, rh = rect
    return rx <= px <= rx + rw and ry <= py <= ry + rh


def circle_pushout(px, py, r, rect):
    """If a circle of radius r at (px,py) overlaps rect, return the corrected
    centre that just clears it; otherwise return (px, py) unchanged."""
    cx, cy = _closest_point_on_rect(px, py, rect)
    dx, dy = px - cx, py - cy
    d2 = dx * dx + dy * dy
    if d2 >= r * r:
        return px, py
    d = math.sqrt(d2)
    if d < 1e-9:
        # centre is inside the rect: push out along the shallowest axis.
        rx, ry, rw, rh = rect
        left, right = px - rx, (rx + rw) - px
        top, bottom = py - ry, (ry + rh) - py
        m = min(left, right, top, bottom)
        if m == left:
            return rx - r, py
        if m == right:
            return rx + rw + r, py
        if m == top:
            return px, ry - r
        return px, ry + rh + r
    nx, ny = dx / d, dy / d
    return cx + nx * r, cy + ny * r


def _seg_intersects_seg(ax, ay, bx, by, cx, cy, dx, dy):
    def ccw(x1, y1, x2, y2, x3, y3):
        return (y3 - y1) * (x2 - x1) - (y2 - y1) * (x3 - x1)
    d1 = ccw(cx, cy, dx, dy, ax, ay)
    d2 = ccw(cx, cy, dx, dy, bx, by)
    d3 = ccw(ax, ay, bx, by, cx, cy)
    d4 = ccw(ax, ay, bx, by, dx, dy)
    if ((d1 > 0) != (d2 > 0)) and ((d3 > 0) != (d4 > 0)):
        return True
    return False


def seg_intersects_rect(x1, y1, x2, y2, rect):
    """True if the segment (x1,y1)-(x2,y2) touches/crosses rect. Used for laser
    line-of-sight (blocked = no hit) and rocket step-collision."""
    if point_in_rect(x1, y1, rect) or point_in_rect(x2, y2, rect):
        return True
    rx, ry, rw, rh = rect
    corners = [(rx, ry), (rx + rw, ry), (rx + rw, ry + rh), (rx, ry + rh)]
    for i in range(4):
        ex, ey = corners[i]
        fx, fy = corners[(i + 1) % 4]
        if _seg_intersects_seg(x1, y1, x2, y2, ex, ey, fx, fy):
            return True
    return False


def los_blocked(x1, y1, x2, y2, walls):
    """True if any wall blocks the straight line between the two points."""
    for w in walls:
        if seg_intersects_rect(x1, y1, x2, y2, w):
            return True
    return False
