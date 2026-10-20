from benchmarks.prepare_medquad_candidates import in_scope


def test_medquad_topics_are_not_matched_inside_rare_disease_or_unrelated_labels():
    assert in_scope("Attention Deficit-Hyperactivity Disorder")
    assert in_scope("Post-Traumatic Stress Disorder")
    assert in_scope("Depression")
    assert not in_scope("Environmental Health")
    assert not in_scope("Cataracts, ataxia, short stature, and mental retardation")
    assert not in_scope("Antidepressants")
    assert not in_scope("Parasites - African Trypanosomiasis (also known as Sleeping Sickness)")
