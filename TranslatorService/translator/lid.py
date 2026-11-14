"""Our own language-identification model for the 24 EU languages (trained by train_lid.py).

fastText-style classifier, implemented from scratch:
  features  = lowercased words + character 1-4-grams of each word (with < > boundaries),
              hashed into a fixed number of buckets with CRC32 (deterministic)
  model     = mean of feature embeddings (EmbeddingBag) -> linear layer -> softmax
"""

from __future__ import annotations

import re
import zlib

import torch
from torch import nn

_WORD = re.compile(r"[^\W\d_]+(?:['’][^\W\d_]+)*", re.UNICODE)  # letters, keeps l'amour / don't


def features(
    text: str, buckets: int, max_n: int = 4, min_n: int = 2, word_weight: int = 2
) -> list[int]:
    """Hashed feature ids for a text: whole words + char n-grams of each word.

    Single characters (min_n=1) carry little language information and dilute the average
    on very short texts, so they are skipped; whole words count word_weight times.
    """
    ids = []
    mask = buckets - 1
    for word in _WORD.findall(text.lower()):
        ids.extend([zlib.crc32(b"w:" + word.encode("utf-8")) & mask] * word_weight)
        w = f"<{word}>"
        for n in range(min_n, max_n + 1):
            for i in range(len(w) - n + 1):
                ids.append(zlib.crc32(w[i : i + n].encode("utf-8")) & mask)
    return ids


class LIDModel(nn.Module):
    def __init__(self, buckets: int, dim: int, num_classes: int):
        super().__init__()
        self.buckets = buckets
        self.embed = nn.EmbeddingBag(buckets, dim, mode="mean", sparse=True)
        self.out = nn.Linear(dim, num_classes)
        nn.init.uniform_(self.embed.weight, -1.0 / dim, 1.0 / dim)

    def forward(self, flat_ids: torch.Tensor, offsets: torch.Tensor) -> torch.Tensor:
        return self.out(self.embed(flat_ids, offsets))
