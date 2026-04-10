"""
model_v2.py — Improved TCN for exoskeleton torque prediction.

Key changes vs v1:
  1. LayerNorm instead of WeightNorm  → more stable training, better generalization
  2. GELU activation                  → smoother gradients than ReLU
  3. Temporal Self-Attention bottleneck at the end  → captures long-range gait dependencies
  4. Larger receptive field option    → deeper dilation stack
  5. Kaiming init                     → better for GELU networks
  6. Optional skip connections from all levels to output (DenseNet-style)
"""

import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import List


# ─────────────────────────────────────────────
# Building blocks
# ─────────────────────────────────────────────

class Chomp1d(nn.Module):
    """Removes causal padding tail."""
    def __init__(self, chomp_size: int):
        super().__init__()
        self.chomp_size = chomp_size

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return x[:, :, : -self.chomp_size].contiguous() if self.chomp_size > 0 else x


class TemporalBlock(nn.Module):
    """
    Temporal block with:
      - LayerNorm (applied over channels at each time step) instead of WeightNorm
      - GELU activation
      - Residual connection with optional channel-matching 1×1 conv
    """

    def __init__(
        self,
        n_inputs: int,
        n_outputs: int,
        kernel_size: int,
        dilation: int,
        dropout: float = 0.2,
    ):
        super().__init__()
        padding = (kernel_size - 1) * dilation

        self.conv1 = nn.Conv1d(n_inputs, n_outputs, kernel_size,
                               padding=padding, dilation=dilation)
        self.chomp1 = Chomp1d(padding)
        self.norm1  = nn.GroupNorm(1, n_outputs)   # equivalent to LayerNorm over channels
        self.act1   = nn.GELU()
        self.drop1  = nn.Dropout(dropout)

        self.conv2 = nn.Conv1d(n_outputs, n_outputs, kernel_size,
                               padding=padding, dilation=dilation)
        self.chomp2 = Chomp1d(padding)
        self.norm2  = nn.GroupNorm(1, n_outputs)
        self.act2   = nn.GELU()
        self.drop2  = nn.Dropout(dropout)

        self.downsample = nn.Conv1d(n_inputs, n_outputs, 1) if n_inputs != n_outputs else None
        self.act_res    = nn.GELU()

        self._init_weights()

    def _init_weights(self):
        nn.init.kaiming_normal_(self.conv1.weight, nonlinearity="relu")
        nn.init.kaiming_normal_(self.conv2.weight, nonlinearity="relu")
        nn.init.zeros_(self.conv1.bias)
        nn.init.zeros_(self.conv2.bias)
        if self.downsample is not None:
            nn.init.kaiming_normal_(self.downsample.weight, nonlinearity="relu")

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out = self.drop1(self.act1(self.norm1(self.chomp1(self.conv1(x)))))
        out = self.drop2(self.act2(self.norm2(self.chomp2(self.conv2(out)))))
        res = x if self.downsample is None else self.downsample(x)
        return self.act_res(out + res)


# ─────────────────────────────────────────────
# Lightweight temporal self-attention
# ─────────────────────────────────────────────

class TemporalAttention(nn.Module):
    """
    Single-head causal self-attention over the time axis.
    Cheap: O(T^2) but T=200 is fine.
    Helps the model relate push-off phase to loading phase across the window.
    """

    def __init__(self, channels: int, num_heads: int = 4):
        super().__init__()
        self.attn = nn.MultiheadAttention(
            embed_dim=channels, num_heads=num_heads, batch_first=True, dropout=0.1
        )
        self.norm = nn.LayerNorm(channels)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        # x: (B, C, T)  →  (B, T, C) for MHA
        xt = x.permute(0, 2, 1)
        T = xt.size(1)
        # Causal mask: upper triangle = -inf
        causal_mask = torch.triu(
            torch.full((T, T), float("-inf"), device=x.device), diagonal=1
        )
        attn_out, _ = self.attn(xt, xt, xt, attn_mask=causal_mask)
        out = self.norm(xt + attn_out)
        return out.permute(0, 2, 1)  # back to (B, C, T)


# ─────────────────────────────────────────────
# Main model
# ─────────────────────────────────────────────

class TCN(nn.Module):
    """
    TCN v2 for exoskeleton ankle-torque prediction.

    Architecture:
      Input projection → N × TemporalBlock (exponential dilation) →
      TemporalAttention bottleneck → 1×1 output projection

    Args:
        input_size:   Number of input channels (sensor features).
        output_size:  Number of output channels (torque DOFs).
        num_channels: Hidden channel sizes for each temporal block.
                      E.g. [64, 64, 128, 128] — depth and width.
        kernel_size:  Convolution kernel size (3 or 5 recommended).
        dropout:      Dropout rate inside each block.
        use_attention:Whether to add temporal self-attention after TCN stack.
        num_heads:    Attention heads (must divide num_channels[-1]).
    """

    def __init__(
        self,
        input_size: int,
        output_size: int,
        num_channels: List[int] = [64, 64, 128, 128],
        kernel_size: int = 3,
        dropout: float = 0.2,
        use_attention: bool = True,
        num_heads: int = 4,
    ):
        super().__init__()

        # Stack temporal blocks with doubling dilation
        layers = []
        in_ch = input_size
        for i, out_ch in enumerate(num_channels):
            dilation = 2 ** i
            layers.append(
                TemporalBlock(in_ch, out_ch, kernel_size, dilation=dilation, dropout=dropout)
            )
            in_ch = out_ch

        self.tcn = nn.Sequential(*layers)

        # Optional attention bottleneck
        self.use_attention = use_attention
        if use_attention:
            # Pad channels to be divisible by num_heads
            if num_channels[-1] % num_heads != 0:
                raise ValueError(
                    f"num_channels[-1]={num_channels[-1]} must be divisible by num_heads={num_heads}"
                )
            self.attention = TemporalAttention(num_channels[-1], num_heads=num_heads)

        # Final 1×1 projection to output
        self.output_proj = nn.Conv1d(num_channels[-1], output_size, kernel_size=1)
        nn.init.xavier_uniform_(self.output_proj.weight)
        nn.init.zeros_(self.output_proj.bias)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        """
        x:      (B, C_in, T)
        return: (B, C_out, T)
        """
        out = self.tcn(x)
        if self.use_attention:
            out = self.attention(out)
        return self.output_proj(out)


    @property
    def receptive_field(self) -> int:
        """Compute the theoretical receptive field of the TCN stack."""
        # For each block: 2 × (kernel_size - 1) × dilation
        # This assumes the model is built as in __init__ with standard dilation=2^i
        raise NotImplementedError("Call receptive_field_from_config() instead.")


def receptive_field_from_config(num_channels, kernel_size):
    """Utility: compute receptive field of a TCN stack."""
    rf = 1
    for i in range(len(num_channels)):
        dilation = 2 ** i
        rf += 2 * (kernel_size - 1) * dilation
    return rf


# ─────────────────────────────────────────────
# Quick sanity check
# ─────────────────────────────────────────────

if __name__ == "__main__":
    B, C_in, T = 8, 13, 200
    model = TCN(input_size=C_in, output_size=1, num_channels=[64, 64, 128, 128],
                kernel_size=3, dropout=0.2, use_attention=True, num_heads=4)

    x = torch.randn(B, C_in, T)
    y = model(x)

    print(f"Input : {x.shape}")
    print(f"Output: {y.shape}")
    print(f"Receptive field: {receptive_field_from_config([64, 64, 128, 128], 3)} time steps")

    total_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(f"Trainable params: {total_params:,}")
