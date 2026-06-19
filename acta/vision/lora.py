"""Visual Instruction Tuning with LoRA — dependency-free reference adapter.

A faithful, tiny implementation of Low-Rank Adaptation usable entirely offline:
a frozen base projection is adapted by a low-rank residual ``ΔW = (α/r)·B·A``
where ``A ∈ R^{r×in}`` and ``B ∈ R^{out×r}``. ``B`` is zero-initialized so a fresh
adapter is a no-op, exactly like real LoRA. A small SGD loop lets the system
*visually instruction-tune* the residual from (feature, target) pairs without
PyTorch. Adapters serialize to JSON so they can be stored and reloaded.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

Vector = list[float]
Matrix = list[list[float]]


@dataclass(slots=True)
class LoRAConfig:
    name: str = "vit_default"
    rank: int = 8
    alpha: float = 16.0
    target_module: str = "vision_proj"
    seed: int = 1234

    def __post_init__(self) -> None:
        if self.rank <= 0:
            raise ValueError("rank must be positive")
        if self.alpha <= 0:
            raise ValueError("alpha must be positive")

    @property
    def scaling(self) -> float:
        return self.alpha / self.rank


class _LCG:
    """Tiny deterministic PRNG (no numpy/random global state dependency)."""

    def __init__(self, seed: int) -> None:
        self._state = (seed & 0x7FFFFFFF) or 1

    def uniform(self) -> float:
        self._state = (1103515245 * self._state + 12345) & 0x7FFFFFFF
        return self._state / 0x7FFFFFFF

    def normal(self, std: float) -> float:
        # Box–Muller from two uniforms.
        u1 = max(self.uniform(), 1e-9)
        u2 = self.uniform()
        return std * math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)


def _matvec(mat: Matrix, vec: Sequence[float]) -> Vector:
    return [sum(m_ij * vec[j] for j, m_ij in enumerate(row)) for row in mat]


@dataclass(slots=True)
class LoRAAdapter:
    """Low-rank residual adapter over a fixed ``in→out`` projection."""

    config: LoRAConfig
    in_features: int
    out_features: int
    A: Matrix = field(default_factory=list)  # rank x in
    B: Matrix = field(default_factory=list)  # out x rank

    def __post_init__(self) -> None:
        if not self.A:
            rng = _LCG(self.config.seed)
            std = 1.0 / math.sqrt(self.in_features)
            self.A = [
                [rng.normal(std) for _ in range(self.in_features)]
                for _ in range(self.config.rank)
            ]
        if not self.B:
            # Zero init → adapter starts as identity (no-op), as in real LoRA.
            self.B = [[0.0] * self.config.rank for _ in range(self.out_features)]

    @property
    def scaling(self) -> float:
        return self.config.scaling

    def delta(self, x: Sequence[float]) -> Vector:
        """Low-rank residual ``(α/r)·B·(A·x)``."""
        if len(x) != self.in_features:
            raise ValueError(f"expected input dim {self.in_features}, got {len(x)}")
        a = _matvec(self.A, x)
        ba = _matvec(self.B, a)
        return [self.scaling * v for v in ba]

    def apply(self, base_output: Sequence[float], x: Sequence[float]) -> Vector:
        """Adapt a frozen base projection output for input ``x``."""
        d = self.delta(x)
        if len(base_output) != self.out_features:
            raise ValueError("base_output dim mismatch")
        return [b + d[i] for i, b in enumerate(base_output)]

    def to_dict(self) -> dict[str, object]:
        return {
            "config": {
                "name": self.config.name,
                "rank": self.config.rank,
                "alpha": self.config.alpha,
                "target_module": self.config.target_module,
                "seed": self.config.seed,
            },
            "in_features": self.in_features,
            "out_features": self.out_features,
            "A": self.A,
            "B": self.B,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "LoRAAdapter":
        cfg_raw = dict(data["config"])
        config = LoRAConfig(
            name=str(cfg_raw.get("name", "vit_default")),
            rank=int(cfg_raw.get("rank", 8)),
            alpha=float(cfg_raw.get("alpha", 16.0)),
            target_module=str(cfg_raw.get("target_module", "vision_proj")),
            seed=int(cfg_raw.get("seed", 1234)),
        )
        return cls(
            config=config,
            in_features=int(data["in_features"]),
            out_features=int(data["out_features"]),
            A=[[float(v) for v in row] for row in data["A"]],
            B=[[float(v) for v in row] for row in data["B"]],
        )


class VisualInstructionTuner:
    """Fit a LoRA adapter's residual to (feature → target) pairs via SGD."""

    def __init__(self, adapter: LoRAAdapter) -> None:
        self.adapter = adapter

    def loss(self, samples: Sequence[tuple[Sequence[float], Sequence[float]]]) -> float:
        if not samples:
            return 0.0
        total = 0.0
        for x, y in samples:
            d = self.adapter.delta(x)
            total += sum((d[i] - y[i]) ** 2 for i in range(len(y))) / len(y)
        return total / len(samples)

    def fit(
        self,
        samples: Sequence[tuple[Sequence[float], Sequence[float]]],
        *,
        epochs: int = 50,
        lr: float = 0.05,
    ) -> dict[str, float]:
        """Train ``A`` and ``B`` to map features to residual targets.

        Returns the loss before and after training so callers (and tests) can
        assert that instruction tuning actually reduced the objective.
        """
        if not samples:
            return {"initial_loss": 0.0, "final_loss": 0.0, "epochs": 0}
        adapter = self.adapter
        s = adapter.scaling
        r = adapter.config.rank
        out_f = adapter.out_features
        initial = self.loss(samples)
        n = len(samples)
        for _ in range(max(0, epochs)):
            for x, y in samples:
                a = _matvec(adapter.A, x)  # r
                pred = [s * sum(adapter.B[o][k] * a[k] for k in range(r)) for o in range(out_f)]
                err = [pred[o] - y[o] for o in range(out_f)]
                grad_b = [
                    [(2.0 / out_f) * err[o] * s * a[k] for k in range(r)] for o in range(out_f)
                ]
                grad_a_vec = [
                    (2.0 / out_f) * s * sum(err[o] * adapter.B[o][k] for o in range(out_f))
                    for k in range(r)
                ]
                for o in range(out_f):
                    for k in range(r):
                        adapter.B[o][k] -= (lr / n) * grad_b[o][k]
                for k in range(r):
                    gk = grad_a_vec[k]
                    if gk:
                        for j in range(adapter.in_features):
                            adapter.A[k][j] -= (lr / n) * gk * x[j]
        final = self.loss(samples)
        return {"initial_loss": initial, "final_loss": final, "epochs": float(max(0, epochs))}


