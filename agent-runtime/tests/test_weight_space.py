import numpy as np
import pytest

from training.weight_space import (difference_squared, norm_squared, principal_change,
                                   projected_b, make_patch, case_id)


def factors():
    rng = np.random.default_rng(17)
    return [rng.normal(size=s) for s in [(3, 11), (13, 3), (3, 11), (13, 3)]]


def test_effective_delta_norm_matches_dense_and_is_basis_invariant():
    a, b, a0, b0 = factors()
    assert norm_squared(a, b) == pytest.approx(np.linalg.norm(b @ a)**2)
    assert difference_squared(a, b, a0, b0) == pytest.approx(np.linalg.norm(b @ a-b0 @ a0)**2)
    change = np.diag([2., 3., .5])
    assert difference_squared(change @ a, b @ np.linalg.inv(change), a0, b0) == pytest.approx(
        difference_squared(a, b, a0, b0))


def test_small_core_principal_direction_matches_full_dense_svd():
    a, b, a0, b0 = factors()
    u, energy = principal_change(a, b, a0, b0)
    dense_u, singular, _ = np.linalg.svd(b @ a-b0 @ a0, full_matrices=False)
    assert abs(u @ dense_u[:, 0]) == pytest.approx(1.)
    assert energy == pytest.approx(singular[0]**2 / np.sum(singular**2))
    edited = projected_b(b, u)
    assert np.linalg.norm(u @ edited) < 1e-10
    assert np.allclose(projected_b(b, u, 0), b)


def test_random_control_matches_effective_perturbation_and_preserves_sources():
    a, b, a0, b0 = factors()
    key = 'base_model.model.model.layers.1.mlp.up_proj.lora_A.weight'
    bk = key.replace('.lora_A.', '.lora_B.')
    current, parent = {key:a.copy(),bk:b.copy()}, {key:a0.copy(),bk:b0.copy()}
    program = {'kind':'principal_projection','key':key,'strength':.5}
    target = make_patch(current, parent, program)
    control = make_patch(current, parent, {**program,'kind':'random_projection','seed':1104})
    assert norm_squared(a, target[bk]-b) == pytest.approx(norm_squared(a, control[bk]-b))
    assert np.array_equal(current[key], a) and np.array_equal(current[bk], b)
    assert make_patch(current, parent, {'kind':'original'}) == {}
    rollback = make_patch(current, parent, {'kind':'rollback','layers':[1]})
    assert np.array_equal(rollback[key], a0) and np.array_equal(rollback[bk], b0)
    assert not np.shares_memory(rollback[key], parent[key])


def test_frozen_course_rows_need_no_publisher_id_and_labels_do_not_define_identity():
    row = {"task":"grounded", "state":{"source":{"role":"user","content":"我喜欢读书。"}},
           "label":"yes"}
    assert len(case_id(row)) == 64
    assert case_id(row) == case_id({**row,"label":"no"})
    assert case_id({**row,"id":"publisher-1"}) == "publisher-1"


def test_module_scaling_changes_only_selected_mlp_B_without_mutating_input():
    keys = [f"base.layers.{layer}.{module}.lora_{factor}.weight"
            for layer in [10, 23, 26] for module in ["mlp.up_proj", "self_attn.o_proj"]
            for factor in ["A", "B"]]
    current = {key: np.ones((2, 2), dtype=np.float32) for key in keys}
    patch = make_patch(current, {}, {"kind":"scale_modules", "layers":[23,26], "factor":.8})
    assert len(patch) == 2
    assert all(".mlp." in k and ".lora_B." in k for k in patch)
    assert all(np.allclose(v, .8) for v in patch.values())
    assert all(np.all(v == 1) for v in current.values())
    with pytest.raises(ValueError, match="no matching"):
        make_patch(current, {}, {"kind":"scale_modules", "layers":[99], "factor":.8})
