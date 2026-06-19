from __future__ import annotations

import pytest

from acta.vision.preprocess import (
    PreprocessConfig,
    candidate_aspect_ratios,
    find_closest_aspect_ratio,
    pixel_shuffle,
    split_into_patches,
    visual_token_count,
)


def test_config_validation():
    with pytest.raises(ValueError):
        PreprocessConfig(patch_size=0)
    with pytest.raises(ValueError):
        PreprocessConfig(min_patches=0)
    with pytest.raises(ValueError):
        PreprocessConfig(max_patches=2, min_patches=3)
    with pytest.raises(ValueError):
        PreprocessConfig(pixel_shuffle_scale=2.0)


def test_shuffle_factor():
    assert PreprocessConfig(pixel_shuffle_scale=0.5).shuffle_factor == 2
    assert PreprocessConfig(pixel_shuffle_scale=1.0).shuffle_factor == 1
    assert PreprocessConfig(pixel_shuffle_scale=0.25).shuffle_factor == 4


def test_candidate_aspect_ratios_bounds():
    ratios = candidate_aspect_ratios(1, 6)
    assert (1, 1) in ratios
    assert all(1 <= c * r <= 6 for c, r in ratios)


def test_find_closest_aspect_ratio_landscape():
    cols, rows = find_closest_aspect_ratio(
        1920, 1080, patch_size=448, min_patches=1, max_patches=12
    )
    assert cols >= rows


def test_find_closest_aspect_ratio_square():
    cols, rows = find_closest_aspect_ratio(
        500, 500, patch_size=448, min_patches=1, max_patches=12
    )
    assert cols == rows


def test_find_closest_aspect_ratio_invalid():
    with pytest.raises(ValueError):
        find_closest_aspect_ratio(0, 100, patch_size=448, min_patches=1, max_patches=4)


def test_split_into_patches_grid_and_thumbnail():
    cfg = PreprocessConfig(patch_size=448, max_patches=12, use_thumbnail=True)
    plan = split_into_patches(1280, 720, cfg)
    assert plan.tile_count == plan.grid_cols * plan.grid_rows
    if plan.tile_count > 1:
        assert any(p.is_thumbnail for p in plan.patches)
        assert len(plan.patches) == plan.tile_count + 1
    assert plan.target_width == plan.grid_cols * 448


def test_split_single_tile_no_thumbnail():
    cfg = PreprocessConfig(patch_size=448, min_patches=1, max_patches=1)
    plan = split_into_patches(500, 500, cfg)
    assert plan.tile_count == 1
    assert not any(p.is_thumbnail for p in plan.patches)


def test_patch_plan_to_dict():
    plan = split_into_patches(800, 600)
    d = plan.to_dict()
    assert set(d) >= {"grid_cols", "grid_rows", "tile_count", "patches"}
    assert isinstance(d["patches"], list) and d["patches"]


def test_pixel_shuffle_reduces_tokens_and_grows_channels():
    grid = [[[float(c) for c in range(8)] for _ in range(4)] for _ in range(4)]
    out = pixel_shuffle(grid, scale=0.5)
    assert len(out) == 2 and len(out[0]) == 2
    assert len(out[0][0]) == 8 * 4


def test_pixel_shuffle_requires_divisible_grid():
    grid = [[[1.0] for _ in range(3)] for _ in range(3)]
    with pytest.raises(ValueError):
        pixel_shuffle(grid, scale=0.5)


def test_pixel_shuffle_empty():
    with pytest.raises(ValueError):
        pixel_shuffle([], scale=0.5)


def test_pixel_shuffle_identity_factor_one():
    grid = [[[1.0, 2.0]]]
    out = pixel_shuffle(grid, scale=1.0)
    assert out == grid


def test_visual_token_count_drops_with_shuffle():
    cfg_full = PreprocessConfig(patch_size=448, max_patches=4, pixel_shuffle_scale=1.0)
    cfg_shuffled = PreprocessConfig(patch_size=448, max_patches=4, pixel_shuffle_scale=0.5)
    plan = split_into_patches(900, 900, cfg_full)
    plan2 = split_into_patches(900, 900, cfg_shuffled)
    assert visual_token_count(plan, cfg_full) > visual_token_count(plan2, cfg_shuffled)
