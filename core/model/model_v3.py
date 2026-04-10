import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.nn.utils import weight_norm
from typing import List, Optional

STAGE1_LR_MIN  = 1e-6
STAGE1_LR_MAX  = 1e-3
STAGE1_L1_MIN  = 1e-5
STAGE1_L1_MAX  = 1e-3
STAGE1_L2_MIN  = 1e-5
STAGE1_L2_MAX  = 1e-3


def _get_activation(name: str) -> nn.Module:
    mapping = {"ELU": nn.ELU, "GELU": nn.GELU, "ReLU": nn.ReLU, "Swish": nn.SiLU}
    if name not in mapping:
        raise ValueError(f"Activation '{name}' not in Stage-1 search space: {list(mapping)}")
    return mapping[name]()


class SpatialDropout1d(nn.Module):
    def __init__(self, p: float = 0.15):
        super().__init__()
        self.p = p

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        if not self.training or self.p == 0.0:
            return x
        mask = torch.ones(x.size(0), x.size(1), 1, device=x.device, dtype=x.dtype)
        mask = F.dropout(mask, p=self.p, training=True)
        return x * mask


def _make_dropout(dropout_type: str, p: float) -> nn.Module:
    if dropout_type == "Spatial":
        return SpatialDropout1d(p)
    elif dropout_type == "ElementWise":
        return nn.Dropout(p)
    raise ValueError(f"dropout_type must be 'Spatial' or 'ElementWise', got '{dropout_type}'")


def _make_norm(norm_type: str, num_channels: int) -> nn.Module:
    if norm_type == "BatchNorm":
        return nn.BatchNorm1d(num_channels)
    elif norm_type == "LayerNorm":
        return nn.GroupNorm(1, num_channels)
    elif norm_type == "WeightNorm":
        return nn.Identity()
    raise ValueError(f"norm_type must be 'BatchNorm', 'LayerNorm', or 'WeightNorm', got '{norm_type}'")


class Chomp1d(nn.Module):
    def __init__(self, chomp_size: int):
        super().__init__()
        self.chomp_size = chomp_size

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return x[:, :, :-self.chomp_size].contiguous() if self.chomp_size > 0 else x


class TemporalBlock(nn.Module):
    def __init__(self, n_inputs, n_outputs, kernel_size, stride, dilation, padding,
                 dropout=0.15, activation="ReLU", norm_type="WeightNorm", dropout_type="Spatial"):
        super().__init__()
        use_weight_norm = (norm_type == "WeightNorm")

        def _conv(in_c, out_c):
            c = nn.Conv1d(in_c, out_c, kernel_size, stride=stride, padding=padding, dilation=dilation)
            return weight_norm(c) if use_weight_norm else c

        self.conv1  = _conv(n_inputs,  n_outputs)
        self.chomp1 = Chomp1d(padding)
        self.norm1  = _make_norm(norm_type, n_outputs)
        self.act1   = _get_activation(activation)
        self.drop1  = _make_dropout(dropout_type, dropout)

        self.conv2  = _conv(n_outputs, n_outputs)
        self.chomp2 = Chomp1d(padding)
        self.norm2  = _make_norm(norm_type, n_outputs)
        self.act2   = _get_activation(activation)
        self.drop2  = _make_dropout(dropout_type, dropout)

        self.downsample = nn.Conv1d(n_inputs, n_outputs, 1) if n_inputs != n_outputs else None
        self.act_res    = _get_activation(activation)
        self._init_weights(use_weight_norm)

    def _init_weights(self, use_weight_norm: bool):
        for conv in [self.conv1, self.conv2]:
            w = conv.weight_v if use_weight_norm else conv.weight
            nn.init.normal_(w, 0, 0.01)
        if self.downsample is not None:
            nn.init.normal_(self.downsample.weight, 0, 0.01)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        out = self.drop1(self.act1(self.norm1(self.chomp1(self.conv1(x)))))
        out = self.drop2(self.act2(self.norm2(self.chomp2(self.conv2(out)))))
        res = x if self.downsample is None else self.downsample(x)
        return self.act_res(out + res)


