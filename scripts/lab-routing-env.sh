#!/usr/bin/env bash
# Source before ANY installs, downloads, preprocessing, training, or inference.
# Deliberately do not change HOME or other global account settings.
export PSYCH_ROUTE_ROOT="${PSYCH_ROUTE_ROOT:-/home/ubuntu/data/psych-agent-routing-20261009}"
unset PYTHONPATH PIP_TARGET PIP_PREFIX PYTHONUSERBASE
case "$PSYCH_ROUTE_ROOT" in
  /home/ubuntu/data/*) ;;
  *) echo 'PSYCH_ROUTE_ROOT must be beneath /home/ubuntu/data' >&2; return 1 2>/dev/null || exit 1 ;;
esac
mkdir -p "$PSYCH_ROUTE_ROOT"/{cache/{pip,huggingface,torch,triton,cuda,uv,pycache,xdg},tmp,logs,models,datasets,runs,src}
export TMPDIR="$PSYCH_ROUTE_ROOT/tmp"
export PIP_CACHE_DIR="$PSYCH_ROUTE_ROOT/cache/pip"
export HF_HOME="$PSYCH_ROUTE_ROOT/cache/huggingface"
export HF_HUB_CACHE="$HF_HOME/hub"
export HUGGINGFACE_HUB_CACHE="$HF_HUB_CACHE"
export HF_DATASETS_CACHE="$HF_HOME/datasets"
unset TRANSFORMERS_CACHE
export XDG_CACHE_HOME="$PSYCH_ROUTE_ROOT/cache/xdg"
export TORCH_HOME="$PSYCH_ROUTE_ROOT/cache/torch"
export TORCHINDUCTOR_CACHE_DIR="$PSYCH_ROUTE_ROOT/cache/inductor"
export TRITON_CACHE_DIR="$PSYCH_ROUTE_ROOT/cache/triton"
export CUDA_CACHE_PATH="$PSYCH_ROUTE_ROOT/cache/cuda"
export UV_CACHE_DIR="$PSYCH_ROUTE_ROOT/cache/uv"
export PYTHONPYCACHEPREFIX="$PSYCH_ROUTE_ROOT/cache/pycache"
export LAYA_CUDA_AMP=fp16
export TOKENIZERS_PARALLELISM=false
export OMP_NUM_THREADS=8
