"""
tcn_deploy_jit.py  —  100 Hz robot inference using only the .jit file

No model source code. No scaler. No demographics CSV.
Ship these two files to the robot:
    best_tcn.jit
    jit_metadata.yaml

Dependencies (robot side):
    pip install torch pyyaml numpy
"""

import collections
import time
from typing import Optional

import numpy as np
import torch
import yaml

# ---------------------------------------------------------------------------
# Paths  —  edit to match robot filesystem
# ---------------------------------------------------------------------------
JIT_PATH  = "/Users/narayanan/PycharmProjects/MRSD_exoskeleton/core/scripts/runs/TCN_learnable_binary_fp/best_tcn.jit"
META_PATH = "/Users/narayanan/PycharmProjects/MRSD_exoskeleton/core/scripts/runs/TCN_learnable_binary_fp/jit_metadata.yaml"
DEVICE    = "mps"   # or "cpu"
ASSISTANCE_SCALE = 0.1


# ---------------------------------------------------------------------------
# Observation buffer  (unchanged from before)
# ---------------------------------------------------------------------------
class ObservationBuffer:
    def __init__(self, window_length: int, num_channels: int):
        self.window_length = window_length
        self.num_channels  = num_channels
        self._buf: collections.deque = collections.deque(maxlen=window_length)

    def push(self, obs: np.ndarray) -> None:
        if obs.shape[0] != self.num_channels:
            raise ValueError(
                f"Expected {self.num_channels} channels, got {obs.shape[0]}"
            )
        self._buf.append(obs.astype(np.float32))

    @property
    def ready(self) -> bool:
        return len(self._buf) == self.window_length

    def get_window(self) -> np.ndarray:
        """Returns (C, T) — model input layout."""
        if not self.ready:
            raise RuntimeError("Buffer not full yet.")
        return np.stack(self._buf, axis=0).T.copy()   # (C, T)

    def reset(self) -> None:
        self._buf.clear()

class JITDeployer:
    """
    Minimal inference wrapper around a .jit model.

    forward contract (baked into the JIT):
        input  : (1, C, T) float32  — one window
        output : (output_size,)     — torque in Nm/kg, last timestep only
    """

    def __init__(self, jit_path: str, meta_path: str, device: str = "cuda"):
        self.device = torch.device(
            device if torch.cuda.is_available() else "cpu"
        )
        print(f"[JITDeployer] device: {self.device}")

        # ---- load metadata -------------------------------------------
        with open(meta_path) as f:
            meta = yaml.safe_load(f)

        self.window_length = meta["window_length"]
        self.num_input_ch  = meta["num_input_ch"]
        self.output_size   = meta["output_size"]
        self.subject_mass  = meta["subject_mass_kg"]
        print(
            f"[JITDeployer] subject={meta['subject_id']}  "
            f"mass={self.subject_mass:.1f} kg  "
            f"window={self.window_length}  "
            f"input_ch={self.num_input_ch}  "
            f"output_size={self.output_size}"
        )

        self.model = torch.jit.load(jit_path, map_location=self.device)
        self.model.eval()
        print(f"[JITDeployer] Loaded {jit_path}")

        with torch.inference_mode():
            dummy = torch.zeros(
                1, self.num_input_ch, self.window_length, device=self.device
            )
            _ = self.model(dummy)
        print("[JITDeployer] Warm-up done. Ready.")

        self._buf = ObservationBuffer(self.window_length, self.num_input_ch)


    def push(self, obs: np.ndarray) -> None:
        """Feed one (C,) observation frame."""
        self._buf.push(obs)

    @property
    def ready(self) -> bool:
        return self._buf.ready

    @torch.inference_mode()
    def predict(self) -> np.ndarray:
        """
        Returns torque at the last timestep, shape (output_size,), units Nm/kg.
        Raises RuntimeError if the buffer isn't full yet.
        """
        window = self._buf.get_window()                              # (C, T)
        x = torch.from_numpy(window).unsqueeze(0).to(self.device)   # (1, C, T)
        out = self.model(x)                                          # (output_size,)
        return out.cpu().numpy()

    def reset(self) -> None:
        """Clear buffer between gait bouts or trials."""
        self._buf.reset()


# ---------------------------------------------------------------------------
# 100 Hz control loop example
# ---------------------------------------------------------------------------
def main():
    deployer = JITDeployer(JIT_PATH, META_PATH, device=DEVICE)
    num_ch  = deployer.num_input_ch
    RATE_HZ = 100
    dt      = 1.0 / RATE_HZ

    print(f"\nRunning {RATE_HZ} Hz loop. "
          f"Collecting {deployer.window_length} frames before first prediction…\n")

    step = 0
    while True:
        t0 = time.perf_counter()

        # ---- YOUR SENSOR READ GOES HERE --------------------------------
        obs = np.random.randn(num_ch).astype(np.float32)   # ← replace with real data
        # ----------------------------------------------------------------

        deployer.push(obs)

        if deployer.ready:
            torque_nm_per_kg = deployer.predict()   # (output_size,)

            # ---- YOUR ACTUATOR COMMAND GOES HERE -------------------
            # e.g.  robot.set_torque(torque_nm_per_kg * deployer.subject_mass * ASSISTANCE_SCALE)
            # --------------------------------------------------------

            if step % 100 == 0:
                print(f"[step {step:6d}]  torque (Nm/kg): {torque_nm_per_kg}")

        step += 1
        elapsed = time.perf_counter() - t0
        sleep_t = dt - elapsed
        if sleep_t > 0:
            time.sleep(sleep_t)
        elif step % 100 == 0:
            print(f"[WARNING] Loop overrun by {-sleep_t * 1000:.1f} ms at step {step}")


if __name__ == "__main__":
    main()