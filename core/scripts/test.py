import os
import yaml
import torch
import numpy as np
import joblib
import pandas as pd
import matplotlib.pyplot as plt
from torch.utils.data import DataLoader

from core.model.model_v3 import TCN
from core.datagen.exo_dataloader import ExoDatagen
from utils import (
    load_subject_mass,
    subject_from_trial,
    unnorm_and_mass_normalize,
)

# -----------------------------------------------------------------------
# Config
# -----------------------------------------------------------------------
CONFIG_PATH      = "/Users/narayanan/PycharmProjects/MRSD_exoskeleton/cfg/cfg.yaml"
DEMOGRAPHICS_CSV = "/Users/narayanan/PycharmProjects/MRSD_exoskeleton/csv_output/subject_info.csv"
MODEL_PATH       = "/Users/narayanan/PycharmProjects/MRSD_exoskeleton/core/scripts/runs/TCN_learnable_binary_fp/best_tcn.pt"
OUTPUT_DIR       = "/Users/narayanan/PycharmProjects/MRSD_exoskeleton/core/scripts/runs/TCN_learnable_binary_fp/test_eval"

DEVICE       = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
BATCH_SIZE   = 32
NUM_WORKERS  = 0
EMB_DIM      = 4
NUM_CHANNELS = [80] * 5
KERNEL_SIZE  = 5
ACTIVATION   = "ReLU"
NORM_TYPE    = "WeightNorm"
DROPOUT_TYPE = "Spatial"
DROPOUT      = 0.15

os.makedirs(OUTPUT_DIR, exist_ok=True)


# -----------------------------------------------------------------------
# Cosine similarity helper (same as train.py)
# -----------------------------------------------------------------------
def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-8))


def find_closest_training_subject(test_id, train_ids, dataset):
    test_vec = dataset.get_demo_vec(test_id)
    best_id, best_sim = None, -1.0
    for sid in train_ids:
        sim = cosine_similarity(test_vec, dataset.get_demo_vec(sid))
        if sim > best_sim:
            best_sim, best_id = sim, sid
    print(f"  {test_id} → closest training subject: {best_id} (cosine={best_sim:.4f})")
    return best_id


def build_subject_index(train_subjects):
    return {sid: idx for idx, sid in enumerate(sorted(set(train_subjects)))}


def get_subject_idx_tensor(subject_ids, subject_to_idx, device):
    return torch.tensor(
        [subject_to_idx[sid] for sid in subject_ids],
        dtype=torch.long, device=device
    )


# -----------------------------------------------------------------------
# Reconstruct full signal from overlapping windows
# -----------------------------------------------------------------------
def reconstruct_full_signal(model, dataset, subject_to_idx, use_subject_embedding,
                             scaler_y, subject_mass, device):
    """
    For each trial in the dataset, stitch together all windows into a
    continuous prediction by averaging overlapping predictions.
    Returns per-trial dict with pred and gt arrays in Nm/kg.
    """
    # Group segments by trial
    from collections import defaultdict
    trial_segments = defaultdict(list)
    for seg_idx, (trial_name, start, end) in enumerate(dataset.valid_segments):
        trial_segments[trial_name].append((seg_idx, start, end))

    results = {}

    model.eval()
    with torch.no_grad():
        for trial_name, segs in trial_segments.items():
            feat_arr, targ_arr = dataset._cache[trial_name]
            T_total = len(targ_arr)
            C_out   = targ_arr.shape[1]

            # Accumulators for averaging overlapping windows
            pred_accum  = np.zeros((T_total, C_out), dtype=np.float32)
            count_accum = np.zeros((T_total, 1),     dtype=np.float32)

            # Process in batches
            CHUNK = 64
            for chunk_start in range(0, len(segs), CHUNK):
                chunk = segs[chunk_start : chunk_start + CHUNK]
                xs, starts_list, ends_list, sids = [], [], [], []

                for seg_idx, start, end in chunk:
                    x, _, _, sid = dataset[seg_idx]
                    xs.append(x)
                    starts_list.append(start)
                    ends_list.append(end)
                    sids.append(sid)

                x_batch = torch.stack(xs).to(device)  # (B, C, T)
                subj_idx = get_subject_idx_tensor(sids, subject_to_idx, device) \
                           if use_subject_embedding else None

                out_batch = model(x_batch, subj_idx).cpu().numpy()  # (B, C_out, T_win)

                for i, (start, end) in enumerate(zip(starts_list, ends_list)):
                    out_nm = out_batch[i].T  # (T_win, C_out) — still z-scored
                    pred_accum[start:end+1]  += out_nm
                    count_accum[start:end+1] += 1.0

            # Average overlapping predictions
            count_accum = np.where(count_accum == 0, 1, count_accum)
            pred_z = pred_accum / count_accum  # (T_total, C_out) z-scored

            # Inverse transform to Nm, then mass-normalize
            mass      = subject_mass[subject_from_trial(trial_name)]
            pred_nm   = scaler_y.inverse_transform(pred_z) / mass
            targ_nm   = scaler_y.inverse_transform(targ_arr.astype(np.float32)) / mass

            # Mask NaN rows (between gait bouts)
            valid_mask = ~np.isnan(targ_nm).any(axis=1)

            results[trial_name] = {
                "pred":       pred_nm,
                "gt":         targ_nm,
                "valid_mask": valid_mask,
                "mass":       mass,
            }

    return results


