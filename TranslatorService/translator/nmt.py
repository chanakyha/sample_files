"""Our own Transformer translation model (encoder-decoder), written from scratch in PyTorch.

Used both by training/train_nmt.py and by the runtime engine.

Design (Vaswani et al. 2017, with common modern choices):
  * pre-layer-norm, sinusoidal positions, shared (tied) embeddings for encoder, decoder and
    output projection, one SentencePiece vocabulary for all languages
  * deep encoder / shallow decoder (the shipped model: 6 / 2 layers, set by train_nmt.py):
    nearly the same quality as a deep decoder, much faster decoding
  * the target language is chosen by a tag token (<2fr>, <2es>, <2en>) at the start of the
    source sentence, so one model serves every direction
  * incremental decoding with key/value caching, greedy and beam search
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import torch
import torch.nn.functional as F
from torch import nn


@dataclass
class NMTConfig:
    vocab_size: int = 16000
    d_model: int = 256
    heads: int = 4
    ff: int = 1024
    enc_layers: int = 6
    dec_layers: int = 3
    dropout: float = 0.1
    max_positions: int = 1024
    pad_id: int = 0
    unk_id: int = 1
    bos_id: int = 2
    eos_id: int = 3

    def to_dict(self) -> dict:
        return asdict(self)


def sinusoidal_positions(n: int, d: int) -> torch.Tensor:
    pos = torch.arange(n, dtype=torch.float32).unsqueeze(1)
    div = torch.exp(torch.arange(0, d, 2, dtype=torch.float32) * (-math.log(10000.0) / d))
    pe = torch.zeros(n, d)
    pe[:, 0::2] = torch.sin(pos * div)
    pe[:, 1::2] = torch.cos(pos * div)
    return pe


class SelfAttention(nn.Module):
    def __init__(self, d: int, heads: int, dropout: float):
        super().__init__()
        self.heads, self.dk = heads, d // heads
        self.qkv = nn.Linear(d, 3 * d)
        self.out = nn.Linear(d, d)
        self.dropout = dropout

    def forward(self, x, mask=None, causal=False, cache: dict | None = None):
        b, t, d = x.shape
        q, k, v = self.qkv(x).view(b, t, 3, self.heads, self.dk).permute(2, 0, 3, 1, 4)
        if cache is not None:  # incremental decoding: append this step's keys/values
            if "k" in cache:
                k = torch.cat([cache["k"], k], dim=2)
                v = torch.cat([cache["v"], v], dim=2)
            cache["k"], cache["v"] = k, v
            causal = False  # a single new query may attend to all cached positions
        y = F.scaled_dot_product_attention(
            q, k, v, attn_mask=mask, is_causal=causal,
            dropout_p=self.dropout if self.training else 0.0,
        )
        return self.out(y.transpose(1, 2).reshape(b, t, d))


class CrossAttention(nn.Module):
    def __init__(self, d: int, heads: int, dropout: float):
        super().__init__()
        self.heads, self.dk = heads, d // heads
        self.q = nn.Linear(d, d)
        self.kv = nn.Linear(d, 2 * d)
        self.out = nn.Linear(d, d)
        self.dropout = dropout

    def project_memory(self, memory):
        b, s, _ = memory.shape
        k, v = self.kv(memory).view(b, s, 2, self.heads, self.dk).permute(2, 0, 3, 1, 4)
        return k, v

    def forward(self, x, kv, mask):
        b, t, d = x.shape
        q = self.q(x).view(b, t, self.heads, self.dk).transpose(1, 2)
        k, v = kv
        y = F.scaled_dot_product_attention(
            q, k, v, attn_mask=mask, dropout_p=self.dropout if self.training else 0.0
        )
        return self.out(y.transpose(1, 2).reshape(b, t, d))


class FeedForward(nn.Sequential):
    def __init__(self, d: int, ff: int, dropout: float):
        super().__init__(nn.Linear(d, ff), nn.ReLU(), nn.Dropout(dropout), nn.Linear(ff, d))


class EncoderLayer(nn.Module):
    def __init__(self, c: NMTConfig):
        super().__init__()
        self.norm1, self.norm2 = nn.LayerNorm(c.d_model), nn.LayerNorm(c.d_model)
        self.attn = SelfAttention(c.d_model, c.heads, c.dropout)
        self.ff = FeedForward(c.d_model, c.ff, c.dropout)
        self.drop = nn.Dropout(c.dropout)

    def forward(self, x, mask):
        x = x + self.drop(self.attn(self.norm1(x), mask=mask))
        return x + self.drop(self.ff(self.norm2(x)))


class DecoderLayer(nn.Module):
    def __init__(self, c: NMTConfig):
        super().__init__()
        self.norm1 = nn.LayerNorm(c.d_model)
        self.norm2 = nn.LayerNorm(c.d_model)
        self.norm3 = nn.LayerNorm(c.d_model)
        self.self_attn = SelfAttention(c.d_model, c.heads, c.dropout)
        self.cross_attn = CrossAttention(c.d_model, c.heads, c.dropout)
        self.ff = FeedForward(c.d_model, c.ff, c.dropout)
        self.drop = nn.Dropout(c.dropout)

    def forward(self, x, memory_kv, src_mask, cache=None):
        x = x + self.drop(self.self_attn(self.norm1(x), causal=cache is None, cache=cache))
        x = x + self.drop(self.cross_attn(self.norm2(x), memory_kv, src_mask))
        return x + self.drop(self.ff(self.norm3(x)))


class TransformerNMT(nn.Module):
    def __init__(self, config: NMTConfig):
        super().__init__()
        self.config = c = config
        self.embed = nn.Embedding(c.vocab_size, c.d_model, padding_idx=c.pad_id)
        nn.init.normal_(self.embed.weight, mean=0.0, std=c.d_model ** -0.5)
        with torch.no_grad():
            self.embed.weight[c.pad_id].zero_()
        self.register_buffer("positions", sinusoidal_positions(c.max_positions, c.d_model), persistent=False)
        self.scale = math.sqrt(c.d_model)
        self.drop = nn.Dropout(c.dropout)
        self.encoder = nn.ModuleList(EncoderLayer(c) for _ in range(c.enc_layers))
        self.decoder = nn.ModuleList(DecoderLayer(c) for _ in range(c.dec_layers))
        self.enc_norm = nn.LayerNorm(c.d_model)
        self.dec_norm = nn.LayerNorm(c.d_model)

    # ---- building blocks ----
    def _embed(self, tokens, offset: int = 0):
        t = tokens.size(1)
        return self.drop(self.embed(tokens) * self.scale + self.positions[offset : offset + t])

    def _logits(self, h):
        return F.linear(self.dec_norm(h), self.embed.weight)  # tied output projection

    def encode(self, src):
        mask = (src != self.config.pad_id)[:, None, None, :]  # [B,1,1,S] True = attend
        x = self._embed(src)
        for layer in self.encoder:
            x = layer(x, mask)
        return self.enc_norm(x), mask

    def forward(self, src, tgt_in):
        """Training: logits for every target position (teacher forcing)."""
        memory, mask = self.encode(src)
        x = self._embed(tgt_in)
        for layer in self.decoder:
            x = layer(x, layer.cross_attn.project_memory(memory), mask)
        return self._logits(x)

    # ---- inference ----
    @torch.inference_mode()
    def generate(
        self, src, beam: int = 1, max_len_a: float = 1.5, max_len_b: int = 10,
        len_penalty: float = 1.0, banned_ids: list[int] | None = None,
    ) -> list[list[int]]:
        """Translate a padded batch of source ids. Returns target ids (without bos/eos)."""
        c = self.config
        b = src.size(0)
        src_len = (src != c.pad_id).sum(1)
        max_len = min(int(src_len.max().item() * max_len_a + max_len_b), c.max_positions - 1)
        memory, mask = self.encode(src)
        if beam > 1:
            memory = memory.repeat_interleave(beam, 0)
            mask = mask.repeat_interleave(beam, 0)
        cross = [layer.cross_attn.project_memory(memory) for layer in self.decoder]
        caches = [dict() for _ in self.decoder]
        banned = [c.pad_id, c.bos_id, c.unk_id] + list(banned_ids or [])

        def step(tokens_last, t):
            x = self._embed(tokens_last, offset=t)
            for layer, kv, cache in zip(self.decoder, cross, caches):
                x = layer(x, kv, mask, cache=cache)
            logp = F.log_softmax(self._logits(x[:, -1]).float(), dim=-1)
            logp[:, banned] = -float("inf")
            return logp

        if beam == 1:
            return self._greedy(step, b, max_len)
        return self._beam(step, caches, b, beam, max_len, len_penalty)

    def _greedy(self, step, b, max_len):
        c = self.config
        dev = self.embed.weight.device
        last = torch.full((b, 1), c.bos_id, dtype=torch.long, device=dev)
        out = [[] for _ in range(b)]
        done = torch.zeros(b, dtype=torch.bool, device=dev)
        for t in range(max_len):
            nxt = step(last, t).argmax(-1)
            nxt = torch.where(done, torch.full_like(nxt, c.pad_id), nxt)
            for i, tok in enumerate(nxt.tolist()):
                if not done[i] and tok != c.eos_id:
                    out[i].append(tok)
            done |= nxt == c.eos_id
            if bool(done.all()):
                break
            last = nxt.unsqueeze(1)
        return out

    def _beam(self, step, caches, b, beam, max_len, len_penalty):
        c = self.config
        vocab = c.vocab_size
        dev = self.embed.weight.device
        seqs = [[] for _ in range(b * beam)]
        scores = torch.zeros(b, beam, device=dev)
        scores[:, 1:] = -float("inf")  # all beams start identical; keep only the first
        finished: list[list[tuple[float, list[int]]]] = [[] for _ in range(b)]
        done = [False] * b
        last = torch.full((b * beam, 1), c.bos_id, dtype=torch.long, device=dev)
        for t in range(max_len):
            logp = step(last, t)  # [B*beam, V]
            cand = (scores.view(-1, 1) + logp).view(b, beam * vocab)
            top_s, top_i = cand.topk(2 * beam, dim=1)
            top_s, top_i = top_s.tolist(), top_i.tolist()
            order, tokens, new_scores = [], [], []
            for i in range(b):
                kept = 0
                for s, idx in zip(top_s[i], top_i[i]):
                    if kept == beam:
                        break
                    src_row = i * beam + idx // vocab
                    tok = idx % vocab
                    if done[i] or s == -float("inf"):
                        order.append(i * beam)
                        tokens.append(c.pad_id)
                        new_scores.append(-float("inf"))
                        kept += 1
                        continue
                    if tok == c.eos_id:
                        length = len(seqs[src_row]) + 1
                        finished[i].append((s / length ** len_penalty, seqs[src_row]))
                        continue
                    order.append(src_row)
                    tokens.append(tok)
                    new_scores.append(s)
                    kept += 1
                while kept < beam:  # pad with dead beams if too many candidates ended
                    order.append(i * beam)
                    tokens.append(c.pad_id)
                    new_scores.append(-float("inf"))
                    kept += 1
                if len(finished[i]) >= beam:
                    done[i] = True
            if all(done):
                break
            order_t = torch.tensor(order, device=dev)
            seqs = [seqs[o] + [tk] for o, tk in zip(order, tokens)]
            scores = torch.tensor(new_scores, device=dev).view(b, beam)
            for cache in caches:
                cache["k"] = cache["k"].index_select(0, order_t)
                cache["v"] = cache["v"].index_select(0, order_t)
            last = torch.tensor(tokens, device=dev).unsqueeze(1)
        results = []
        for i in range(b):
            if not finished[i]:  # hit max length: fall back to the best unfinished beam
                best = max(range(beam), key=lambda k: scores[i, k].item())
                finished[i].append((0.0, seqs[i * beam + best]))
            results.append(max(finished[i], key=lambda x: x[0])[1])
        return results

    def num_parameters(self) -> int:
        return sum(p.numel() for p in self.parameters())
