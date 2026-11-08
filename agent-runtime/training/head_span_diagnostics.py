"""Read-only, role-aligned head/result and attention-contribution diagnostics.

Heads are sliced before o_proj with config.head_dim, not hidden_size/n_heads.
No optimizer, backward pass, parameter patch, model export, or production calls.
"""

import argparse
import hashlib
import json
import math
import time
from pathlib import Path


def sha(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f,'sha256').hexdigest()


def write(path,value):
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    temporary.replace(path)


def stable(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True).encode()).hexdigest()


def correct(margin,label):
    return (margin>=0)==(label=='yes')


def source(row):
    state=row['state']
    return state['candidate' if row['task']=='relevance' else 'source']['content']


def align(prompt,row,offsets,mask):
    """Map predeclared role spans to tokens; never infer roles from word order."""
    def escaped(text):return json.dumps(text,ensure_ascii=False)[1:-1].replace('<','\\u003c').replace('>','\\u003e')
    document=prompt.index('<Document>:')
    body=escaped(source(row));body_start=prompt.index(body,document)
    query_start=None
    if row['task']=='relevance':
        query_start=prompt.index(escaped(row['state']['query']),prompt.index('<Query>:'))
    indices={}
    for key,span in row['spans'].items():
        text=escaped(span['text'])
        field=escaped(row['state']['query']) if span['field']=='query' else body
        start=query_start if span['field']=='query' else body_start
        location=field.find(text)
        if location<0:raise ValueError('missing annotated role span: '+row['id']+'/'+key)
        start+=location;end=start+len(text)
        tokens=[i for i,((a,b),active) in enumerate(zip(offsets,mask,strict=True)) if active and a<end and b>start]
        if not tokens:raise ValueError('unaligned role span')
        indices[key]=tokens
    indices['tail']=[i for i,((a,b),active) in enumerate(zip(offsets,mask,strict=True))
                     if active and a<body_start+len(body) and b>body_start][-1:]
    indices['decision']=[max(i for i,active in enumerate(mask) if active)]
    annotated={i for k,items in indices.items() if k not in {'tail','decision'} for i in items}
    indices['other']=[i for i,((a,b),active) in enumerate(zip(offsets,mask,strict=True))
                      if active and a<body_start+len(body) and b>body_start and i not in annotated]
    return indices


def replace_head(x,positions,values,head,head_dim,n_heads):
    if x.shape[-1]!=head_dim*n_heads:raise ValueError('head width is not config n_heads*head_dim')
    if not 0<=head<n_heads:raise ValueError('invalid head')
    result=x.clone() if hasattr(x,'clone') else x.copy()
    for i,position in enumerate(positions):
        result[i,position,head*head_dim:(head+1)*head_dim]=values[i]
    return result


def effect(pairs,baseline,values,valid=None):
    cases=[]
    for i,(p,a,b) in enumerate(zip(pairs,baseline,values,strict=True)):
        if valid is not None and not valid[i]:continue
        cases.append({'family':p['id'],'language':p['language'],'category':p['category'],'label':p['label'],
                      'baseline':a,'patched':b,'signedDelta':(1 if p['label']=='yes' else -1)*(b-a),
                      'correctBefore':correct(a,p['label']),'correctAfter':correct(b,p['label'])})
    def aggregate(rows):
        return {'pairs':len(rows),'meanSignedDelta':sum(r['signedDelta'] for r in rows)/len(rows) if rows else None,
                'correctBefore':sum(r['correctBefore'] for r in rows),'correctAfter':sum(r['correctAfter'] for r in rows)}
    return {'all':aggregate(cases),'byLanguage':{k:aggregate([r for r in cases if r['language']==k]) for k in ['zh','en']},
            'byCategory':{k:aggregate([r for r in cases if r['category']==k]) for k in ['role_binding','appointment_correction']},'cases':cases}


