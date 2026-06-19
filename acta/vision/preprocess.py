"""Multimodal visual preprocessing: dynamic patch splitting + pixel shuffle.

These are the two transforms that modern Vision-Language Models (InternVL,
LLaVA-style) use to feed high-resolution imagery into a transformer at a
manageable token budget:

* **Patch splitting** — tile an image into an aspect-ratio-optimal grid of
  fixed-size patches (plus an optional global thumbnail), so detail is preserved
  without forcing a single low-resolution resize.
* **Pixel shuffle** — fold a ``factor x factor`` neighborhood of vision tokens
  into the channel dimension, cutting the visual token count by ``factor**2``
  while keeping the information.

Everything here is pure Python and operates on frame *geometry* (and optional
token grids), so it runs with zero heavy dependencies and is fully testable.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Sequence


@dataclass(slots=True)
class PreprocessConfig:
    patch_size: int = 448
    min_patches: int = 1
    max_patches: int = 12
    use_thumbnail: bool = True
    pixel_shuffle_scale: float = 0.5  # 0.5 → merge 2x2 token blocks
    hidden_size: int = 64

    def __post_init__(self) -> None:
        if self.patch_size <= 0:
            raise ValueError("patch_size must be positive")
        if self.min_patches < 1 or self.max_patches < self.min_patches:
            raise ValueError("require 1 <= min_patches <= max_patches")
        if not (0.0 < self.pixel_shuffle_scale <= 1.0):
            raise ValueError("pixel_shuffle_scale must be in (0, 1]")

    @property
    def shuffle_factor(self) -> int:
        """Integer down-sampling factor implied by the shuffle scale."""
        return max(1, round(1.0 / self.pixel_shuffle_scale))


@dataclass(slots=True)
class PatchBox:
    index: int
    row: int
    col: int
    x: int
    y: int
    width: int
    height: int
    is_thumbnail: bool = False

    def to_dict(self) -> dict[str, int | bool]:
        return {
            "index": self.index,
            "row": self.row,
            "col": self.col,
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
            "is_thumbnail": self.is_thumbnail,
        }


@dataclass(slots=True)
class PatchPlan:
    grid_cols: int
    grid_rows: int
    patch_size: int
    target_width: int
    target_height: int
    patches: list[PatchBox] = field(default_factory=list)

    @property
    def tile_count(self) -> int:
        return self.grid_cols * self.grid_rows

    def to_dict(self) -> dict[str, object]:
        return {
            "grid_cols": self.grid_cols,
            "grid_rows": self.grid_rows,
            "tile_count": self.tile_count,
            "patch_size": self.patch_size,
            "target_width": self.target_width,
            "target_height": self.target_height,
            "patches": [p.to_dict() for p in self.patches],
        }


def candidate_aspect_ratios(min_patches: int, max_patches: int) -> list[tuple[int, int]]:
    """All (cols, rows) grids whose tile count is within [min, max]."""
    ratios = {
        (cols, rows)
        for n in range(min_patches, max_patches + 1)
        for cols, rows in itertools.product(range(1, n + 1), repeat=2)
        if min_patches <= cols * rows <= max_patches and cols * rows == n
    }
    return sorted(ratios, key=lambda cr: (cr[0] * cr[1], cr[0]))


def find_closest_aspect_ratio(
    width: int,
    height: int,
    *,
    patch_size: int,
    min_patches: int,
    max_patches: int,
) -> tuple[int, int]:
    """Pick the (cols, rows) grid best matching the image aspect ratio.

    Ties on aspect-ratio distance are broken toward the grid that better fills
    the original area (the InternVL heuristic).
    """
    if width <= 0 or height <= 0:
        raise ValueError("width/height must be positive")
    aspect_ratio = width / height
    area = width * height
    best: tuple[int, int] = (1, 1)
    best_diff = math.inf
    for cols, rows in candidate_aspect_ratios(min_patches, max_patches):
        target_ratio = cols / rows
        diff = abs(aspect_ratio - target_ratio)
        if diff < best_diff:
            best_diff = diff
            best = (cols, rows)
        elif diff == best_diff:
            # Prefer the grid whose capacity is closer to the original area.
            capacity = patch_size * patch_size * cols * rows
            best_capacity = patch_size * patch_size * best[0] * best[1]
            if abs(capacity - area) < abs(best_capacity - area):
                best = (cols, rows)
    return best


def split_into_patches(
    width: int,
    height: int,
    config: PreprocessConfig | None = None,
) -> PatchPlan:
    """Compute the dynamic patch grid for a frame of the given geometry."""
    cfg = config or PreprocessConfig()
    cols, rows = find_closest_aspect_ratio(
        width,
        height,
        patch_size=cfg.patch_size,
        min_patches=cfg.min_patches,
        max_patches=cfg.max_patches,
    )
    target_w = cfg.patch_size * cols
    target_h = cfg.patch_size * rows
    patches: list[PatchBox] = []
    index = 0
    for row in range(rows):
        for col in range(cols):
            patches.append(
                PatchBox(
                    index=index,
                    row=row,
                    col=col,
                    x=col * cfg.patch_size,
                    y=row * cfg.patch_size,
                    width=cfg.patch_size,
                    height=cfg.patch_size,
                )
            )
            index += 1
    if cfg.use_thumbnail and (cols * rows) > 1:
        patches.append(
            PatchBox(
                index=index,
                row=-1,
                col=-1,
                x=0,
                y=0,
                width=cfg.patch_size,
                height=cfg.patch_size,
                is_thumbnail=True,
            )
        )
    return PatchPlan(
        grid_cols=cols,
        grid_rows=rows,
        patch_size=cfg.patch_size,
        target_width=target_w,
        target_height=target_h,
        patches=patches,
    )


def pixel_shuffle(
    tokens: Sequence[Sequence[Sequence[float]]],
    *,
    scale: float = 0.5,
) -> list[list[list[float]]]:
    """Space-to-depth pixel shuffle on a ``(H, W, C)`` token grid.

    Folds a ``factor x factor`` neighborhood (``factor = round(1/scale)``) into
    the channel dimension, returning a ``(H//factor, W//factor, C*factor**2)``
    grid. This is the standard VLM trick to reduce the visual token count.
    """
    if not tokens or not tokens[0] or not tokens[0][0]:
        raise ValueError("tokens must be a non-empty (H, W, C) grid")
    factor = max(1, round(1.0 / scale))
    h = len(tokens)
    w = len(tokens[0])
    c = len(tokens[0][0])
    if h % factor or w % factor:
        raise ValueError(
            f"grid {h}x{w} not divisible by shuffle factor {factor}; "
            "pad or resize before pixel shuffle"
        )
    out_h, out_w = h // factor, w // factor
    out: list[list[list[float]]] = []
    for oy in range(out_h):
        row: list[list[float]] = []
        for ox in range(out_w):
            merged: list[float] = []
            for dy in range(factor):
                for dx in range(factor):
                    merged.extend(tokens[oy * factor + dy][ox * factor + dx])
            row.append(merged)
        out.append(row)
    assert len(out[0][0]) == c * factor * factor
    return out


def visual_token_count(plan: PatchPlan, config: PreprocessConfig | None = None) -> int:
    """Visual tokens emitted for a plan after patching + pixel shuffle.

    Each tile is encoded into a ``g x g`` grid of patch tokens (``g`` =
    ``patch_size / 14`` floor, the canonical ViT patch granularity) and then
    reduced by ``shuffle_factor**2`` via pixel shuffle.
    """
    cfg = config or PreprocessConfig()
    vit_patch = 14
    g = max(1, cfg.patch_size // vit_patch)
    factor = cfg.shuffle_factor
    g_shuffled = max(1, g // factor)
    tokens_per_tile = g_shuffled * g_shuffled
    return tokens_per_tile * len(plan.patches)
