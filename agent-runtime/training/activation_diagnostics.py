"""Read-only memory diagnostics: behavioral contrasts, logit lens, activation patches.

No weight editing, optimizer, training, model export, or production calls.
All interventions replace a cloned forward activation and are removed in finally.
"""

import argparse
import hashlib
import json
import math
import time
from pathlib import Path


def digest(path):
    with Path(path).open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()


def write(path, value):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temp.replace(path)


def source_text(row):
    state = row["state"]
    if "observation" in state:
        return state["observation"]
    return state["candidate" if row["task"] == "relevance" else "source"]["content"]


def token_positions(prompt, row, offsets, mask):
    """Align semantic field boundaries, never absolute clean/corrupt token numbers."""
    text = json.dumps(source_text(row), ensure_ascii=False)[1:-1]
    text = text.replace("<", "\\u003c").replace(">", "\\u003e")
    start = prompt.index(text, prompt.index("<Document>:"))
    end = start + len(text)
    overlap = [i for i, ((a, b), active) in enumerate(zip(offsets, mask, strict=True))
               if active and b > start and a < end]
    if not overlap:
        raise ValueError("semantic field has no aligned tokens")
    return {"source_first": overlap[0], "source_end": overlap[-1],
            "last": max(i for i, active in enumerate(mask) if active)}


def cloned_slot(output, positions, replacement):
    """Preserve tuple auxiliary outputs and never mutate the original activation."""
    hidden = output[0] if isinstance(output, tuple) else output
    copied = hidden.clone() if hasattr(hidden, "clone") else hidden.copy()
    for i, position in enumerate(positions):
        copied[i, position] = replacement[i]
    return (copied, *output[1:]) if isinstance(output, tuple) else copied


def correct_choice(margin, label):
    # The serving scorer selects yes at an exact zero, including for a no gold.
    return (margin >= 0) == (label == "yes")


def compare_effect(pairs, baseline, patched, donor, valid=None):
    result = []
    for i, (pair, a, b, c) in enumerate(zip(pairs, baseline, patched, donor, strict=True)):
        if valid is not None and not valid[i]:
            continue
        sign = 1 if pair["label"] == "yes" else -1
        gap = sign * (c - a)
        delta = sign * (b - a)
        result.append({"family": pair["family"], "task": pair["task"], "label": pair["label"],
                       "donorKind": pair["donorKind"], "baselineMargin": a, "patchedMargin": b,
                       "donorMargin": c, "signedDelta": delta,
                       "normalizedRecovery": delta / gap if abs(gap) >= 1 else None,
                       "correctBefore": correct_choice(a, pair["label"]),
                       "correctAfter": correct_choice(b, pair["label"])})
    groups = {}
    for kind in ["same_semantics_paraphrase", "task_irrelevant_source_control_not_paraphrase"]:
        subset = [r for r in result if r["donorKind"] == kind]
        groups[kind] = {"pairs": len(subset),
                        "meanSignedDelta": sum(r["signedDelta"] for r in subset) / len(subset) if subset else None,
                        "positivePairs": sum(r["signedDelta"] > .25 for r in subset),
                        "correctBefore": sum(r["correctBefore"] for r in subset),
                        "correctAfter": sum(r["correctAfter"] for r in subset)}
    return {"groups": groups, "cases": result}