# -----------------------------------------------------------------------
# Metrics
# -----------------------------------------------------------------------
def compute_metrics(pred, gt, valid_mask):
    p = pred[valid_mask]
    g = gt[valid_mask]
    rmse      = float(np.sqrt(np.mean((p - g) ** 2)))
    mae       = float(np.mean(np.abs(p - g)))
    peak2peak = float(g.max() - g.min())
    norm_mae  = mae / peak2peak if peak2peak > 1e-6 else float("nan")
    r2        = float(1 - np.sum((p - g)**2) / (np.sum((g - g.mean())**2) + 1e-8))
    return {"rmse": rmse, "mae": mae, "norm_mae": norm_mae, "r2": r2, "peak2peak": peak2peak}


# -----------------------------------------------------------------------
# Plotting
# -----------------------------------------------------------------------
def plot_trial(trial_name, pred, gt, valid_mask, metrics, out_dir):
    t = np.arange(len(gt))
    fig, ax = plt.subplots(figsize=(16, 4))
    ax.plot(t, gt[:, 0],   color="blue",  lw=1.2, label="Ground Truth (Nm/kg)", alpha=0.85)
    ax.plot(t, pred[:, 0], color="red",   lw=1.0, label="Prediction (Nm/kg)",   alpha=0.85, linestyle="--")

    # Shade invalid (NaN) regions
    invalid = ~valid_mask
    if invalid.any():
        ax.fill_between(t, gt[:, 0].min() - 0.1, gt[:, 0].max() + 0.1,
                        where=invalid, alpha=0.15, color="gray", label="NaN region")

    ax.set_title(f"{trial_name} | RMSE={metrics['rmse']:.4f}  MAE={metrics['mae']:.4f}  "
                 f"R²={metrics['r2']:.3f}  NormMAE={metrics['norm_mae']:.3f} Nm/kg")
    ax.set_xlabel("Time Step")
    ax.set_ylabel("Torque (Nm/kg)")
    ax.legend(loc="upper right")
    ax.grid(True, linestyle="--", alpha=0.5)
    plt.tight_layout()
    fname = os.path.join(out_dir, f"{trial_name.replace('.csv','')}_full.png")
    plt.savefig(fname, dpi=150)
    plt.close()
    print(f"  Saved: {fname}")