class TemporalConvNet(nn.Module):
    def __init__(self, num_inputs, num_channels, kernel_size=5, dropout=0.15,
                 activation="ReLU", norm_type="WeightNorm", dropout_type="Spatial"):
        super().__init__()
        layers = []
        for i, out_ch in enumerate(num_channels):
            dilation = 2 ** i
            in_ch    = num_inputs if i == 0 else num_channels[i - 1]
            layers.append(TemporalBlock(
                n_inputs=in_ch, n_outputs=out_ch, kernel_size=kernel_size,
                stride=1, dilation=dilation, padding=(kernel_size - 1) * dilation,
                dropout=dropout, activation=activation,
                norm_type=norm_type, dropout_type=dropout_type,
            ))
        self.network = nn.Sequential(*layers)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.network(x)


class TCN(nn.Module):
    """
    TCN with optional learnable subject embedding.

    When use_subject_embedding=True:
      - nn.Embedding of shape (num_subjects, emb_dim) is learned during training
      - At forward(), pass subject_idx (batch,) to retrieve and concatenate
        the embedding as constant channels: input becomes (C + emb_dim, T)
      - num_subjects must include an extra slot (index = num_subjects-1)
        used as the fallback embedding for unseen subjects at inference
    """

    def __init__(
        self,
        input_size:            int,
        output_size:           int,
        num_channels:          List[int] = None,
        kernel_size:           int       = 5,
        activation:            str       = "ReLU",
        norm_type:             str       = "WeightNorm",
        dropout_type:          str       = "Spatial",
        dropout:               float     = 0.15,
        l1_reg:                float     = 0.0,
        l2_reg:                float     = 0.0,
        use_subject_embedding: bool      = False,
        num_subjects:          int       = 0,
        emb_dim:               int       = 4,
    ):
        super().__init__()

        if num_channels is None:
            num_channels = [80] * 5

        self.l1_reg               = l1_reg
        self.l2_reg               = l2_reg
        self.use_subject_embedding = use_subject_embedding
        self.emb_dim              = emb_dim

        if use_subject_embedding:
            self.subject_embedding = nn.Embedding(num_subjects, emb_dim)
            nn.init.normal_(self.subject_embedding.weight, 0, 0.01)
            tcn_input_size = input_size + emb_dim
        else:
            tcn_input_size = input_size

        self.tcn    = TemporalConvNet(tcn_input_size, num_channels, kernel_size=kernel_size,
                                      dropout=dropout, activation=activation,
                                      norm_type=norm_type, dropout_type=dropout_type)
        self.linear = nn.Conv1d(num_channels[-1], output_size, kernel_size=1)

    def forward(self, x: torch.Tensor, subject_idx: Optional[torch.Tensor] = None) -> torch.Tensor:
        """
        x           : (batch, C, T)
        subject_idx : (batch,) int tensor — required when use_subject_embedding=True
        """
        if self.use_subject_embedding:
            assert subject_idx is not None, "subject_idx required when use_subject_embedding=True"
            emb = self.subject_embedding(subject_idx)          # (batch, emb_dim)
            emb = emb.unsqueeze(-1).expand(-1, -1, x.size(2)) # (batch, emb_dim, T)
            x   = torch.cat([x, emb], dim=1)                  # (batch, C+emb_dim, T)
        return self.linear(self.tcn(x))

    def regularization_loss(self) -> torch.Tensor:
        l1 = torch.tensor(0.0)
        l2 = torch.tensor(0.0)
        for name, param in self.named_parameters():
            if "weight" in name:
                if self.l1_reg > 0:
                    l1 = l1 + param.abs().sum()
                if self.l2_reg > 0:
                    l2 = l2 + param.pow(2).sum()
        return self.l1_reg * l1 + self.l2_reg * l2