import numpy as np
import pytest

from training.head_span_diagnostics import align, replace_head, effect


def test_head_patch_uses_config_width_and_preserves_other_heads_and_source():
    x=np.arange(2*4*12).reshape(2,4,12);before=x.copy()
    result=replace_head(x,[1,2],np.zeros((2,4)),1,4,3)
    assert np.array_equal(x,before)
    assert np.array_equal(result[0,1,:4],x[0,1,:4])
    assert np.array_equal(result[0,1,8:],x[0,1,8:])
    assert not result[0,1,4:8].any()
    with pytest.raises(ValueError):replace_head(x,[1,2],np.zeros((2,3)),1,3,3)


def test_query_and_document_role_spans_are_separate_despite_repeated_words():
    prompt='<Query>: "I email whom?" <Document>: "My tutor emailed me." suffix'
    row={'id':'r','task':'relevance','state':{'query':'I email whom?','candidate':{'content':'My tutor emailed me.'}},
         'spans':{'agent':{'field':'document','text':'My tutor'},'query_agent':{'field':'query','text':'I'}}}
    offsets=[(i,i+1) for i in range(len(prompt))]
    result=align(prompt,row,offsets,[1]*len(prompt))
    assert result['query_agent'][0]<prompt.index('<Document>:')<result['agent'][0]
    assert not set(result['other'])&set(result['agent'])


def test_no_label_zero_margin_is_wrong_and_masks_keep_matched_denominators():
    pair={'id':'f','language':'en','category':'role_binding','label':'no'}
    r=effect([pair],[1.],[0.])
    assert r['all']['correctAfter']==0
    assert effect([pair],[1.],[-2.],[False])['all']['pairs']==0
