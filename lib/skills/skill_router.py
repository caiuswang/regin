"""SkillRouter inference wrapper (experimental).

Wraps the released `pipizhao/SkillRouter-Embedding-0.6B` bi-encoder and
`pipizhao/SkillRouter-Reranker-0.6B` cross-encoder behind a small Python
API that works on Apple Silicon (MPS), CUDA, and CPU. Inference protocol
mirrors `src/common.py` and `src/run_open_model_eval.py` in
https://github.com/zhengyanzhao1997/SkillRouter so released checkpoints
behave the same way they do in the paper.

Torch and transformers are imported lazily so the rest of regin keeps
working on machines that don't have them installed.
"""

from __future__ import annotations

import os
import threading
from typing import Any

# Avoid tokenizers' fork-after-parallel warning, which can manifest as a deadlock
# under Flask's threaded request handler.
os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")


EMBEDDING_MODEL_ID = "pipizhao/SkillRouter-Embedding-0.6B"
RERANKER_MODEL_ID = "pipizhao/SkillRouter-Reranker-0.6B"

QUERY_INSTRUCTION = (
    "Instruct: Given a coding task description, retrieve the most relevant "
    "skill document that would help an agent complete the task\nQuery:"
)
RERANK_INSTRUCTION = (
    "Given a coding task description, judge whether the skill document "
    "is relevant and useful for completing the task"
)

EMBED_MAX_LEN = 4096
RERANK_MAX_LEN = 4096
EMBED_BATCH = 8
RERANK_BATCH = 4


class DependencyError(RuntimeError):
    """Raised when torch / transformers are not installed."""


def ensure_deps():
    try:
        import numpy  # noqa: F401
        import torch  # noqa: F401
        import transformers  # noqa: F401
    except ImportError as exc:
        raise DependencyError(
            "SkillRouter inference needs torch + transformers + numpy. "
            "Install with: pip install -r requirements-router.txt"
        ) from exc


def get_device():
    """Pick the best available torch device for this machine."""
    ensure_deps()
    import torch
    if torch.cuda.is_available():
        return torch.device("cuda")
    if torch.backends.mps.is_available():
        return torch.device("mps")
    return torch.device("cpu")


def _dtype_for(device) -> Any:
    import torch
    if device.type == "cuda":
        return torch.bfloat16
    if device.type == "mps":
        return torch.float16
    return torch.float32


_emb_state: dict[str, Any] = {}
_rr_state: dict[str, Any] = {}
_emb_lock = threading.Lock()
_rr_lock = threading.Lock()


def _from_pretrained(loader, model_id: str, **kw):
    """Prefer the local HF cache; only hit the network on a true cache miss.

    Avoids deadlocks where transformers' freshness HEAD call goes through
    httpx → SOCKS proxy inside a Flask request thread.
    """
    try:
        return loader.from_pretrained(model_id, local_files_only=True, **kw)
    except (OSError, ValueError):
        return loader.from_pretrained(model_id, **kw)


def _load_embedding(model_id: str):
    if _emb_state.get("model_id") == model_id:
        return _emb_state["model"], _emb_state["tokenizer"], _emb_state["device"]
    ensure_deps()
    from transformers import AutoModel, AutoTokenizer
    with _emb_lock:
        if _emb_state.get("model_id") == model_id:
            return _emb_state["model"], _emb_state["tokenizer"], _emb_state["device"]
        device = get_device()
        dtype = _dtype_for(device)
        tok = _from_pretrained(AutoTokenizer, model_id, trust_remote_code=True, padding_side="left")
        if tok.pad_token is None and tok.eos_token is not None:
            tok.pad_token = tok.eos_token
        model = _from_pretrained(AutoModel, model_id, trust_remote_code=True, dtype=dtype)
        model.to(device).eval()
        _emb_state.update(model_id=model_id, model=model, tokenizer=tok, device=device)
        return model, tok, device


def _load_reranker(model_id: str):
    if _rr_state.get("model_id") == model_id:
        return _rr_state["model"], _rr_state["tokenizer"], _rr_state["device"]
    ensure_deps()
    from transformers import AutoModelForCausalLM, AutoTokenizer
    with _rr_lock:
        if _rr_state.get("model_id") == model_id:
            return _rr_state["model"], _rr_state["tokenizer"], _rr_state["device"]
        device = get_device()
        dtype = _dtype_for(device)
        tok = _from_pretrained(AutoTokenizer, model_id, trust_remote_code=True, padding_side="left")
        if tok.pad_token is None and tok.eos_token is not None:
            tok.pad_token = tok.eos_token
        model = _from_pretrained(AutoModelForCausalLM, model_id, trust_remote_code=True, dtype=dtype)
        model.to(device).eval()
        _rr_state.update(model_id=model_id, model=model, tokenizer=tok, device=device)
        return model, tok, device


