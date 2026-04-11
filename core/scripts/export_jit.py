import os
import yaml
import torch
import joblib
import numpy as np
import pandas as pd
from core.model.model_v3 import TCN

CONFIG_PATH      = "/Users/narayanan/PycharmProjects/MRSD_exoskeleton/cfg/cfg.yaml"
DEMOGRAPHICS_CSV = "/Users/narayanan/PycharmProjects/exo-data/csv_output/subject_info.csv"
MODEL_PATH       = "/Users/narayanan/PycharmProjects/MRSD_exoskeleton/core/scripts/runs/TCN_learnable_binary_fp/best_tcn.pt"
OUTPUT_JIT       = "/Users/narayanan/PycharmProjects/MRSD_exoskeleton/core/scripts/runs/TCN_learnable_binary_fp/best_tcn.jit"
OUTPUT_META      = "/Users/narayanan/PycharmProjects/MRSD_exoskeleton/core/scripts/runs/TCN_learnable_binary_fp/jit_metadata.yaml"

# Subject info collected at deployment time (via terminal prompts)
DEPLOY_SUBJECT   = "new_subject"   # internal label — doesn't need to match CSV

# Model architecture (must match training)
NUM_CHANNELS = [80] * 5
KERNEL_SIZE  = 5
ACTIVATION   = "ReLU"
NORM_TYPE    = "WeightNorm"
DROPOUT_TYPE = "Spatial"
DROPOUT      = 0.15
EMB_DIM      = 4

DEVICE = torch.device("cuda" if torch.cuda.is_available() else "cpu")

# ---------------------------------------------------------------------------
# Demographics helpers
# ---------------------------------------------------------------------------
_HEIGHT_MEAN, _HEIGHT_STD = 1.7055, 0.0724
_WEIGHT_MEAN, _WEIGHT_STD = 68.5018, 11.0719


def load_demo_vecs(csv_path):
    df = pd.read_csv(csv_path)
    df["Subject"] = df["Subject"].str.strip()
    vecs = {}
    for _, row in df.iterrows():
        gm = 1.0 if row["Gender"].strip() == "M" else 0.0
        gf = 1.0 if row["Gender"].strip() == "F" else 0.0
        h  = (row["Height"] - _HEIGHT_MEAN) / _HEIGHT_STD
        w  = (row["Weight"] - _WEIGHT_MEAN) / _WEIGHT_STD
        vecs[row["Subject"]] = np.array([h, w, gm, gf], dtype=np.float32)
    return vecs


def cosine_sim(a, b):
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-8))


