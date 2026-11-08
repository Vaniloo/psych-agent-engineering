import numpy as np
import pytest

from training.activation_diagnostics import cloned_slot, token_positions, compare_effect


def test_cloned_patch_preserves_source_and_tuple_auxiliary_output():
    original=np.arange(24).reshape(2,3,4)
    before=original.copy()
    aux=object()
    result=cloned_slot((original,aux),[1,2],np.zeros((2,4)))
    assert result[1] is aux and np.array_equal(original,before)
    assert np.array_equal(result[0][0,1],np.zeros(4))
    assert np.array_equal(result[0][1,0],before[1,0])


def test_semantic_alignment_ignores_padding_and_repeated_text_before_document():
    prompt='read AB <Document>: AB end'
    offsets=[(0,0)]+[(i,i+1) for i in range(len(prompt))]
    mask=[0]+[1]*len(prompt)
    row={'task':'write_worthy','state':{'source':{'role':'user','content':'AB'}}}
    positions=token_positions(prompt,row,offsets,mask)
    begin=prompt.index('AB',prompt.index('<Document>:'))
    assert positions['source_first']==begin+1
    assert positions['source_end']==begin+2
    assert positions['last']==len(prompt)


def test_recovery_sign_and_small_denominator_are_handled_for_no_label():
    pairs=[{'family':'f','task':'relevance','label':'no','donorKind':'same_semantics_paraphrase'}]
    result=compare_effect(pairs,[4.],[1.],[-2.])['cases'][0]
    assert result['signedDelta']==3 and result['normalizedRecovery']==pytest.approx(.5)
    assert not result['correctAfter']
    result=compare_effect(pairs,[.2],[.1],[-.1])['cases'][0]
    assert result['normalizedRecovery'] is None


def test_zero_logit_margin_uses_yes_tie_break_not_signed_zero_as_correct_no():
    pair={'family':'f','task':'relevance','label':'no','donorKind':'same_semantics_paraphrase'}
    case=compare_effect([pair],[0.],[0.],[-2.])['cases'][0]
    assert not case['correctBefore'] and not case['correctAfter']