def run(args):
    args.out.mkdir(parents=True,exist_ok=False)
    import os
    for key,name in [('TMPDIR','tmp'),('CUDA_CACHE_PATH','cuda'),('TRITON_CACHE_DIR','triton'),('TORCHINDUCTOR_CACHE_DIR','inductor')]:
        p=args.out/'cache'/name;p.mkdir(parents=True,exist_ok=True);os.environ[key]=str(p)
    import numpy as np
    import torch
    from peft import PeftModel
    from transformers import AutoTokenizer,AutoModelForCausalLM
    from transformers.models.qwen3.modeling_qwen3 import apply_rotary_pos_emb
    from safetensors.torch import save_file
    from psych_agent_runtime.memory_lifecycle_contract import render,INSTRUCTIONS

    started=time.monotonic();torch.set_num_threads(4)
    report=json.loads((args.checkpoint/'training-report.json').read_text())
    original_sha=sha(args.checkpoint/'adapter/adapter_model.safetensors')
    if original_sha!=report['adapterSha256'] or args.base.name!=report['baseRevision']:raise ValueError('model identity mismatch')
    if stable(INSTRUCTIONS)!=report['datasetManifest']['instructionsSha256']:raise ValueError('instructions changed')
    for name,expected in report['baseWeightsSha256'].items():
        if sha(args.base/name)!=expected:raise ValueError('base hash mismatch')
    if sha(args.challenge)!=report['challengeSha256']:raise ValueError('challenge changed')
    dataset=json.loads(args.cases.read_text());families=dataset['families']
    rows=[r for f in families for r in f['variants']]+dataset['extraBehavior']
    tokenizer=AutoTokenizer.from_pretrained(args.base,padding_side='left',local_files_only=True)

    def encode(batch,alignment=False):
        prompts=[render(r['task'],r['state']) for r in batch]
        tokens=tokenizer(prompts,padding=True,return_tensors='pt',truncation=False,return_offsets_mapping=alignment)
        if tokens.input_ids.shape[1]>2048:raise ValueError('no silent truncation')
        maps=[]
        if alignment:
            offsets=tokens.pop('offset_mapping').tolist();masks=tokens['attention_mask'].tolist()
            maps=[align(p,r,o,m) for p,r,o,m in zip(prompts,batch,offsets,masks,strict=True)]
        return tokens,maps
    aligned_count=0
    for start in range(0,len(rows),args.batch_size):
        encode(rows[start:start+args.batch_size])
    traced=[r for f in families for r in f['variants']]
    for start in range(0,len(traced),args.batch_size):
        _,maps=encode(traced[start:start+args.batch_size],True);aligned_count+=len(maps)
    write(args.out/'preflight.json',{'behaviorRows':len(rows),'roleAlignedRows':aligned_count,'gpuModelLoaded':False})
    protocol={'sourceSha256':sha(Path(__file__)),'casesSha256':sha(args.cases),'adapterSha256':original_sha,
              'challengeSha256':sha(args.challenge),'layer':args.layer,'slots':['agent','relation','patient','tail','decision'],
              'selection':'discovery only; rank paired-minus-unrelated signed delta, positive paired effect, max3 distinct heads',
              'donors':'first rewrite and first unrelated with correct signed margin>=2; no explicit-request or translation donors',
              'edge':'paired or unrelated attention-weighted V contribution difference at one head; non-renormalizing intervention',
              'reconstructionTolerance':.02,'weightsEdited':False,'deploymentEnabled':False,
              'limits':['assistant authored shared templates; not human gold','heads are query heads; GQA KV heads are shared',
                        'field-end token alignment does not prove an isolated circuit','confirmation is exploratory; no weight patch']}
    write(args.out/'protocol.json',protocol)
    if torch.cuda.mem_get_info()[0]<24*1024**3:raise RuntimeError('need 24GiB free; do not evict others')
    torch.cuda.set_per_process_memory_fraction(.24)
    base=AutoModelForCausalLM.from_pretrained(args.base,torch_dtype=torch.bfloat16,attn_implementation='sdpa',local_files_only=True).to('cuda:0')
    model=PeftModel.from_pretrained(base,args.checkpoint/'adapter').eval();model.requires_grad_(False)
    versions={k:p._version for k,p in model.named_parameters()}
    attn=model.base_model.model.model.layers[args.layer].self_attn
    config=model.config;heads=config.num_attention_heads;kv_heads=config.num_key_value_heads;dim=attn.head_dim
    if attn.o_proj.in_features!=heads*dim:raise ValueError('projection head dimensions disagree')
    no,yes=[tokenizer.convert_tokens_to_ids(x) for x in ['no','yes']]
    slots=['agent','relation','patient','tail','decision'];forwards=0

    def forward(batch,capture=False,patches=None,module_patches=None):
        nonlocal forwards
        data,maps=encode(batch,capture or bool(patches) or bool(module_patches));cache={'maps':maps,'mask':data['attention_mask'].clone()};handles=[]
        try:
            if capture:
                def positional(_,args,kwargs):
                    cos,sin=kwargs['position_embeddings'];cache['cos']=cos.detach().cpu();cache['sin']=sin.detach().cpu()
                handles.append(attn.register_forward_pre_hook(positional,with_kwargs=True))
                for name in ['q_proj','k_proj','v_proj']:
                    def collect(_,__,output,name=name):cache[name]=output.detach().cpu().contiguous()
                    handles.append(getattr(attn,name).register_forward_hook(collect))
                def before_o(_,inputs):
                    x=inputs[0]
                    for slot in slots:
                        cache[slot]=torch.stack([x[i,m[slot][-1]].detach().cpu() for i,m in enumerate(maps)]).contiguous()
                handles.append(attn.o_proj.register_forward_pre_hook(before_o))
                if args.followup_from:
                    layer_modules=model.base_model.model.model.layers
                    for name,module in [('mlp23',layer_modules[23].mlp),('mlp26',layer_modules[26].mlp),('final_residual',layer_modules[-1])]:
                        def collect_final(_,__,output,name=name):
                            value=output[0] if isinstance(output,tuple) else output
                            cache[name]=torch.stack([value[i,m['decision'][-1]].detach().cpu() for i,m in enumerate(maps)]).contiguous()
                        handles.append(module.register_forward_hook(collect_final))
            if patches:
                def intervene(_,inputs):
                    value=inputs[0]
                    for patch in patches:
                        replacement=patch['values'].to(value.device,dtype=value.dtype)
                        if patch['head'] is None:
                            value=value.clone()
                            for i,m in enumerate(maps):value[i,m[patch['slot']][-1]]=replacement[i]
                        else:
                            value=replace_head(value,[m[patch['slot']][-1] for m in maps],replacement,patch['head'],dim,heads)
                    return (value,*inputs[1:])
                handles.append(attn.o_proj.register_forward_pre_hook(intervene))
            if module_patches:
                layer_modules=model.base_model.model.model.layers
                targets={'mlp23':layer_modules[23].mlp,'mlp26':layer_modules[26].mlp,'final_residual':layer_modules[-1]}
                for name,replacement in module_patches.items():
                    def replace_module(_,__,output,replacement=replacement):
                        hidden=output[0] if isinstance(output,tuple) else output
                        value=hidden.clone();replacement=replacement.to(value.device,dtype=value.dtype)
                        for i,m in enumerate(maps):value[i,m['decision'][-1]]=replacement[i]
                        return (value,*output[1:]) if isinstance(output,tuple) else value
                    handles.append(targets[name].register_forward_hook(replace_module))
            with torch.inference_mode():
                logits=model(**data.to('cuda:0'),use_cache=False,logits_to_keep=1).logits[:,-1,[no,yes]].float()
            forwards+=1;margin=(logits[:,1]-logits[:,0]).cpu().tolist()
            if not all(math.isfinite(x) for x in margin):raise ValueError('nonfinite margin')
            return margin,cache
        finally:
            for handle in handles:handle.remove()

    def score(items):
        result=[]
        for start in range(0,len(items),args.batch_size):result.extend(forward(items[start:start+args.batch_size])[0])
        return result
    if args.followup_from:
        prior=json.loads((args.followup_from/'protocol.json').read_text())
        for key in ['casesSha256','adapterSha256','challengeSha256','layer']:
            if protocol[key]!=prior[key]:raise ValueError('followup input changed: '+key)
        groups=json.loads((args.followup_from/'pair-selection.json').read_text())['groups']
        write(args.out/'followup-protocol.json',{'priorProtocolSha256':sha(args.followup_from/'protocol.json'),
              'frozenSelectionSha256':sha(args.followup_from/'selection.json'),'newSitesSelected':False,
              'positiveControl':'all heads at tail/decision, whole MLP23/26, and trivial final residual transplant',
              'necessityGate':'only tail all-head paired mean gain>=1 and at least1 restored; otherwise skip',
              'interpretation':'conditional necessity inside an artificial all-head donor context, not normal-model circuit proof'})
        records={};noop=0
        for group,pairs in groups.items():
            if not pairs:continue
            margins={};cache={}
            for kind in ['anchor','paired','unrelated']:
                margins[kind],cache[kind]=forward([p[kind] for p in pairs],True)
            baseline=margins['anchor'];batch=[p['anchor'] for p in pairs];experiments=[];tail_score=None
            for slot in ['tail','decision']:
                for kind in ['paired','unrelated','anchor']:
                    result,_=forward(batch,patches=[{'head':None,'slot':slot,'values':cache[kind][slot]}])
                    experiments.append({'name':'all_heads_'+slot+'_'+kind,**effect(pairs,baseline,result)})
                    if slot=='tail' and kind=='paired':tail_score=result
                    if kind=='anchor':noop=max(noop,max(abs(a-b) for a,b in zip(baseline,result,strict=True)))
            for target in ['mlp23','mlp26','final_residual']:
                for kind in ['paired','unrelated','anchor']:
                    result,_=forward(batch,module_patches={target:cache[kind][target]})
                    experiments.append({'name':target+'_'+kind,**effect(pairs,baseline,result)})
                    if kind=='anchor':noop=max(noop,max(abs(a-b) for a,b in zip(baseline,result,strict=True)))
                    if target=='final_residual' and kind=='paired' and max(abs(a-b) for a,b in zip(margins['paired'],result,strict=True))>.25:
                        raise ValueError('trivial readout positive control failed')
            tail_effect=effect(pairs,baseline,tail_score)['all'];necessity=[]
            if tail_effect['meanSignedDelta']>=1 and tail_effect['correctAfter']>0:
                for h in range(heads):
                    patches=[{'head':None,'slot':'tail','values':cache['paired']['tail']},
                             {'head':h,'slot':'tail','values':cache['anchor']['tail'][:,h*dim:(h+1)*dim]}]
                    result,_=forward(batch,patches=patches)
                    loss=[(1 if p['label']=='yes' else -1)*(a-b) for p,a,b in zip(pairs,tail_score,result,strict=True)]
                    necessity.append({'head':h,'meanLossFromAllHeadsPaired':sum(loss)/len(loss),**effect(pairs,baseline,result)})
            records[group]={'pairs':len(pairs),'experiments':experiments,'conditionalNecessity':necessity,
                            'necessityGatePassed':bool(necessity)}
        unchanged=sha(args.checkpoint/'adapter/adapter_model.safetensors')==original_sha
        versions_ok=all(p._version==versions[k] for k,p in model.named_parameters())
        if not unchanged or not versions_ok or noop>.25:raise ValueError('followup immutability/no-op failed')
        result={'status':'complete','groups':records,'noopMaxAbsoluteDelta':noop,'weightsEdited':False,
                'modelExported':False,'sourceAdapterUnchanged':unchanged,'parameterVersionsUnchanged':versions_ok,
                'deploymentEnabled':False,'forwardBatches':forwards,'totalSeconds':time.monotonic()-started}
        write(args.out/'followup-summary.json',result);write(args.out/'progress.json',{'stage':'complete','weightsEdited':False})
        print(json.dumps({'stage':'followup_complete','forwards':forwards,'seconds':result['totalSeconds']}),flush=True)
        return
    challenge=[]
    for line in args.challenge.read_text().splitlines():
        row=json.loads(line);task,label=next(iter(row['expected'].items()));challenge.append({**row,'task':task,'label':label})
    challenge_scores=score(challenge)
    replay={'rows':len(challenge),'correct':sum(correct(m,r['label']) for r,m in zip(challenge,challenge_scores,strict=True))}
    values=score(rows);lookup={r['id']:m for r,m in zip(rows,values,strict=True)}
    write(args.out/'behavior.json',{'challenge':replay,'cases':[{**r,'margin':m,'correct':correct(m,r['label'])} for r,m in zip(rows,values,strict=True)]})
    pairs={'discovery':[],'confirmation':[]};excluded=[]
    for family in families:
        anchor=family['variants'][0];label=anchor['label'];sign=1 if label=='yes' else -1
        rewrite=next((r for r in family['variants'] if r['kind'].startswith('rewrite') and sign*lookup[r['id']]>=2),None)
        unrelated=next((r for r in family['variants'] if r['kind'].startswith('unrelated') and sign*lookup[r['id']]>=2),None)
        if correct(lookup[anchor['id']],label) or rewrite is None or unrelated is None:
            excluded.append({'family':family['id'],'language':family['language'],'anchorMargin':lookup[anchor['id']],
                             'reason':'anchor_correct' if correct(lookup[anchor['id']],label) else 'no_correct_rewrite' if rewrite is None else 'no_correct_unrelated'})
            continue
        pairs[family['split']].append({'id':family['id'],'language':family['language'],'category':family['category'],
                  'label':label,'anchor':anchor,'paired':rewrite,'unrelated':unrelated,
                  'counter':next(r for r in family['variants'] if r['kind']=='opposite')})
    prepared={}
    for group,items in pairs.items():
        if not items:continue
        # Recheck all three primary conditions at exact intervention batch size.
        while items:
            caches={};margins={}
            for kind in ['anchor','paired','unrelated','counter']:
                margins[kind],caches[kind]=forward([p[kind] for p in items],True)
            valid=[i for i,p in enumerate(items) if not correct(margins['anchor'][i],p['label']) and
                   all((1 if p['label']=='yes' else -1)*margins[k][i]>=2 for k in ['paired','unrelated'])]
            if len(valid)==len(items):break
            excluded.extend({'family':p['id'],'reason':'batch_stability_gate'} for i,p in enumerate(items) if i not in valid)
            items=[items[i] for i in valid]
        pairs[group]=items
        if items:
            prepared[group]={'pairs':items,'cache':caches,'margins':margins}
            tensors={kind+'/'+slot:caches[kind][slot] for kind in caches for slot in slots}
            save_file(tensors,str(args.out/(group+'-head-activations.safetensors')))
    write(args.out/'pair-selection.json',{'groups':pairs,'excluded':excluded})
    print(json.dumps({'stage':'behavior','challenge':replay,'rows':len(rows),'correct':sum(correct(m,r['label']) for r,m in zip(rows,values,strict=True)),
                      'pairs':{k:len(v['pairs']) for k,v in prepared.items()},'excluded':len(excluded)}),flush=True)

    def head_value(cache,slot,h):return cache[slot][:,h*dim:(h+1)*dim]
    sweep=[]
    if 'discovery' in prepared:
        p=prepared['discovery'];batch=[r['anchor'] for r in p['pairs']]
        for h in range(heads):
            for slot in slots:
                modes={}
                for kind in ['paired','unrelated']:
                    patched,_=forward(batch,patches=[{'head':h,'slot':slot,'values':head_value(p['cache'][kind],slot,h)}])
                    modes[kind]=effect(p['pairs'],p['margins']['anchor'],patched)
                a=modes['paired']['all']['meanSignedDelta'];b=modes['unrelated']['all']['meanSignedDelta']
                sweep.append({'head':h,'slot':slot,'specificityDelta':a-b,**modes})
            write(args.out/'progress.json',{'stage':'head_sweep','headsCompleted':h+1,'heads':heads})
            if h%8==7:print(json.dumps({'stage':'head_sweep','headsCompleted':h+1}),flush=True)
    write(args.out/'head-sweep.json',{'layer':args.layer,'sites':sweep})
    ranking=sorted([r for r in sweep if r['paired']['all']['meanSignedDelta']>0],key=lambda r:-r['specificityDelta'])
    selected=[]
    for r in ranking:
        if all(r['head']!=v['head'] for v in selected):selected.append(r)
        if len(selected)==3:break
    write(args.out/'selection.json',{'sites':selected,'frozenBeforeConfirmation':True})
    confirmation=[];edges=[];reconstruction=[];noop=0
    if 'confirmation' in prepared:
        p=prepared['confirmation'];batch=[r['anchor'] for r in p['pairs']]
        for number,site in enumerate(selected):
            h,slot=site['head'],site['slot'];bad=head_value(p['cache']['anchor'],slot,h).float()
            donor=head_value(p['cache']['paired'],slot,h).float();delta=donor-bad
            modes=[('paired',donor,False,None),('unrelated',head_value(p['cache']['unrelated'],slot,h),False,None),
                   ('self',bad,False,None),('counter',head_value(p['cache']['counter'],slot,h),False,
                    [correct(m,r['counter']['label']) for m,r in zip(p['margins']['counter'],p['pairs'],strict=True)]),
                   ('reverse',bad,True,None)]
            for seed in [2201,2202,2203]:
                noise=torch.from_numpy(np.random.default_rng(seed+number*10).normal(size=delta.shape)).float()
                noise=noise/noise.norm(dim=-1,keepdim=True).clamp_min(1e-12)*delta.norm(dim=-1,keepdim=True)
                modes.append(('random_'+str(seed),bad+noise,False,None))
            for mode,replacement,reverse,mask in modes:
                recipient=[r['paired' if reverse else 'anchor'] for r in p['pairs']]
                before=p['margins']['paired' if reverse else 'anchor']
                patched,_=forward(recipient,patches=[{'head':h,'slot':slot,'values':replacement}])
                result={'head':h,'slot':slot,'mode':mode,**effect(p['pairs'],before,patched,mask)}
                if mode=='self':noop=max(noop,max(abs(a-b) for a,b in zip(before,patched,strict=True)))
                confirmation.append(result)

        # Reconstruct query-head results from recorded Q/K/V using the same q/k
        # norms, RoPE and GQA mapping, then validate against actual SDPA outputs.
        def contributions(cache,site):
            b,t,_=cache['q_proj'].shape;h=site['head'];slot=site['slot']
            with torch.inference_mode():
                q=attn.q_norm(cache['q_proj'].to('cuda:0').view(b,t,heads,dim)).transpose(1,2)
                k=attn.k_norm(cache['k_proj'].to('cuda:0').view(b,t,kv_heads,dim)).transpose(1,2)
                q,k=apply_rotary_pos_emb(q,k,cache['cos'].to('cuda:0'),cache['sin'].to('cuda:0'))
                v=cache['v_proj'].to('cuda:0').view(b,t,kv_heads,dim)
                positions=[m[slot][-1] for m in cache['maps']]
                qh=torch.stack([q[i,h,j] for i,j in enumerate(positions)]).float()
                kh=k[:,h//(heads//kv_heads)].float();vh=v[:,:,h//(heads//kv_heads)].float()
                logits=torch.einsum('bd,btd->bt',qh,kh)*attn.scaling
                mask=cache['mask'].to('cuda:0').bool()
                causal=torch.arange(t,device='cuda:0')[None,:]<=torch.tensor(positions,device='cuda:0')[:,None]
                if attn.sliding_window is not None:
                    causal &= torch.arange(t,device='cuda:0')[None,:] > torch.tensor(positions,device='cuda:0')[:,None]-attn.sliding_window
                weights=logits.masked_fill(~(mask&causal),float('-inf')).softmax(-1)
                reconstructed=torch.einsum('bt,btd->bd',weights,vh).cpu()
                observed=head_value(cache,slot,h).float()
                relative=(reconstructed-observed).norm(dim=-1)/observed.norm(dim=-1).clamp_min(1e-6)
                result={}
                for span in ['agent','relation','patient','query_agent','query_relation','query_patient','other']:
                    take=torch.zeros((b,t),device='cuda:0',dtype=torch.bool);valid=[];counts=[]
                    for i,m in enumerate(cache['maps']):
                        indices=m.get(span,[]);valid.append(bool(indices));counts.append(len(set(indices)))
                        if indices:take[i,indices]=True
                    mass=weights*take
                    result[span]={'vector':torch.einsum('bt,btd->bd',mass,vh).cpu(),
                                  'mass':mass.sum(-1).cpu().tolist(),'valid':valid,'tokens':counts}
                return result,relative.tolist()

        for site in selected:
            caches={};errs={}
            for kind in ['anchor','paired','unrelated']:
                caches[kind],errs[kind]=contributions(p['cache'][kind],site)
            reconstruction.append({'head':site['head'],'slot':site['slot'],'relativeL2Error':errs})
            observed=head_value(p['cache']['anchor'],site['slot'],site['head']).float()
            for span in caches['anchor']:
                valid=[all(caches[k][span]['valid'][i] and errs[k][i]<=.02 for k in caches) for i in range(len(p['pairs']))]
                if not any(valid):continue
                for kind in ['paired','unrelated']:
                    delta=caches[kind][span]['vector']-caches['anchor'][span]['vector']
                    values=observed+delta
                    for i,active in enumerate(valid):
                        if not active:values[i]=observed[i]
                    patched,_=forward(batch,patches=[{'head':site['head'],'slot':site['slot'],'values':values}])
                    edges.append({'head':site['head'],'slot':site['slot'],'span':span,'mode':kind,
                                  'attentionMass':{k:caches[k][span]['mass'] for k in caches},
                                  **effect(p['pairs'],p['margins']['anchor'],patched,valid)})
        if selected:
            for mode in ['paired','unrelated','self']:
                patches=[{'head':s['head'],'slot':s['slot'],'values':head_value(p['cache']['anchor' if mode=='self' else mode],s['slot'],s['head'])} for s in selected]
                patched,_=forward(batch,patches=patches)
                confirmation.append({'head':'combination','slot':'selected_sites','mode':mode,**effect(p['pairs'],p['margins']['anchor'],patched)})
                if mode=='self':noop=max(noop,max(abs(a-b) for a,b in zip(p['margins']['anchor'],patched,strict=True)))
    write(args.out/'confirmation.json',{'experiments':confirmation})
    write(args.out/'edge-contributions.json',{'experiments':edges,'reconstruction':reconstruction,'tolerance':.02})
    unchanged=sha(args.checkpoint/'adapter/adapter_model.safetensors')==original_sha
    versions_ok=all(r._version==versions[k] for k,r in model.named_parameters())
    if not unchanged or not versions_ok or noop>.25:raise ValueError('immutability/no-op check failed')
    summary={'status':'complete','layer':args.layer,'heads':heads,'kvHeads':kv_heads,'headDim':dim,
             'behaviorRows':len(rows),'behaviorCorrect':sum(correct(lookup[r['id']],r['label']) for r in rows),
             'challenge':replay,'eligiblePairs':{k:len(v['pairs']) for k,v in prepared.items()},'excluded':excluded,
             'selectedSites':[{k:v for k,v in r.items() if k not in ['paired','unrelated']} for r in selected],
             'confirmation':[{k:v for k,v in r.items() if k!='cases'} for r in confirmation],
             'sourceAdapterUnchanged':unchanged,'parameterVersionsUnchanged':versions_ok,'weightsEdited':False,'modelExported':False,
             'noopMaxAbsoluteDelta':noop,'forwardBatches':forwards,'totalSeconds':time.monotonic()-started,
             'peakAllocatedMiB':torch.cuda.max_memory_allocated()/1024**2,'deploymentEnabled':False}
    write(args.out/'summary.json',summary);write(args.out/'progress.json',{'stage':'complete','weightsEdited':False})
    print(json.dumps({'stage':'complete','forwards':forwards,'seconds':summary['totalSeconds']}),flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['base','checkpoint','cases','challenge','out']:parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--layer',type=int,default=17);parser.add_argument('--batch-size',type=int,default=16)
    parser.add_argument('--followup-from',type=Path,help='Frozen-cohort whole-node controls and gated conditional necessity')
    args=parser.parse_args();existed=args.out.exists()
    try:run(args)
    except Exception as exc:
        if not existed and args.out.is_dir():write(args.out/'failure.json',{'type':type(exc).__name__,'message':str(exc)[:300]})
        raise
