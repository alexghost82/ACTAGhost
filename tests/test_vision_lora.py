from __future__ import annotations

import pytest

from acta.vision.lora import (
    LoRAAdapter,
    LoRAConfig,
    LoRARegistry,
    VisualInstructionTuner,
)


def test_config_validation():
    with pytest.raises(ValueError):
        LoRAConfig(rank=0)
    with pytest.raises(ValueError):
        LoRAConfig(alpha=0)
    assert LoRAConfig(rank=8, alpha=16).scaling == 2.0


def test_fresh_adapter_is_noop():
    adapter = LoRAAdapter(LoRAConfig(rank=4, alpha=8), in_features=5, out_features=3)
    assert adapter.delta([1.0, 2.0, 3.0, 4.0, 5.0]) == [0.0, 0.0, 0.0]


def test_apply_adds_residual():
    adapter = LoRAAdapter(LoRAConfig(rank=2, alpha=4), in_features=3, out_features=2)
    base = [1.0, 1.0]
    out = adapter.apply(base, [0.5, 0.5, 0.5])
    assert out == base


def test_delta_dimension_mismatch():
    adapter = LoRAAdapter(LoRAConfig(rank=2, alpha=4), in_features=3, out_features=2)
    with pytest.raises(ValueError):
        adapter.delta([1.0])
    with pytest.raises(ValueError):
        adapter.apply([1.0], [1.0, 2.0, 3.0])


def test_visual_instruction_tuning_reduces_loss():
    adapter = LoRAAdapter(LoRAConfig(rank=4, alpha=8, seed=7), in_features=6, out_features=3)
    tuner = VisualInstructionTuner(adapter)
    samples = [
        ([1.0, 0, 0, 0, 0, 0], [0.5, -0.2, 0.1]),
        ([0, 1.0, 0, 0, 0, 0], [0.1, 0.3, -0.4]),
        ([0, 0, 1.0, 0, 0, 0], [-0.3, 0.2, 0.2]),
    ]
    report = tuner.fit(samples, epochs=300, lr=0.1)
    assert report["final_loss"] < report["initial_loss"]
    assert report["final_loss"] < 1e-3


def test_tuner_handles_empty():
    adapter = LoRAAdapter(LoRAConfig(), in_features=4, out_features=2)
    tuner = VisualInstructionTuner(adapter)
    assert tuner.fit([], epochs=10)["epochs"] == 0
    assert tuner.loss([]) == 0.0


def test_adapter_serialization_roundtrip():
    adapter = LoRAAdapter(LoRAConfig(name="cam1", rank=3, alpha=6), in_features=4, out_features=2)
    tuner = VisualInstructionTuner(adapter)
    tuner.fit([([1.0, 0, 0, 0], [0.2, 0.4])], epochs=20, lr=0.1)
    restored = LoRAAdapter.from_dict(adapter.to_dict())
    assert restored.config.name == "cam1"
    assert restored.delta([1.0, 0, 0, 0]) == pytest.approx(adapter.delta([1.0, 0, 0, 0]))


def test_registry_register_activate_and_persist(tmp_path):
    reg = LoRARegistry(store_dir=tmp_path / "lora")
    a = LoRAAdapter(LoRAConfig(name="a"), in_features=3, out_features=2)
    b = LoRAAdapter(LoRAConfig(name="b"), in_features=3, out_features=2)
    reg.register(a)
    reg.register(b, activate=False)
    assert reg.active().config.name == "a"
    assert set(reg.names()) == {"a", "b"}
    reg.activate("b")
    assert reg.active().config.name == "b"
    with pytest.raises(KeyError):
        reg.activate("missing")
    path = reg.save("a")
    assert path is not None and path.exists()

    reg2 = LoRARegistry(store_dir=tmp_path / "lora")
    loaded = reg2.load("a")
    assert loaded is not None and loaded.config.name == "a"
    assert reg2.load("nope") is None


def test_registry_save_without_store_dir():
    reg = LoRARegistry()
    reg.register(LoRAAdapter(LoRAConfig(name="x"), in_features=2, out_features=2))
    assert reg.save("x") is None
    assert reg.load("x") is None