def find_closest(target_vec, candidate_vecs):
    best_id, best_sim = None, -2.0
    for sid, vec in candidate_vecs.items():
        s = cosine_sim(target_vec, vec)
        if s > best_sim:
            best_sim, best_id = s, sid
    print(f"  New subject → matched to training subject '{best_id}' (cosine={best_sim:.4f})")
    return best_id


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main():
    # ---- load config -----------------------------------------------------
    with open(CONFIG_PATH) as f:
        cfg = yaml.safe_load(f)

    data_root       = cfg["paths"]["data_root_dir"]
    window_length   = cfg["model"].get("min_segment_length", 300)
    use_subject_emb = cfg["model"].get("use_subject_embedding", False)
    train_subjects  = cfg["model"]["subject_split"]["train_subjects"]

    # ---- infer input / output sizes from a sample trial -----------------
    sample_feat = next(
        f for f in os.listdir(data_root) if f.endswith("_features.csv")
    )
    feat_df = pd.read_csv(os.path.join(data_root, sample_feat))
    fs_cfg  = cfg.get("feature_sets", {})
    active  = fs_cfg.get("active")
    if active and active in fs_cfg:
        feat_df = feat_df[list(fs_cfg[active])]
    num_input_ch = feat_df.shape[1]

    sample_targ = sample_feat.replace("_features.csv", "_targets.csv")
    output_size = pd.read_csv(os.path.join(data_root, sample_targ)).shape[1]

    print(f"input_ch={num_input_ch}, output_size={output_size}, window={window_length}")

    # ---- collect subject info interactively -----------------------------
    print("\n--- New subject info ---")
    height_m  = float(input("  Height (m)   : "))
    weight_kg = float(input("  Weight (kg)  : "))
    gender    = input("  Gender (M/F) : ").strip().upper()

    subject_mass  = weight_kg
    gender_male   = 1.0 if gender == "M" else 0.0
    gender_female = 1.0 if gender == "F" else 0.0
    h_norm = (height_m - _HEIGHT_MEAN) / _HEIGHT_STD
    w_norm = (weight_kg - _WEIGHT_MEAN) / _WEIGHT_STD
    new_subject_vec = np.array([h_norm, w_norm, gender_male, gender_female], dtype=np.float32)
    print(f"  → mass={weight_kg:.1f} kg, demo_vec={new_subject_vec}\n")

    # ---- resolve subject embedding index --------------------------------
    demo_vecs   = load_demo_vecs(DEMOGRAPHICS_CSV)
    subject_idx = None
    if use_subject_emb:
        subject_to_idx = {sid: idx for idx, sid in enumerate(sorted(train_subjects))}
        train_vecs     = {s: demo_vecs[s] for s in train_subjects if s in demo_vecs}
        closest        = find_closest(new_subject_vec, train_vecs)
        subject_idx    = subject_to_idx[closest]

    # ---- scaler ----------------------------------------------------------
    scaler_y = joblib.load(os.path.join(data_root, "scaler_y.pkl"))
    # Bake scaler params into tensors so we can embed them in the JIT model
    scaler_mean  = torch.tensor(scaler_y.mean_,  dtype=torch.float32)
    scaler_scale = torch.tensor(scaler_y.scale_, dtype=torch.float32)

    # ---- build & load model ---------------------------------------------
    model = TCN(
        input_size            = num_input_ch,
        output_size           = output_size,
        num_channels          = NUM_CHANNELS,
        kernel_size           = KERNEL_SIZE,
        activation            = ACTIVATION,
        norm_type             = NORM_TYPE,
        dropout_type          = DROPOUT_TYPE,
        dropout               = DROPOUT,
        use_subject_embedding = use_subject_emb,
        num_subjects          = len(train_subjects),
        emb_dim               = EMB_DIM,
    ).to(DEVICE)

    model.load_state_dict(torch.load(MODEL_PATH, map_location=DEVICE))
    model.eval()

    # ---- wrap into a self-contained scriptable module -------------------
    class DeployWrapper(torch.nn.Module):
        """
        Wraps TCN + inverse-scaler + mass-normalisation into a single
        TorchScript-compatible module.

        forward(x) -> torque in Nm/kg  (last timestep only)
          x : (1, C, T)  float32
        """
        def __init__(self, tcn, scaler_mean, scaler_scale,
                     subject_mass, subject_idx, use_subject_emb):
            super().__init__()
            self.tcn              = tcn
            self.register_buffer("scaler_mean",  scaler_mean)
            self.register_buffer("scaler_scale", scaler_scale)
            self.subject_mass     = subject_mass
            self.subject_idx      = subject_idx          # int or -1
            self.use_subject_emb  = use_subject_emb

        def forward(self, x: torch.Tensor) -> torch.Tensor:
            # x : (1, C, T)
            sidx: torch.Optional[torch.Tensor] = None
            if self.use_subject_emb:
                sidx = torch.tensor(
                    [self.subject_idx], dtype=torch.long, device=x.device
                )
            out       = self.tcn(x, sidx)           # (1, output_size, T)
            last      = out[0, :, -1]               # (output_size,)
            denormed  = last * self.scaler_scale + self.scaler_mean  # inverse z-score
            return denormed / self.subject_mass      # Nm/kg

    wrapper = DeployWrapper(
        tcn             = model,
        scaler_mean     = scaler_mean,
        scaler_scale    = scaler_scale,
        subject_mass    = subject_mass,
        subject_idx     = subject_idx if subject_idx is not None else -1,
        use_subject_emb = use_subject_emb,
    ).to(DEVICE)

    # ---- trace / script -------------------------------------------------
    # Use torch.jit.trace with a representative dummy input
    dummy_x = torch.zeros(1, num_input_ch, window_length, device=DEVICE)

    print("Tracing model…")
    with torch.no_grad():
        traced = torch.jit.trace(wrapper, dummy_x)

    # Verify traced output matches eager output
    with torch.no_grad():
        eager_out  = wrapper(dummy_x)
        traced_out = traced(dummy_x)
    max_err = (eager_out - traced_out).abs().max().item()
    print(f"Trace verification max error: {max_err:.2e}  "
          f"({'OK' if max_err < 1e-5 else 'WARNING — large error!'})")

    # ---- save -----------------------------------------------------------
    traced.save(OUTPUT_JIT)
    print(f"Saved JIT model → {OUTPUT_JIT}")

    # ---- save metadata (for the deployment loader) ----------------------
    meta = {
        "window_length":    window_length,
        "num_input_ch":     num_input_ch,
        "output_size":      output_size,
        "subject_id":       DEPLOY_SUBJECT,
        "subject_mass_kg":  subject_mass,
        "subject_idx":      subject_idx,
        "use_subject_emb":  use_subject_emb,
        "output_units":     "Nm/kg",
    }
    with open(OUTPUT_META, "w") as f:
        yaml.dump(meta, f)
    print(f"Saved metadata   → {OUTPUT_META}")
    print("\nDone. Ship best_tcn.jit + jit_metadata.yaml to the robot.")


if __name__ == "__main__":
    main()