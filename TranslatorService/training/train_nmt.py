"""Step 3: train our Transformer translation model (en <-> es <-> fr) on the GPU or CPU.

Reads training/data/prepared (from prepare_data.py). Every --eval-minutes it scores the dev
set with BLEU, saves a resumable checkpoint, and exports the best model so far to
models/nmt-en-es-fr/, which the API uses. Training can be stopped at any time:
  * create the file training/STOP (or press Ctrl+C) -> saves and exits cleanly
  * run again with --resume to continue

--mix sets how often each corpus is seen per epoch (e.g. Tatoeba=3 repeats the short everyday
sentences 3 times, Europarl=0.3 uses a random 30% of the parliament text each epoch).

Usage:  python training/train_nmt.py --hours 8
        python training/train_nmt.py --hours 4 --resume
GPU:    a CUDA build of PyTorch is needed (see README, "Training on a GPU").
"""

from __future__ import annotations

import argparse
import json
import math
import random
import shutil
import sys
import time
from pathlib import Path

import numpy as np
import sacrebleu
import sentencepiece as spm
import torch
import torch.nn.functional as F

ROOT = Path(__file__).resolve().parent
SERVICE = ROOT.parent
sys.path.insert(0, str(SERVICE))
from translator.nmt import NMTConfig, TransformerNMT  # noqa: E402

DATA = ROOT / "data" / "prepared"
CKPT_DIR = ROOT / "checkpoints"
EXPORT_DIR = SERVICE / "models" / "nmt-en-es-fr"
LOG = ROOT / "logs" / "train_nmt.log"
STOP_FILE = ROOT / "STOP"
LANGS = ["en", "es", "fr"]
DIRECTIONS = [(s, t) for s in LANGS for t in LANGS if s != t]
CORPORA = ["Tatoeba", "NewsCommentary", "Europarl", "Domain", "Names"]  # same order as prepare_data.py


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


class Data:
    def __init__(self, sp: spm.SentencePieceProcessor):
        self.src = np.fromfile(DATA / "train_src.bin", dtype=np.uint16)
        self.tgt = np.fromfile(DATA / "train_tgt.bin", dtype=np.uint16)
        self.src_off = np.load(DATA / "train_src_off.npy")
        self.tgt_off = np.load(DATA / "train_tgt_off.npy")
        self.dirs = np.load(DATA / "train_dir.npy")
        self.n = len(self.dirs)
        corpus_file = DATA / "train_corpus.npy"
        self.corpus = np.load(corpus_file) if corpus_file.is_file() else np.zeros(self.n, np.uint8)
        self.src_len = np.diff(self.src_off) + 2  # + target tag + eos
        self.tgt_len = np.diff(self.tgt_off) + 1  # + bos / eos
        self.tags = [sp.piece_to_id(f"<2{t}>") for _, t in DIRECTIONS]

    def epoch_examples(self, mix: dict[str, float], rng: np.random.Generator) -> np.ndarray:
        """Example indices for one epoch, with each corpus repeated / subsampled by its weight."""
        if not mix:
            return np.arange(self.n)
        parts = []
        for ci, name in enumerate(CORPORA):
            idx = np.flatnonzero(self.corpus == ci)
            w = mix.get(name, 1.0)
            if not len(idx):
                continue
            parts += [idx] * int(w)
            frac = w - int(w)
            if frac > 0:
                parts.append(rng.choice(idx, int(len(idx) * frac), replace=False))
        return np.concatenate(parts)

    def batches(self, examples: np.ndarray, max_tokens: int, rng: np.random.Generator) -> list[np.ndarray]:
        """Length-bucketed batches with at most max_tokens (padded) per side."""
        noise = rng.random(len(examples))  # random tie-breaking -> different batches each epoch
        order = examples[np.lexsort((noise, self.tgt_len[examples], self.src_len[examples]))]
        n = len(order)
        batches, start = [], 0
        while start < n:
            end = start
            longest = 0
            while end < n:
                i = order[end]
                longest = max(longest, self.src_len[i], self.tgt_len[i])
                if longest * (end - start + 1) > max_tokens:
                    break
                end += 1
            end = max(end, start + 1)
            batches.append(order[start:end])
            start = end
        rng.shuffle(batches)
        return batches

    def tensors(self, idx: np.ndarray, c: NMTConfig):
        b = len(idx)
        s_max = int(self.src_len[idx].max())
        t_max = int(self.tgt_len[idx].max())
        src = np.full((b, s_max), c.pad_id, dtype=np.int64)
        tgt_in = np.full((b, t_max), c.pad_id, dtype=np.int64)
        tgt_out = np.full((b, t_max), c.pad_id, dtype=np.int64)
        for row, i in enumerate(idx):
            s = self.src[self.src_off[i] : self.src_off[i + 1]]
            t = self.tgt[self.tgt_off[i] : self.tgt_off[i + 1]]
            src[row, 0] = self.tags[self.dirs[i]]
            src[row, 1 : 1 + len(s)] = s
            src[row, 1 + len(s)] = c.eos_id
            tgt_in[row, 0] = c.bos_id
            tgt_in[row, 1 : 1 + len(t)] = t
            tgt_out[row, : len(t)] = t
            tgt_out[row, len(t)] = c.eos_id
        return torch.from_numpy(src), torch.from_numpy(tgt_in), torch.from_numpy(tgt_out)


