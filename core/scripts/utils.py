import os
import numpy as np
import pandas as pd
import torch
import matplotlib.pyplot as plt


def load_subject_mass(csv_path: str) -> dict:
    df = pd.read_csv(csv_path)
    return dict(zip(df["Subject"].str.strip(), df["Weight"].astype(float)))


def load_subject_demographics(csv_path: str) -> dict:
    """
    Returns {subject_id: np.array([height, weight, gender_male, gender_female], dtype=float32)}
    Gender one-hot: M -> [1, 0], F -> [0, 1]
    """
    df = pd.read_csv(csv_path)
    df["Subject"] = df["Subject"].str.strip()
    demo = {}
    for _, row in df.iterrows():
        gender_male   = 1.0 if row["Gender"].strip() == "M" else 0.0
        gender_female = 1.0 if row["Gender"].strip() == "F" else 0.0
        demo[row["Subject"]] = np.array(
            [row["Height"], row["Weight"], gender_male, gender_female],
            dtype=np.float32
        )
    return demo


def subject_from_trial(trial_name: str) -> str:
    return trial_name.split("_")[0]


def unnorm_and_mass_normalize(tensor, scaler_y, mass_kg: float) -> np.ndarray:
    """Inverse z-score (C, T) tensor, divide by mass. Returns (T, C) in Nm/kg."""
    arr    = tensor.detach().cpu().numpy().T
    arr_nm = scaler_y.inverse_transform(arr)
    return arr_nm / mass_kg


def compute_rmse_nmpkg(pred, target, scaler_y, mass_kg: float) -> float:
    pred_nmpkg   = unnorm_and_mass_normalize(pred,   scaler_y, mass_kg)
    target_nmpkg = unnorm_and_mass_normalize(target, scaler_y, mass_kg)
    return float(np.sqrt(np.mean((pred_nmpkg - target_nmpkg) ** 2)))


def compute_mae_nmpkg(pred, target, scaler_y, mass_kg: float) -> float:
    pred_nmpkg   = unnorm_and_mass_normalize(pred,   scaler_y, mass_kg)
    target_nmpkg = unnorm_and_mass_normalize(target, scaler_y, mass_kg)
    return float(np.mean(np.abs(pred_nmpkg - target_nmpkg)))


def compute_normalized_mae_nmpkg(pred, target, scaler_y, mass_kg: float) -> float:
    """MAE normalized by peak-to-peak range of the target (per channel), then averaged."""
    pred_nmpkg   = unnorm_and_mass_normalize(pred,   scaler_y, mass_kg)
    target_nmpkg = unnorm_and_mass_normalize(target, scaler_y, mass_kg)
    mae        = np.mean(np.abs(pred_nmpkg - target_nmpkg), axis=0)   # (C,)
    peak2peak  = target_nmpkg.max(axis=0) - target_nmpkg.min(axis=0)  # (C,)
    peak2peak  = np.where(peak2peak < 1e-6, 1e-6, peak2peak)          # avoid div/0
    return float(np.mean(mae / peak2peak))


def plot_torque_sequence(preds, targets, epoch, split, scaler_y, subject_mass, plot_dir, sample_idx=0):
    os.makedirs(plot_dir, exist_ok=True)

    p_nmpkg = unnorm_and_mass_normalize(preds[sample_idx],   scaler_y, subject_mass)
    t_nmpkg = unnorm_and_mass_normalize(targets[sample_idx], scaler_y, subject_mass)

    plt.figure(figsize=(10, 4))
    plt.plot(t_nmpkg[:, 0], label="Ground Truth (Nm/kg)", color="blue",  alpha=0.8)
    plt.plot(p_nmpkg[:, 0], "--", label="Prediction (Nm/kg)", color="red", alpha=0.8)
    plt.title(f"{split.upper()} Epoch {epoch} - Torque Tracking (Nm/kg)")
    plt.ylabel("Torque (Nm/kg)")
    plt.xlabel("Time Step (Window)")
    plt.legend()
    plt.grid(True, linestyle="--", alpha=0.6)
    plt.savefig(os.path.join(plot_dir, f"{split}_ep{epoch}.png"))
    plt.close()