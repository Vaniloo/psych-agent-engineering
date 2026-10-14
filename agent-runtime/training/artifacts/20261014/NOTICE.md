# Attribution and redistribution scope

These are development decision models and frozen decision courses from Psych Agent.
They are not generative chat models, clinical labels, or independently reviewed medical data.

## Models

- Qwen LoRA adapters modify Qwen/Qwen3-Reranker-4B, published by the Qwen team under Apache-2.0. Frozen base revision: 22e683669bc0f0bd69640a1354a6d0aebcfeede5. Official base weights are not bundled. Project modifications: independent one-epoch LoRA training and task-specific calibration on the released courses.
- psych-executor-domain-v6 modifies convaiinnovations/laya-multilingual, published by Convai Innovations under Apache-2.0. It includes the mmBERT-base backbone published by jhu-clsp with the MIT license identified in its model card. Project modifications: domain fine-tuning, finite task contracts, calibrated deployment policies. Upstream model repositories: https://huggingface.co/convaiinnovations/laya-multilingual and https://huggingface.co/jhu-clsp/mmBERT-base.
- Model artifacts and the project's adapter modifications are distributed under Apache-2.0, retaining upstream attribution and embedded component licenses. See licenses/Apache-2.0.txt and licenses/mmBERT-LICENSE.txt.

## Datasets

The released courses are structured project-policy decisions: authored/programmatic fixtures, source-aware counterfactuals and fictional goal themes. Source metadata and frozen split bytes are preserved. They do not include this application's account database, actual app conversations, teacher API credentials, request headers, IP/geolocation metadata, or the original public datasets in full.

Project-authored dataset additions are provided under CC-BY-SA-4.0: https://creativecommons.org/licenses/by-sa/4.0/legalcode . Third-party source material retains its respective license. This notice does not replace or relicense the upstream databases; comply with applicable upstream conditions when redistributing their derivatives.

Public dialogue themes were used in the earlier curriculum from which some goal-theme rows were derived:

| Upstream | Frozen revision | Upstream license | Changes |
| --- | --- | --- | --- |
| qiuhuachuan/PsyDial-D4 | 0ceef5ac19fb8174baa6be31ce1b2637711cb4c2 | Apache-2.0 | Dialogue-theme extraction, fictional goal/action construction, policy variants |
| CAS-SIAT-XinHai/CPsyCoun | 8fe0fa0f77630b921932bdaa6bae7481bc611cf2 | CC-BY-SA-4.0 | Dialogue-theme extraction, fictional goal/action construction, policy variants |
| allenai/WildChat-1M | 7d6490e462285cf85d91eabea0f9a954fbddcd1f | ODC-BY-1.0 | Chinese content/theme selection from the first shard, fictional goal/action construction |

Dataset pages: https://huggingface.co/datasets/qiuhuachuan/PsyDial-D4 ; https://huggingface.co/datasets/CAS-SIAT-XinHai/CPsyCoun ; https://huggingface.co/datasets/allenai/WildChat-1M . The upstream data cards and WildChat license are retained in licenses/. CPsyCoun's selected HF data card uses ShareAlike; the different GitHub annotation is not substituted for it. The project author's additions are shared under that same CC-BY-SA-4.0 license. Source-theme provenance is not claimed to be independently verified line-by-line.

ToolACE was inspected but not used to train these artifacts. LongMemEval is not included in this release or used in these training courses. Frozen challenge rows are authored development fixtures; do not train on them and then present the resulting score as independent test accuracy.

For exact model data sizes, hashes and calibration, see manifest.json and models/*/training-report.json. A reviewed/programmatic marker indicates a project policy label, not independent human or clinical review. In particular, the Laya executor course retains teacher_draft rows and the historic deployment/promotion reports; publishing it does not upgrade those labels or modify the gate.

Publication portability change: adapter_config.json base_model_name_or_path was changed from a server cache path to the official Qwen model ID, with the frozen revision recorded. Adapter weight bytes, training reports, calibration policies and dataset split bytes remain unchanged. rl_agent_config.json training fields describe the upstream Laya checkpoint; project results are in training-report.json/deployment.json.

Upstream model/data card text is preserved with line endings and trailing whitespace normalized for Git.