def load_dev(path: Path, per_direction: int) -> dict[tuple[str, str], list[tuple[str, str]]]:
    by_dir: dict[tuple[str, str], list[tuple[str, str]]] = {d: [] for d in DIRECTIONS}
    with open(path, encoding="utf-8") as f:
        for line in f:
            s, t, x, y, _corpus = line.rstrip("\n").split("\t")
            by_dir[(s, t)].append((x, y))
    rng = random.Random(0)
    return {d: rng.sample(v, min(per_direction, len(v))) for d, v in by_dir.items()}


def translate(model, sp, sentences: list[str], target: str, beam: int = 1, batch: int = 32):
    c = model.config
    tag = sp.piece_to_id(f"<2{target}>")
    tags = [sp.piece_to_id(f"<2{l}>") for l in LANGS]
    out = []
    for i in range(0, len(sentences), batch):
        ids = [[tag] + x[: c.max_positions // 2] + [c.eos_id] for x in sp.encode(sentences[i : i + batch])]
        width = max(map(len, ids))
        src = torch.tensor([x + [c.pad_id] * (width - len(x)) for x in ids], device=model.embed.weight.device)
        out.extend(sp.decode(model.generate(src, beam=beam, banned_ids=tags)))
    return out


def evaluate(model, sp, dev) -> tuple[float, dict]:
    model.eval()
    per_dir, all_hyp, all_ref = {}, [], []
    for (s, t), pairs in dev.items():
        hyps = translate(model, sp, [x for x, _ in pairs], t)
        refs = [y for _, y in pairs]
        per_dir[f"{s}-{t}"] = round(sacrebleu.corpus_bleu(hyps, [refs]).score, 1)
        all_hyp += hyps
        all_ref += refs
    model.train()
    return sacrebleu.corpus_bleu(all_hyp, [all_ref]).score, per_dir


def export(model, sp_path: Path, info: dict) -> None:
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    tmp = EXPORT_DIR / "model.pt.tmp"
    torch.save({k: (v.half() if v.is_floating_point() else v).cpu() for k, v in model.state_dict().items()}, tmp)
    tmp.replace(EXPORT_DIR / "model.pt")
    shutil.copy(sp_path, EXPORT_DIR / "spm.model")
    with open(EXPORT_DIR / "config.json", "w", encoding="utf-8") as f:
        json.dump({"model": model.config.to_dict(), "languages": LANGS, "training": info}, f, indent=2)


def keep_awake() -> None:
    """Ask Windows not to sleep while this process runs (released automatically on exit)."""
    if sys.platform == "win32":
        import ctypes

        ES_CONTINUOUS, ES_SYSTEM_REQUIRED = 0x80000000, 0x00000001
        ctypes.windll.kernel32.SetThreadExecutionState(ES_CONTINUOUS | ES_SYSTEM_REQUIRED)


def main() -> None:
    keep_awake()
    p = argparse.ArgumentParser()
    p.add_argument("--hours", type=float, default=8.0)
    p.add_argument("--resume", action="store_true")
    p.add_argument("--max-tokens", type=int, default=6000, help="tokens per micro-batch")
    p.add_argument("--accum", type=int, default=1, help="micro-batches per optimizer step")
    p.add_argument("--device", default="auto", help="auto, cpu or cuda")
    p.add_argument("--mix", default="", help='corpus weights, e.g. "Tatoeba=3,NewsCommentary=1,Europarl=0.3"')
    p.add_argument("--new-epoch", action="store_true",
                   help="with --resume: start a fresh epoch (use after changing --mix or the data)")
    p.add_argument("--reset-best", action="store_true",
                   help="with --resume: forget the best dev BLEU so far (fine-tuning on new data)")
    # CPU training gets only a few thousand steps, so warm up quickly
    p.add_argument("--lr", type=float, default=1e-3)
    p.add_argument("--warmup", type=int, default=400)
    p.add_argument("--label-smoothing", type=float, default=0.1)
    p.add_argument("--threads", type=int, default=4)
    p.add_argument("--eval-minutes", type=float, default=30)
    p.add_argument("--dev-per-direction", type=int, default=60)
    p.add_argument("--d-model", type=int, default=256)
    p.add_argument("--ff", type=int, default=1024)
    p.add_argument("--heads", type=int, default=4)
    p.add_argument("--enc-layers", type=int, default=6)
    p.add_argument("--dec-layers", type=int, default=2)
    p.add_argument("--benchmark", type=int, default=0, help="run N steps, print speed, exit")
    args = p.parse_args()

    torch.set_num_threads(args.threads)
    torch.manual_seed(1)
    device = torch.device("cuda" if args.device == "auto" and torch.cuda.is_available()
                          else "cpu" if args.device == "auto" else args.device)
    mix = {k: float(v) for k, v in (kv.split("=") for kv in args.mix.split(",") if kv)}
    unknown = set(mix) - set(CORPORA)
    if unknown:
        p.error(f"unknown corpus in --mix: {unknown}; choose from {CORPORA}")
    sp_path = DATA / "spm.model"
    sp = spm.SentencePieceProcessor(model_file=str(sp_path))
    data = Data(sp)
    dev = load_dev(DATA / "dev.tsv", args.dev_per_direction)

    config = NMTConfig(
        vocab_size=sp.get_piece_size(), d_model=args.d_model, ff=args.ff, heads=args.heads,
        enc_layers=args.enc_layers, dec_layers=args.dec_layers,
    )
    model = TransformerNMT(config).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=args.lr, betas=(0.9, 0.98), eps=1e-9, weight_decay=1e-4)
    state = {"step": 0, "epoch": 0, "batch_in_epoch": 0, "best_bleu": -1.0, "seconds": 0.0}

    CKPT_DIR.mkdir(parents=True, exist_ok=True)
    last_ckpt = CKPT_DIR / "last.pt"
    if args.resume and last_ckpt.is_file():
        ck = torch.load(last_ckpt, map_location="cpu", weights_only=False)
        model.load_state_dict(ck["model"])
        opt.load_state_dict(ck["opt"])  # moves optimizer state to the model's device
        state = ck["state"]
        if args.new_epoch:
            state["epoch"] += 1
            state["batch_in_epoch"] = 0
        if args.reset_best:
            state["best_bleu"] = -1.0
        log(f"resumed at step {state['step']} (epoch {state['epoch']}, best dev BLEU {state['best_bleu']:.2f})")
    dev_name = torch.cuda.get_device_name(0) if device.type == "cuda" else f"CPU, {args.threads} threads"
    log(f"model: {model.num_parameters() / 1e6:.1f}M parameters, {data.n:,} training examples, "
        f"device {dev_name}, {args.max_tokens}x{args.accum} tokens per step, mix {mix or 'all once'}")

    def lr_at(step: int) -> float:
        step = max(step, 1)
        return args.lr * min(step / args.warmup, math.sqrt(args.warmup / step))

    def save_checkpoint() -> None:
        tmp = CKPT_DIR / "last.pt.tmp"
        torch.save({"model": model.state_dict(), "opt": opt.state_dict(), "state": state,
                    "config": config.to_dict()}, tmp)
        tmp.replace(last_ckpt)

    def eval_and_save() -> None:
        bleu, per_dir = evaluate(model, sp, dev)
        sample = translate(model, sp, ["The car needs a new battery.", "hi my name is chanakyha",
                                       "Our dealership is closed on Monday for maintenance.",
                                       "¿Dónde está la estación de tren?"], "fr")
        improved = bleu > state["best_bleu"]
        if improved:
            state["best_bleu"] = bleu
            export(model, sp_path, {"step": state["step"], "dev_bleu": round(bleu, 2),
                                    "dev_bleu_per_direction": per_dir,
                                    "hours": round(state["seconds"] / 3600, 2)})
        save_checkpoint()
        log(f"EVAL step {state['step']}: dev BLEU {bleu:.2f} {per_dir}"
            f"{'  -> new best, exported' if improved else ''}")
        log(f"     sample ->fr: {sample}")

    model.train()
    deadline = time.time() + args.hours * 3600
    next_eval = time.time() + args.eval_minutes * 60
    window_tokens, window_loss, window_start = 0, 0.0, time.time()
    stop = False
    try:
        while not stop:
            # Same epoch number -> same rng -> same batches, so --resume continues exactly.
            rng = np.random.default_rng(1 + state["epoch"])
            batches = data.batches(data.epoch_examples(mix, rng), args.max_tokens, rng)
            for bi in range(state["batch_in_epoch"], len(batches), args.accum):
                t0 = time.time()
                group = batches[bi : bi + args.accum]
                ntok = int(sum(data.tgt_len[idx].sum() for idx in group))  # non-pad target tokens
                for g in opt.param_groups:
                    g["lr"] = lr_at(state["step"] + 1)
                opt.zero_grad(set_to_none=True)
                step_loss = 0.0
                for idx in group:
                    src, tgt_in, tgt_out = (t.to(device, non_blocking=True) for t in data.tensors(idx, config))
                    logits = model(src, tgt_in)
                    loss = F.cross_entropy(
                        logits.view(-1, logits.size(-1)), tgt_out.view(-1),
                        ignore_index=config.pad_id, label_smoothing=args.label_smoothing, reduction="sum",
                    ) / ntok
                    loss.backward()
                    step_loss += loss.item()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
                opt.step()
                state["step"] += 1
                state["batch_in_epoch"] = bi + len(group)
                state["seconds"] += time.time() - t0
                window_tokens += ntok
                window_loss += step_loss * ntok

                if args.benchmark and state["step"] >= args.benchmark:
                    el = state["seconds"]
                    print(f"BENCHMARK {state['step']} steps: {window_tokens / el:.0f} target tok/s, "
                          f"{el / state['step']:.2f} s/step, loss {window_loss / window_tokens:.3f}, "
                          f"batch build {time.time() - window_start - el:.0f}s")
                    return
                if state["step"] % 100 == 0:
                    el = time.time() - window_start
                    log(f"step {state['step']} epoch {state['epoch']} batch {bi + 1}/{len(batches)} "
                        f"loss {window_loss / window_tokens:.3f} lr {lr_at(state['step']):.2e} "
                        f"{window_tokens / el:.0f} tok/s")
                    window_tokens, window_loss, window_start = 0, 0.0, time.time()
                now = time.time()
                if now >= next_eval:
                    eval_and_save()
                    next_eval = time.time() + args.eval_minutes * 60
                if now >= deadline or STOP_FILE.exists():
                    stop = True
                    break
            else:
                state["epoch"] += 1
                state["batch_in_epoch"] = 0
                log(f"epoch {state['epoch']} finished")
    except KeyboardInterrupt:
        log("interrupted")
    eval_and_save()
    if STOP_FILE.exists():
        STOP_FILE.unlink()
    log(f"stopped at step {state['step']}; best dev BLEU {state['best_bleu']:.2f}; "
        f"model in {EXPORT_DIR}")


if __name__ == "__main__":
    main()