def _last_token_pool(last_hidden_states, attention_mask):
    import torch
    left_padding = (attention_mask[:, -1].sum() == attention_mask.shape[0])
    if left_padding:
        return last_hidden_states[:, -1]
    sequence_lengths = attention_mask.sum(dim=1) - 1
    batch_size = last_hidden_states.shape[0]
    return last_hidden_states[
        torch.arange(batch_size, device=last_hidden_states.device),
        sequence_lengths,
    ]


def format_query(text: str, max_chars: int = 2000) -> str:
    """Prepend the SkillRouter query instruction (matches training)."""
    return f"{QUERY_INSTRUCTION}{text[:max_chars]}"


def format_document(name: str, description: str, body: str,
                    desc_max: int = 500, body_max: int = 8000) -> str:
    """Flatten a skill into the `name | description | body` form used in training."""
    return f"{name} | {(description or '')[:desc_max]} | {(body or '')[:body_max]}"


def embed(texts: list[str], *, model_id: str = EMBEDDING_MODEL_ID,
          batch_size: int = EMBED_BATCH, max_length: int = EMBED_MAX_LEN):
    """Encode pre-formatted strings into L2-normalized fp32 numpy vectors."""
    if not texts:
        import numpy as np
        return np.zeros((0, 0), dtype="float32")
    ensure_deps()
    import numpy as np
    import torch
    import torch.nn.functional as F
    model, tok, device = _load_embedding(model_id)
    chunks = []
    for i in range(0, len(texts), batch_size):
        batch = texts[i:i + batch_size]
        enc = tok(batch, padding=True, truncation=True,
                  max_length=max_length, return_tensors="pt")
        enc = {k: v.to(device) for k, v in enc.items()}
        with torch.no_grad():
            out = model(**enc)
            vecs = _last_token_pool(out.last_hidden_state, enc["attention_mask"])
            vecs = F.normalize(vecs, p=2, dim=1)
        chunks.append(vecs.float().cpu().numpy())
    return np.concatenate(chunks, axis=0)


def _rerank_prompt(name: str, desc: str, body: str, query_text: str,
                   desc_max: int = 500, body_max: int = 2000) -> str:
    doc_text = f"{name} | {desc[:desc_max]} | {body[:body_max]}"
    return (
        f"<Instruct>: {RERANK_INSTRUCTION}\n\n"
        f"<Query>: {query_text}\n\n"
        f"<Document>: {doc_text}"
    )


def _rerank_template_tokens(tok):
    prefix = (
        '<|im_start|>system\nJudge whether the Document meets the requirements '
        'based on the Query and the Instruct provided. Note that the answer can '
        'only be "yes" or "no".<|im_end|>\n<|im_start|>user\n'
    )
    suffix = '<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n'
    return (
        tok.encode(prefix, add_special_tokens=False),
        tok.encode(suffix, add_special_tokens=False),
    )


def rerank(query_text: str, candidates: list[dict], *,
           model_id: str = RERANKER_MODEL_ID,
           batch_size: int = RERANK_BATCH,
           max_length: int = RERANK_MAX_LEN) -> list[float]:
    """Score (query, candidate) pairs. Candidates need name/description/body keys."""
    if not candidates:
        return []
    ensure_deps()
    import torch
    model, tok, device = _load_reranker(model_id)
    prefix_ids, suffix_ids = _rerank_template_tokens(tok)
    yes_id = tok.convert_tokens_to_ids("yes")
    no_id = tok.convert_tokens_to_ids("no")

    texts = [
        _rerank_prompt(c.get("name", ""), c.get("description", "") or "",
                       c.get("body", "") or "", query_text)
        for c in candidates
    ]
    tokenized: list[list[int]] = []
    for text in texts:
        body_max = max_length - len(prefix_ids) - len(suffix_ids)
        ids = tok(text, padding=False, truncation=True,
                  max_length=body_max, return_attention_mask=False)["input_ids"]
        tokenized.append(prefix_ids + ids + suffix_ids)

    pad_id = tok.pad_token_id if tok.pad_token_id is not None else 0
    scores: list[float] = []
    for i in range(0, len(tokenized), batch_size):
        batch = tokenized[i:i + batch_size]
        max_len = max(len(x) for x in batch)
        padded, masks = [], []
        for ids in batch:
            pad = max_len - len(ids)
            padded.append([pad_id] * pad + ids)
            masks.append([0] * pad + [1] * len(ids))
        input_ids = torch.tensor(padded, dtype=torch.long, device=device)
        attention_mask = torch.tensor(masks, dtype=torch.long, device=device)
        with torch.no_grad():
            logits = model(input_ids=input_ids, attention_mask=attention_mask).logits[:, -1, :]
            batch_scores = (logits[:, yes_id] - logits[:, no_id]).float().cpu().tolist()
        scores.extend(batch_scores)
    return scores