# -----------------------------------------------------------------------
# Main
# -----------------------------------------------------------------------
def main():
    with open(CONFIG_PATH, "r") as f:
        cfg = yaml.safe_load(f)

    use_subject_embedding = cfg["model"].get("use_subject_embedding", False)
    subject_mass          = load_subject_mass(DEMOGRAPHICS_CSV)
    scaler_y              = joblib.load(os.path.join(cfg["paths"]["data_root_dir"], "scaler_y.pkl"))

    train_subjects = cfg["model"]["subject_split"]["train_subjects"]
    val_subjects   = cfg["model"]["subject_split"]["val_subjects"]
    test_subjects  = cfg["model"]["subject_split"]["test_subjects"]

    # Build datasets
    train_dataset = ExoDatagen(cfg, split="train", verbose=False)
    test_dataset  = ExoDatagen(cfg, split="test",  verbose=True)

    # Subject index map
    subject_to_idx = build_subject_index(train_subjects)
    if use_subject_embedding:
        for sid in val_subjects + test_subjects:
            closest = find_closest_training_subject(sid, train_subjects, train_dataset)
            subject_to_idx[sid] = subject_to_idx[closest]

    # Build and load model
    input_size  = train_dataset[0][0].shape[0]
    output_size = train_dataset[0][1].shape[0]

    model = TCN(
        input_size            = input_size,
        output_size           = output_size,
        num_channels          = NUM_CHANNELS,
        kernel_size           = KERNEL_SIZE,
        activation            = ACTIVATION,
        norm_type             = NORM_TYPE,
        dropout_type          = DROPOUT_TYPE,
        dropout               = DROPOUT,
        use_subject_embedding = use_subject_embedding,
        num_subjects          = len(train_subjects),
        emb_dim               = EMB_DIM,
    ).to(DEVICE)

    model.load_state_dict(torch.load(MODEL_PATH, map_location=DEVICE))
    model.eval()
    print(f"\nLoaded model from {MODEL_PATH}")

    # Reconstruct full signals
    print("\nReconstructing full test signals...")
    results = reconstruct_full_signal(
        model, test_dataset, subject_to_idx,
        use_subject_embedding, scaler_y, subject_mass, DEVICE
    )

    # Compute and print metrics per trial, then aggregate
    all_metrics = []
    print(f"\n{'Trial':<45} {'RMSE':>8} {'MAE':>8} {'NormMAE':>9} {'R²':>7} {'P2P':>8}")
    print("-" * 90)

    for trial_name, data in sorted(results.items()):
        m = compute_metrics(data["pred"], data["gt"], data["valid_mask"])
        all_metrics.append(m)
        print(f"{trial_name:<45} {m['rmse']:>8.4f} {m['mae']:>8.4f} "
              f"{m['norm_mae']:>9.4f} {m['r2']:>7.3f} {m['peak2peak']:>8.4f}")
        plot_trial(trial_name, data["pred"], data["gt"], data["valid_mask"], m,
                   OUTPUT_DIR)

    # Subject-level RMSE — concatenate all trials, compute once
    all_pred = np.concatenate([d["pred"][d["valid_mask"]] for d in results.values()])
    all_gt   = np.concatenate([d["gt"][d["valid_mask"]]   for d in results.values()])

    subject_rmse     = float(np.sqrt(np.mean((all_pred - all_gt) ** 2)))
    subject_mae      = float(np.mean(np.abs(all_pred - all_gt)))
    peak2peak_all    = float(all_gt.max() - all_gt.min())
    subject_norm_mae = subject_mae / peak2peak_all
    subject_r2       = float(1 - np.sum((all_pred - all_gt) ** 2) /
                             (np.sum((all_gt - all_gt.mean()) ** 2) + 1e-8))

    print("-" * 90)
    print(f"\n>>> Subject-level RMSE (all trials concatenated):")
    print(f"    RMSE    : {subject_rmse:.4f} Nm/kg")
    print(f"    MAE     : {subject_mae:.4f} Nm/kg")
    print(f"    NormMAE : {subject_norm_mae*100:.2f}% of peak-to-peak")
    print(f"    R²      : {subject_r2:.4f}")
    print(f"    P2P     : {peak2peak_all:.4f} Nm/kg")

    # Save summary CSV
    rows = [{"trial": tn, **compute_metrics(d["pred"], d["gt"], d["valid_mask"])}
            for tn, d in sorted(results.items())]
    rows.append({"trial": "SUBJECT_TOTAL", "rmse": subject_rmse, "mae": subject_mae,
                 "norm_mae": subject_norm_mae, "r2": subject_r2, "peak2peak": peak2peak_all})
    pd.DataFrame(rows).to_csv(os.path.join(OUTPUT_DIR, "test_metrics.csv"), index=False)
    print(f"\nMetrics saved to {OUTPUT_DIR}/test_metrics.csv")


if __name__ == "__main__":
    main()