class LoRARegistry:
    """In-memory registry of named adapters with optional JSON persistence."""

    def __init__(self, store_dir: Path | None = None) -> None:
        self._adapters: dict[str, LoRAAdapter] = {}
        self._active: str | None = None
        self._store_dir = store_dir

    def register(self, adapter: LoRAAdapter, *, activate: bool = True) -> None:
        self._adapters[adapter.config.name] = adapter
        if activate or self._active is None:
            self._active = adapter.config.name

    def get(self, name: str) -> LoRAAdapter | None:
        return self._adapters.get(name)

    def active(self) -> LoRAAdapter | None:
        return self._adapters.get(self._active) if self._active else None

    def names(self) -> list[str]:
        return list(self._adapters)

    def activate(self, name: str) -> None:
        if name not in self._adapters:
            raise KeyError(name)
        self._active = name

    def save(self, name: str) -> Path | None:
        if self._store_dir is None or name not in self._adapters:
            return None
        self._store_dir.mkdir(parents=True, exist_ok=True)
        path = self._store_dir / f"{name}.json"
        path.write_text(json.dumps(self._adapters[name].to_dict()), encoding="utf-8")
        return path

    def load(self, name: str) -> LoRAAdapter | None:
        if self._store_dir is None:
            return None
        path = self._store_dir / f"{name}.json"
        if not path.exists():
            return None
        adapter = LoRAAdapter.from_dict(json.loads(path.read_text(encoding="utf-8")))
        self.register(adapter, activate=False)
        return adapter
