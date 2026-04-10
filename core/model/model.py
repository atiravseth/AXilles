import torch.nn as nn
from torch.nn.utils import weight_norm
from typing import List

class Chomp1d(nn.Module):
    """Removes extra padding to maintain causal convolution."""
    def __init__(self, chomp_size):
        super().__init__()
        self.chomp_size = chomp_size

    def forward(self, x):
        return x[:, :, :-self.chomp_size].contiguous() if self.chomp_size > 0 else x


class TemporalBlock(nn.Module):
    """Single temporal block: Conv1d -> Chomp -> Activation -> Dropout -> Conv1d -> Chomp -> Activation -> Dropout + Residual"""
    def __init__(self, n_inputs, n_outputs, kernel_size, stride, dilation, padding, dropout=0.2, activation='ReLU'):
        super().__init__()
        self.conv1 = weight_norm(nn.Conv1d(n_inputs, n_outputs, kernel_size,
                                           stride=stride, padding=padding, dilation=dilation))
        self.chomp1 = Chomp1d(padding)
        self.act1 = getattr(nn, activation)()
        self.drop1 = nn.Dropout(dropout)

        self.conv2 = weight_norm(nn.Conv1d(n_outputs, n_outputs, kernel_size,
                                           stride=stride, padding=padding, dilation=dilation))
        self.chomp2 = Chomp1d(padding)
        self.act2 = getattr(nn, activation)()
        self.drop2 = nn.Dropout(dropout)

        # Residual connection
        self.downsample = nn.Conv1d(n_inputs, n_outputs, 1) if n_inputs != n_outputs else None
        self.act = getattr(nn, activation)()
        self.init_weights()

    def init_weights(self):
        nn.init.normal_(self.conv1.weight, 0, 0.01)
        nn.init.normal_(self.conv2.weight, 0, 0.01)
        if self.downsample is not None:
            nn.init.normal_(self.downsample.weight, 0, 0.01)

    def forward(self, x):
        out = self.conv1(x)
        out = self.chomp1(out)
        out = self.act1(out)
        out = self.drop1(out)

        out = self.conv2(out)
        out = self.chomp2(out)
        out = self.act2(out)
        out = self.drop2(out)

        res = x if self.downsample is None else self.downsample(x)
        return self.act(out + res)


class TemporalConvNet(nn.Module):
    """Stack multiple TemporalBlocks with exponentially increasing dilation."""
    def __init__(self, num_inputs, num_channels: List[int], kernel_size=3, dropout=0.2):
        super().__init__()
        layers = []
        num_levels = len(num_channels)
        for i in range(num_levels):
            dilation_size = 2 ** i
            in_channels = num_inputs if i == 0 else num_channels[i-1]
            out_channels = num_channels[i]
            layers.append(TemporalBlock(in_channels, out_channels, kernel_size,
                                        stride=1, dilation=dilation_size,
                                        padding=(kernel_size-1) * dilation_size,
                                        dropout=dropout))
        self.network = nn.Sequential(*layers)

    def forward(self, x):
        return self.network(x)


class TCN(nn.Module):
    """
    Temporal Convolutional Network for exoskeleton sliding-window data.

    Input:  (batch, features, timesteps)
    Output: (batch, output_features, timesteps)
    """
    def __init__(self,
                 input_size: int,
                 output_size: int,
                 num_channels: List[int] = [64, 64, 64, 64],
                 kernel_size: int = 3,
                 dropout: float = 0.2):
        super().__init__()
        self.tcn = TemporalConvNet(input_size, num_channels, kernel_size=kernel_size, dropout=dropout)
        self.linear = nn.Conv1d(num_channels[-1], output_size, kernel_size=1)

    def forward(self, x):
        """
        x: (batch, features, timesteps)
        returns: (batch, output_features, timesteps)
        """
        out = self.tcn(x)          # (batch, channels, timesteps)
        out = self.linear(out)     # (batch, output_features, timesteps)
        return out
