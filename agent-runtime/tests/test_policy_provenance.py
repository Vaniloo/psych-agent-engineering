from training.train_decisions import fit_policy, label_provenance


def test_programmatic_review_marker_does_not_claim_independent_gold():
    rows = [
        {
            "splitGroup": "one",
            "reviewed": True,
            "reviewedBy": "programmatic_scenario_contract",
            "annotationStatus": "programmatic",
            "validationScope": "synthetic_domain_curriculum_not_clinical",
        }
    ] * 8
    provenance = label_provenance({"validation": rows})
    assert provenance["independentReviewVerified"] is False
    assert provenance["splits"]["validation"]["records"] == 8
    assert provenance["splits"]["validation"]["splitGroups"] == 1
    assert provenance["splits"]["validation"]["annotationStatus"] == {"programmatic": 8}
    # Even a perfectly separable synthetic holdout is not human-reviewed gold.
    samples = [({"yes": 0.99, "no": 0.01}, "yes")] * 100
    policy = fit_policy(samples, samples, False)
    assert policy["enabled"]
    assert policy["validationScope"] == "project_policy_holdout_not_independent_review"