def run(args):
    args.out.mkdir(parents=True, exist_ok=False)
    import os
    for key, suffix in [("TMPDIR", "tmp"), ("CUDA_CACHE_PATH", "cuda"),
                         ("TORCHINDUCTOR_CACHE_DIR", "inductor"), ("TRITON_CACHE_DIR", "triton")]:
        p = args.out / "cache" / suffix
        p.mkdir(parents=True, exist_ok=True)
        os.environ[key] = str(p)
    import numpy as np
    import torch
    from peft import PeftModel
    from safetensors.torch import save_file
    from transformers import AutoModelForCausalLM, AutoTokenizer
    from psych_agent_runtime.memory_lifecycle_contract import render, INSTRUCTIONS

    started = time.monotonic()
    torch.set_num_threads(4)
    report = json.loads((args.checkpoint / "training-report.json").read_text())
    original_sha = digest(args.checkpoint / "adapter/adapter_model.safetensors")
    if original_sha != report["adapterSha256"] or args.base.name != report["baseRevision"]:
        raise ValueError("source model identity mismatch")
    instructions_sha = hashlib.sha256(json.dumps(INSTRUCTIONS, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if instructions_sha != report["datasetManifest"]["instructionsSha256"]:
        raise ValueError("instructions changed")
    for name, expected in report["baseWeightsSha256"].items():
        if digest(args.base / name) != expected:
            raise ValueError("base weights changed")
    if digest(args.challenge) != report["challengeSha256"]:
        raise ValueError("challenge not frozen")
    dataset = json.loads(args.cases.read_text())
    rows = [r for f in dataset["families"] for r in f["variants"]] + dataset["controls"]
    protocol = {"casesSha256": digest(args.cases), "sourceSha256": digest(Path(__file__)),
                "adapterSha256": original_sha, "instructionsSha256": instructions_sha,
                "challengeSha256": digest(args.challenge), "weightsEdited": False,
                "gradients": False, "deploymentEnabled": False, "familyCount": len(dataset["families"]),
                "behaviorRows": len(rows), "batchSize": args.batch_size,
                "sites": "all layers x residual/attention/MLP x source_first/source_end/last",
                "donorSelection": "first predeclared rewrite with correct signed margin >=2, anchor must be wrong",
                "ranking": "discovery paraphrase pairs only; exclude residual and final two layers; include best source-field site, then highest mean signed deltas at layers at least2 apart, max3",
                "confirmation": "fixed family split; denoise/reverse/self/random-norm/shuffled-same-task-and-label",
                "limits": ["assistant-authored diagnostics, not independent human gold",
                           "logit lens descriptive, late residual answer transfer not bug localization",
                           "node interventions, not a complete causal circuit or per-head path proof"]}
    write(args.out / "protocol.json", protocol)
    if torch.cuda.mem_get_info()[0] < 24 * 1024**3:
        raise RuntimeError("insufficient free GPU memory; do not evict other jobs")
    torch.cuda.set_per_process_memory_fraction(.24)
    tokenizer = AutoTokenizer.from_pretrained(args.base, padding_side="left", local_files_only=True)
    base = AutoModelForCausalLM.from_pretrained(args.base, torch_dtype=torch.bfloat16,
                attn_implementation="sdpa", local_files_only=True).to("cuda:0")
    model = PeftModel.from_pretrained(base, args.checkpoint / "adapter").eval()
    model.requires_grad_(False)
    versions = {name: p._version for name, p in model.named_parameters()}
    layers = model.base_model.model.model.layers
    modules = {f"{kind}/{i}": component for i, layer in enumerate(layers)
               for kind, component in [("residual", layer), ("attention", layer.self_attn), ("mlp", layer.mlp)]}
    positions = ["source_first", "source_end", "last"]
    no, yes = [tokenizer.convert_tokens_to_ids(x) for x in ["no", "yes"]]
    forwards = 0

    def encode(batch, align):
        prompts = [render(r["task"], r["state"]) for r in batch]
        data = tokenizer(prompts, padding=True, return_tensors="pt", truncation=False,
                         return_offsets_mapping=align)
        if data.input_ids.shape[1] > 2048:
            raise ValueError("no silent truncation")
        pos = []
        if align:
            offsets = data.pop("offset_mapping").tolist()
            masks = data["attention_mask"].tolist()
            pos = [token_positions(p, r, o, m) for p, r, o, m in zip(prompts, batch, offsets, masks, strict=True)]
        return data.to("cuda:0"), pos

    def forward(batch, capture=False, intervention=None):
        nonlocal forwards
        data, aligned = encode(batch, capture or intervention is not None)
        cache, handles = {}, []
        try:
            if capture:
                for key, module in modules.items():
                    def hook(_, __, output, key=key):
                        hidden = output[0] if isinstance(output, tuple) else output
                        for pos in positions:
                            cache[key + "/" + pos] = torch.stack([
                                hidden[i, item[pos]].detach().cpu() for i, item in enumerate(aligned)]).contiguous()
                    handles.append(module.register_forward_hook(hook))
            if intervention is not None:
                interventions = intervention if isinstance(intervention, list) else [intervention]
                for site, replacement in interventions:
                    module = modules[site["component"] + "/" + str(site["layer"])]
                    def replace(_, __, output, site=site, replacement=replacement):
                        hidden = output[0] if isinstance(output, tuple) else output
                        return cloned_slot(output, [p[site["position"]] for p in aligned],
                                           replacement.to(device=hidden.device, dtype=hidden.dtype))
                    handles.append(module.register_forward_hook(replace))
            with torch.inference_mode():
                logits = model(**data, use_cache=False, logits_to_keep=1).logits[:, -1, [no, yes]].float()
            forwards += 1
            margin = (logits[:, 1] - logits[:, 0]).cpu().tolist()
            if not all(math.isfinite(x) for x in margin):
                raise ValueError("nonfinite margin")
            return margin, cache
        finally:
            for h in handles:
                h.remove()

    def batches(items, capture=False):
        margins, all_cache = [], {}
        for offset in range(0, len(items), args.batch_size):
            values, cache = forward(items[offset:offset+args.batch_size], capture)
            margins.extend(values)
            for k, tensor in cache.items():
                all_cache.setdefault(k, []).append(tensor)
        return margins, {k: torch.cat(v).contiguous() for k, v in all_cache.items()}

    if args.path_from:
        prior_protocol = json.loads((args.path_from / 'protocol.json').read_text())
        for key in ['casesSha256','adapterSha256','instructionsSha256','challengeSha256']:
            if prior_protocol[key] != protocol[key]:
                raise ValueError('path study inputs changed: '+key)
        pairs = json.loads((args.path_from / 'pair-selection.json').read_text())['groups']['confirmation']
        sites = [r['site'] for r in json.loads((args.path_from / 'localization-selection.json').read_text())['sites']]
        upstream = next(s for s in sites if s['component']=='attention' and s['position']!='last')
        downstream = sorted([s for s in sites if s['component']=='mlp' and s['layer']>upstream['layer']],
                            key=lambda s:s['layer'])
        if len(downstream)!=2:
            raise ValueError('need two frozen downstream MLP candidates')
        def key_of(s): return f"{s['component']}/{s['layer']}/{s['position']}"
        write(args.out/'path-protocol.json',{'priorProtocolSha256':digest(args.path_from/'protocol.json'),
              'frozenSelectionSha256':digest(args.path_from/'localization-selection.json'),
              'upstream':upstream,'downstream':downstream,'newSitesSelected':False,
              'interpretation':'conditional node mediation test, not a complete isolated edge circuit'})
        bad_rows=[p['anchor'] for p in pairs]
        bad_margin,bad=forward(bad_rows,True)
        good_margin,good=forward([p['donor'] for p in pairs],True)
        a=(upstream,good[key_of(upstream)])
        a_margin,a_cache=forward(bad_rows,True,[a])
        experiments=[('baseline',[]),('upstream_clean',[a])]
        for site in downstream:
            experiments.append((f"upstream_block_mlp_{site['layer']}",[a,(site,bad[key_of(site)])]))
            experiments.append((f"mlp_{site['layer']}_only_upstream_induced",[(site,a_cache[key_of(site)])]))
        experiments.extend([
            ('upstream_block_both',[a,*[(s,bad[key_of(s)]) for s in downstream]]),
            ('both_mlp_only_upstream_induced',[(s,a_cache[key_of(s)]) for s in downstream]),
            ('upstream_block_early_control',[a,({'layer':0,'component':'mlp','position':'last'},bad['mlp/0/last'])]),
            ('self_all',[(upstream,bad[key_of(upstream)]),*[(s,bad[key_of(s)]) for s in downstream]])])
        delta=good[key_of(upstream)].float()-bad[key_of(upstream)].float()
        for seed in [1105,1106,1107]:
            noise=torch.from_numpy(np.random.default_rng(seed).normal(size=delta.shape)).float()
            noise=noise/noise.norm(dim=-1,keepdim=True).clamp_min(1e-12)*delta.norm(dim=-1,keepdim=True)
            experiments.append((f"random_upstream_seed_{seed}",[(upstream,bad[key_of(upstream)].float()+noise)]))
        matched_masks={}
        if args.matched_donor_controls:
            # Use unrelated correct records with the same task/label. A generic
            # answer signal transplant must not masquerade as semantic repair.
            pool=[]
            for task,label in sorted({(p['task'],p['label']) for p in pairs}):
                options=[]
                for line in args.challenge.read_text().splitlines():
                    record=json.loads(line)
                    if record['expected']!={task:label}:
                        continue
                    if any(record['state']==v[k]['state'] for v in pairs for k in ['anchor','donor']):
                        continue
                    options.append({**record,'task':task,'label':label})
                options.sort(key=lambda r:hashlib.sha256(json.dumps(r['state'],sort_keys=True,ensure_ascii=False).encode()).hexdigest())
                pool.extend(options[:3])
            pool_scores,_=batches(pool)
            selected=[];valid=[];audit=[]
            for pair in pairs:
                sign=1 if pair['label']=='yes' else -1
                eligible=[(r,m) for r,m in zip(pool,pool_scores,strict=True)
                          if r['task']==pair['task'] and r['label']==pair['label'] and sign*m>=2]
                chosen,margin=eligible[0] if eligible else (pair['anchor'],None)
                selected.append(chosen);valid.append(bool(eligible))
                audit.append({'family':pair['family'],'donor':chosen,'margin':margin,'eligible':bool(eligible)})
            _,matched_cache=forward(selected,True)
            for site in [upstream,*downstream]:
                name=f"unrelated_same_label_{site['component']}_{site['layer']}"
                experiments.append((name,[(site,matched_cache[key_of(site)])]))
                matched_masks[name]=valid
            write(args.out/'matched-donors.json',{'rule':'same task and label; first >=2 signed margin from 3 hash-ranked different records; not length/language matched',
                                                 'rows':audit})
        results=[];baseline_score=None;upstream_score=None;noop=0
        for name,patches in experiments:
            score,_=forward(bad_rows,intervention=patches)
            item={'experiment':name,**compare_effect(pairs,bad_margin,score,good_margin,matched_masks.get(name))}
            item['changeFromUpstream']=[(1 if p['label']=='yes' else -1)*(x-y)
                                      for p,x,y in zip(pairs,score,a_margin,strict=True)]
            results.append(item)
            if name=='self_all':noop=max(abs(a-b) for a,b in zip(score,bad_margin,strict=True))
            if name=='upstream_clean':upstream_score=score
            if name=='upstream_block_early_control':baseline_score=score
        negative_delta=max(abs(x-y) for x,y in zip(baseline_score,upstream_score,strict=True))
        versions_ok=all(p._version==versions[name] for name,p in model.named_parameters())
        source_ok=digest(args.checkpoint/'adapter/adapter_model.safetensors')==original_sha
        if noop>.25 or negative_delta>.25 or not versions_ok or not source_ok:
            raise ValueError('path study immutability or negative control failed')
        save_file({k:v for k,v in a_cache.items() if any(k.startswith(f"mlp/{s['layer']}/") for s in downstream)},
                  str(args.out/'upstream-induced-mlp-activations.safetensors'))
        result={'status':'complete','experiments':results,'sourceAdapterUnchanged':source_ok,
                'parameterVersionsUnchanged':versions_ok,'weightsEdited':False,'modelExported':False,
                'deploymentEnabled':False,'newSitesSelected':False,'noopMaxAbsoluteDelta':noop,
                'earlyControlMaxDelta':negative_delta,'forwardBatches':forwards,
                'totalSeconds':time.monotonic()-started}
        write(args.out/'path-summary.json',result)
        write(args.out/'progress.json',{'stage':'complete','weightsEdited':False})
        print(json.dumps({'stage':'path_complete','forwards':forwards,'seconds':result['totalSeconds']}),flush=True)
        return

    challenge = []
    for line in args.challenge.read_text().splitlines():
        r = json.loads(line)
        task, label = next(iter(r["expected"].items()))
        challenge.append({**r, "task": task, "label": label})
    challenge_margin, _ = batches(challenge)
    replay = {"rows": len(challenge),
              "correct": sum((x >= 0) == (r["label"] == "yes") for r, x in zip(challenge, challenge_margin, strict=True))}
    write(args.out / "challenge-replay.json", replay)
    margins, trace = batches(rows, True)
    by_id = {r["id"]: (r, margin) for r, margin in zip(rows, margins, strict=True)}
    behavior = [{**r, "margin": m, "correct": (m >= 0) == (r["label"] == "yes")}
                for r, m in zip(rows, margins, strict=True)]
    write(args.out / "behavior.json", {"challenge": replay, "cases": behavior})
    save_file(trace, str(args.out / "behavior-activations.safetensors"))
    lens = []
    with torch.inference_mode():
        head = model.get_output_embeddings().weight[[no, yes]]
        norm = model.base_model.model.model.norm
        for i in range(len(layers)):
            h = norm(trace[f"residual/{i}/last"].to("cuda:0"))
            values = torch.nn.functional.linear(h, head).float()
            lens.append((values[:, 1] - values[:, 0]).cpu().tolist())
    write(args.out / "logit-lens.json", {"note":"descriptive intermediate readout, not causal attribution",
            "cases": [{"id":r["id"], "task":r["task"], "label":r["label"], "finalMargin":m,
                       "layerMargins":[layer[j] for layer in lens]}
                      for j,(r,m) in enumerate(zip(rows,margins,strict=True))]})
    del trace
    groups = {"discovery": [], "confirmation": []}
    excluded = []
    for family in dataset["families"]:
        anchor = family["variants"][0]
        a = by_id[anchor["id"]][1]
        sign = 1 if anchor["label"] == "yes" else -1
        donor = next((r for r in family["variants"][1:3] if sign * by_id[r["id"]][1] >= 2), None)
        if correct_choice(a, anchor["label"]) or donor is None:
            excluded.append({"family":family["id"], "anchorMargin":a,
                             "reason":"anchor_not_wrong" if correct_choice(a, anchor["label"]) else "no_correct_same_label_donor"})
            continue
        groups[family["split"]].append({"family":family["id"],"task":anchor["task"],
            "label":anchor["label"],"donorKind":family["donorKind"],"anchor":anchor,"donor":donor})
    write(args.out / "pair-selection.json", {"groups":groups,"excluded":excluded})
    print(json.dumps({"stage":"behavior_complete","challenge":replay,
          "contrastCorrect":sum(r['correct'] for r in behavior),"contrastRows":len(behavior),
          "pairs":{k:len(v) for k,v in groups.items()},"excludedFamilies":len(excluded)}),flush=True)

    prepared = {}
    for group, pairs in groups.items():
        if not pairs:
            continue
        bad_margin, bad = forward([p["anchor"] for p in pairs], True)
        good_margin, good = forward([p["donor"] for p in pairs], True)
        # Recheck pair eligibility at the exact intervention batch size.
        stable = [i for i,p in enumerate(pairs) if
                  not correct_choice(bad_margin[i],p['label']) and
                  (1 if p["label"]=='yes' else -1)*good_margin[i] >= 2]
        if len(stable) != len(pairs):
            excluded.extend({"family":pairs[i]["family"],"reason":"batch_shape_sign_or_margin_changed"}
                            for i in range(len(pairs)) if i not in stable)
            pairs = [pairs[i] for i in stable]
            groups[group] = pairs
            if not pairs:
                continue
            bad_margin,bad=forward([p["anchor"] for p in pairs],True)
            good_margin,good=forward([p["donor"] for p in pairs],True)
        prepared[group] = {"pairs":pairs,"bad":bad,"good":good,"badMargin":bad_margin,"goodMargin":good_margin}
        save_file({**{"bad/"+k:v for k,v in bad.items()}, **{"good/"+k:v for k,v in good.items()}},
                  str(args.out / (group+"-activations.safetensors")))
    write(args.out / "pair-selection.json", {"groups":groups,"excluded":excluded})
    sweep = []
    if "discovery" in prepared:
        p = prepared["discovery"]
        for layer in range(len(layers)):
            for component in ["residual", "attention", "mlp"]:
                for position in positions:
                    site={"layer":layer,"component":component,"position":position}
                    key=f"{component}/{layer}/{position}"
                    score,_=forward([r["anchor"] for r in p["pairs"]], intervention=(site,p["good"][key]))
                    sweep.append({"site":site, **compare_effect(p["pairs"],p["badMargin"],score,p["goodMargin"])})
            write(args.out / "progress.json", {"stage":"sweep","layersCompleted":layer+1,"layers":len(layers)})
            if layer%6==5:
                print(json.dumps({"stage":"sweep","layersCompleted":layer+1}),flush=True)
    write(args.out / "activation-sweep.json", {"group":"discovery","sites":sweep})
    candidates=[r for r in sweep if r['site']['component']!='residual'
                and r['site']['layer']<len(layers)-2
                and r['groups']['same_semantics_paraphrase']['meanSignedDelta'] is not None]
    ranked=sorted(candidates,key=lambda r:-r['groups']['same_semantics_paraphrase']['meanSignedDelta'])
    source_ranked=[r for r in ranked if r['site']['position']!='last']
    top=source_ranked[:1]
    for row in ranked:
        if all(abs(row['site']['layer']-other['site']['layer'])>=2 for other in top):
            top.append(row)
        if len(top)==3:
            break
    write(args.out / "localization-selection.json", {"sites":top,"frozenBeforeConfirmation":True})
    controls=[]
    if "confirmation" in prepared:
        p=prepared["confirmation"]
        for rank,location in enumerate(top):
            site=location['site'];key=f"{site['component']}/{site['layer']}/{site['position']}"
            original=p['bad'][key].float();donor=p['good'][key].float()
            delta=donor-original
            rng=np.random.default_rng(1104+rank)
            random=torch.from_numpy(rng.normal(size=delta.shape)).float()
            random=random/random.norm(dim=-1,keepdim=True).clamp_min(1e-12)*delta.norm(dim=-1,keepdim=True)
            shuffled=[];valid=[]
            for i,pair in enumerate(p['pairs']):
                others=[j for j,v in enumerate(p['pairs']) if j!=i and v['task']==pair['task'] and v['label']==pair['label']]
                shuffled.append(p['good'][key][others[0]] if others else p['bad'][key][i])
                valid.append(bool(others))
            experiments=[('denoise',donor,False,None),('self',original,False,None),
                         ('random_norm',original+random,False,None),
                         ('shuffled_same_task_label',torch.stack(shuffled),False,valid),
                         ('reverse',original,True,None)]
            for mode,replacement,reverse,mask in experiments:
                batch=[r['donor' if reverse else 'anchor'] for r in p['pairs']]
                score,_=forward(batch,intervention=(site,replacement))
                before=p['goodMargin'] if reverse else p['badMargin']
                target=p['badMargin'] if reverse else p['goodMargin']
                record={'site':site,'mode':mode,**compare_effect(p['pairs'],before,score,target,mask)}
                if mode=='self':
                    record['maxAbsoluteDelta']=max(abs(a-b) for a,b in zip(before,score,strict=True))
                controls.append(record)
    write(args.out / "confirmation.json", {"experiments":controls})
    versions_ok=all(p._version==versions[name] for name,p in model.named_parameters())
    source_ok=digest(args.checkpoint / 'adapter/adapter_model.safetensors')==original_sha
    noop=max((r['maxAbsoluteDelta'] for r in controls if r['mode']=='self'),default=0)
    if not versions_ok or not source_ok or noop>.25:
        raise ValueError('weight immutability or no-op control failed')
    summary={'status':'complete','sourceAdapterUnchanged':source_ok,'parameterVersionsUnchanged':versions_ok,
             'weightsEdited':False,'modelExported':False,'deploymentEnabled':False,
             'allParametersFrozen':all(not p.requires_grad for p in model.parameters()),
             'challengeReplay':replay,'behaviorRows':len(rows),'behaviorCorrect':sum(r['correct'] for r in behavior),
             'eligiblePairs':{k:len(v['pairs']) for k,v in prepared.items()},'excludedFamilies':excluded,
             'topSites':[{k:v for k,v in r.items() if k!='cases'} for r in top],
             'confirmation':[{k:v for k,v in r.items() if k!='cases'} for r in controls],
             'noopMaxAbsoluteDelta':noop,'forwardBatches':forwards,
             'totalSeconds':time.monotonic()-started,'peakAllocatedMiB':torch.cuda.max_memory_allocated()/1024**2,
             'limits':protocol['limits']}
    write(args.out / 'summary.json',summary)
    write(args.out / 'progress.json',{'stage':'complete','weightsEdited':False})
    print(json.dumps({'stage':'complete','seconds':summary['totalSeconds'],'forwards':forwards}),flush=True)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['base','checkpoint','cases','challenge','out']:
        parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--batch-size',type=int,default=16)
    parser.add_argument('--path-from',type=Path,help='Conditional path check using already frozen candidate sites')
    parser.add_argument('--matched-donor-controls',action='store_true',help='Additional unrelated same-task/label controls in path mode')
    args=parser.parse_args()
    existed=args.out.exists()
    try:
        run(args)
    except Exception as exc:
        if not existed and args.out.is_dir():
            write(args.out/'failure.json',{'type':type(exc).__name__,'message':str(exc)[:300]})
        raise
