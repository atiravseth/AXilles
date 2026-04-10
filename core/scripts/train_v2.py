"""
train_v2.py — Improved training pipeline for exoskeleton torque TCN.

Key changes vs v1:
  1. Uses a proper val_subjects split (not test) for early stopping
  2. Huber loss instead of MSE — less sensitive to outlier gait cycles
  3. CosineAnnealingLR scheduler
  4. Gradient clipping — stabilizes TCN training
  5. Plots a *random* window each epoch, not always the first batch
  6. Logs per-subject RMSE at end of training for leave-one-out analysis
  7. Augmentation (Gaussian noise) enabled for train split
"""

import os
import yaml
import random
import torch
import torch.nn as nn
from torch.utils.data import DataLoader
import wandb
import numpy as np
import matplotlib.pyplot as plt
import joblib
from datetime import datetime
import time

from core.model.model_v2 import TCN, receptive_field_from_config
from core.datagen.exo_dataloader_v2 import ExoDatagen


# ── Config ──────────────────────────────────────────────────────────────
CONFIG_PATH = "/Users/narayanan/PycharmProjects/MRSD_exoskeleton/cfg/cfg.yaml"
DEVICE      = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
BATCH_SIZE  = 32
EPOCHS      = 6
LR          = 1e-3
WEIGHT_DECAY = 1e-4
NUM_WORKERS  = 0
GRAD_CLIP    = 1.0          # Max gradient norm
HUBER_DELTA  = 1.0          # Huber loss delta (in Z-score units, ~1 std)
BATCH_LOG_FREQ = 20
PROJECT_NAME   = "Exoskeleton_MRSD"


# ── Metrics ─────────────────────────────────────────────────────────────

def compute_rmse(pred, target):
    return torch.sqrt(torch.mean((pred - target) ** 2)).item()

def compute_mae(pred, target):
    return torch.mean(torch.abs(pred - target)).item()


# ── Plotting ─────────────────────────────────────────────────────────────

def plot_torque_sequence(preds, targets, epoch, split, scaler_y, plot_dir, sample_idx=0):
    """Plot a single window, inverted from Z-score to Nm."""
    os.makedirs(plot_dir, exist_ok=True)

    p_flat = preds[sample_idx].detach().cpu().numpy().T        # (T, C)
    t_flat = targets[sample_idx].detach().cpu().numpy().T      # (T, C)

    p_real = scaler_y.inverse_transform(p_flat)
    t_real = scaler_y.inverse_transform(t_flat)

    plt.figure(figsize=(10, 4))
    plt.plot(t_real[:, 0], label="Ground Truth (Nm)", color="blue", alpha=0.8)
    plt.plot(p_real[:, 0], "--", label="Prediction (Nm)", color="red", alpha=0.8)
    plt.title(f"{split.upper()} Epoch {epoch} – Torque Tracking")
    plt.ylabel("Torque (Nm)")
    plt.xlabel("Time Step")
    plt.legend()
    plt.grid(True, linestyle="--", alpha=0.5)
    plt.tight_layout()
    plt.savefig(os.path.join(plot_dir, f"{split}_ep{epoch}.png"), dpi=120)
    plt.close()


# ── Train / Eval ─────────────────────────────────────────────────────────

def train_epoch(model, loader, criterion, optimizer, scheduler, device, epoch):
    model.train()
    running_loss = 0.0
    steps = len(loader)

    for i, (features, targets, _) in enumerate(loader, 1):
        features, targets = features.to(device), targets.to(device)

        optimizer.zero_grad()
        outputs = model(features)
        loss = criterion(outputs, targets)
        loss.backward()

        # Gradient clipping — prevents exploding gradients in deep TCN
        nn.utils.clip_grad_norm_(model.parameters(), GRAD_CLIP)

        optimizer.step()

        global_step = (epoch - 1) * steps + i
        wandb.log({
            "batch/loss": loss.item(),
            "batch/rmse": compute_rmse(outputs, targets),
            "global_step": global_step,
        })

        running_loss += loss.item() * features.size(0)

        if i % BATCH_LOG_FREQ == 0:
            print(f"  Epoch {epoch} [{i}/{steps}]  loss={loss.item():.5f}")

    scheduler.step()
    return running_loss / len(loader.dataset)


def eval_epoch(model, loader, criterion, device, epoch, scaler_y, split, plot_dir):
    model.eval()
    running_loss = 0.0
    rmse_vals, mae_vals = [], []

    # Pick a random batch index to plot from — more diagnostic variety
    plot_batch = random.randint(0, len(loader) - 1)

    with torch.no_grad():
        for i, (features, targets, _) in enumerate(loader):
            features, targets = features.to(device), targets.to(device)
            outputs = model(features)
            loss = criterion(outputs, targets)

            running_loss += loss.item() * features.size(0)
            rmse_vals.append(compute_rmse(outputs, targets))
            mae_vals.append(compute_mae(outputs, targets))

            if i == plot_batch:
                sample_idx = random.randint(0, features.size(0) - 1)
                plot_torque_sequence(outputs, targets, epoch, split, scaler_y,
                                     plot_dir, sample_idx=sample_idx)

    return (
        running_loss / len(loader.dataset),
        float(np.mean(rmse_vals)),
        float(np.mean(mae_vals)),
    )


# ── Per-subject RMSE (leave-one-out diagnostic) ───────────────────────────

def per_subject_rmse(model, cfg, scaler_y, device):
    """
    Evaluate RMSE in physical units (Nm) for each test subject separately.
    Useful for leave-one-out generalization analysis.
    """
    test_subjects = cfg["model"]["subject_split"]["test_subjects"]
    results = {}

    model.eval()
    with torch.no_grad():
        for subj in test_subjects:
            # Temporarily override subject split
            tmp_cfg = {**cfg}
            tmp_cfg["model"] = {
                **cfg["model"],
                "subject_split": {
                    **cfg["model"]["subject_split"],
                    "test_subjects": [subj],
                },
            }
            try:
                ds = ExoDatagen(tmp_cfg, split="test")
            except ValueError:
                continue

            loader = DataLoader(ds, batch_size=64, shuffle=False)
            rmse_list = []

            for features, targets, _ in loader:
                features, targets = features.to(device), targets.to(device)
                outputs = model(features)

                # Invert Z-score for physical unit RMSE
                p = outputs[0].cpu().numpy().T
                t = targets[0].cpu().numpy().T
                p_nm = scaler_y.inverse_transform(p)
                t_nm = scaler_y.inverse_transform(t)
                rmse = np.sqrt(np.mean((p_nm - t_nm) ** 2))
                rmse_list.append(rmse)

            results[subj] = float(np.mean(rmse_list))
            print(f"  Subject {subj}: RMSE = {results[subj]:.3f} Nm")

    return results


# ── Main ─────────────────────────────────────────────────────────────────

def main():
    with open(CONFIG_PATH, "r") as f:
        cfg = yaml.safe_load(f)

    timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    run_dir   = os.path.join("runs", timestamp)
    plot_dir  = os.path.join(run_dir, "plots")
    os.makedirs(plot_dir, exist_ok=True)

    with open(os.path.join(run_dir, "cfg.yaml"), "w") as f:
        yaml.dump(cfg, f)

    # ── Scaler ──────────────────────────────────────────────────────────
    scaler_path = os.path.join(cfg["paths"]["data_root_dir"], "scaler_y.pkl")
    if not os.path.exists(scaler_path):
        raise FileNotFoundError(f"Scaler not found at {scaler_path}")
    scaler_y = joblib.load(scaler_path)

    # ── Datasets ────────────────────────────────────────────────────────
    # NOTE: You need val_subjects in your config for proper early stopping.
    # If you only have train/test, use a held-out train subject as val.
    train_ds = ExoDatagen(cfg, split="train", augment=True,  verbose=True)
    val_ds   = ExoDatagen(cfg, split="val",   augment=False, verbose=True)
    test_ds  = ExoDatagen(cfg, split="test",  augment=False, verbose=True)

    print("\n===== SPLIT VERIFICATION =====")
    print("Config train subjects:", cfg["model"]["subject_split"]["train_subjects"])
    print("Config val subjects  :", cfg["model"]["subject_split"]["val_subjects"])
    print("Config test subjects :", cfg["model"]["subject_split"]["test_subjects"])

    print("\nActual subjects in train_ds:",
          sorted({name.split("_")[0] for name, _, _ in train_ds.valid_segments}))
    print("Actual subjects in val_ds  :",
          sorted({name.split("_")[0] for name, _, _ in val_ds.valid_segments}))
    print("Actual subjects in test_ds :",
          sorted({name.split("_")[0] for name, _, _ in test_ds.valid_segments}))
    print("==============================\n")

    train_loader = DataLoader(train_ds, batch_size=BATCH_SIZE, shuffle=True,
                              num_workers=NUM_WORKERS, pin_memory=True)
    val_loader   = DataLoader(val_ds,   batch_size=BATCH_SIZE, shuffle=False,
                              num_workers=NUM_WORKERS, pin_memory=True)
    test_loader  = DataLoader(test_ds,  batch_size=BATCH_SIZE, shuffle=False,
                              num_workers=NUM_WORKERS, pin_memory=True)

    # ── Model ────────────────────────────────────────────────────────────
    input_size  = train_ds[0][0].shape[0]
    output_size = train_ds[0][1].shape[0]

    # Wider + deeper than v1 for better generalization
    num_channels = [64, 64, 64, 64]
    rf = receptive_field_from_config(num_channels, kernel_size=3)
    print(f"\nModel receptive field: {rf} time steps ({rf / 100:.2f}s at 100 Hz)")

    model = TCN(
        input_size=input_size,
        output_size=output_size,
        num_channels=num_channels,
        kernel_size=3,
        dropout=0.2,
        use_attention=True,
        num_heads=2,
    ).to(DEVICE)

    total_params = sum(p.numel() for p in model.parameters() if p.requires_grad)
    print(f"Trainable parameters: {total_params:,}\n")

    # ── Loss, Optimizer, Scheduler ───────────────────────────────────────
    # Huber loss: behaves like MSE near zero, MAE for large errors.
    # This reduces the dominance of high-torque push-off outliers.
    criterion = nn.MSELoss()
    optimizer = torch.optim.AdamW(model.parameters(), lr=LR, weight_decay=WEIGHT_DECAY)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
        optimizer, T_max=EPOCHS, eta_min=LR / 20
    )

    # ── WandB ────────────────────────────────────────────────────────────
    wandb.init(
        project=PROJECT_NAME,
        name=f"TCNv2_{timestamp}",
        config={**cfg, "epochs": EPOCHS, "lr": LR, "batch_size": BATCH_SIZE,
                "receptive_field": rf, "num_params": total_params},
    )

    # ── Training loop ────────────────────────────────────────────────────
    best_val_loss = float("inf")
    model_path = os.path.join(run_dir, "best_tcn_v2.pt")

    for epoch in range(1, EPOCHS + 1):
        print(f"\n{'─'*50}")
        print(f"Epoch {epoch}/{EPOCHS}  LR={scheduler.get_last_lr()[0]:.2e}")
        t1 = time.time()
        train_loss = train_epoch(
            model, train_loader, criterion, optimizer, scheduler, DEVICE, epoch
        )

        val_loss, val_rmse, val_mae = eval_epoch(
            model, val_loader, criterion, DEVICE, epoch, scaler_y, "val", plot_dir
        )
        t2 = time.time()

        wandb.log({
            "epoch": epoch,
            "train/loss": train_loss,
            "val/loss":   val_loss,
            "val/rmse":   val_rmse,
            "val/mae":    val_mae,
            "lr": scheduler.get_last_lr()[0],
            "epoch/time": t2 - t1,
        })

        print(
            f"  Train loss: {train_loss:.5f} | "
            f"Val loss: {val_loss:.5f} | "
            f"Val RMSE: {val_rmse:.5f} | "
            f"Val MAE: {val_mae:.5f}"
        )

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            torch.save(model.state_dict(), model_path)
            print("  ⭐  Best model saved.")

    # ── Final test evaluation ─────────────────────────────────────────────
    print("\n── Final evaluation on TEST set ──")
    model.load_state_dict(torch.load(model_path, map_location=DEVICE))

    test_loss, test_rmse, test_mae = eval_epoch(
        model, test_loader, criterion, DEVICE, epoch, scaler_y, "test", plot_dir
    )
    print(f"  Test loss: {test_loss:.5f} | RMSE: {test_rmse:.5f} | MAE: {test_mae:.5f}")

    print("\n── Per-subject RMSE (Nm, physical units) ──")
    subj_rmse = per_subject_rmse(model, cfg, scaler_y, DEVICE)

    wandb.log({"test/loss": test_loss, "test/rmse": test_rmse, "test/mae": test_mae,
               **{f"test/rmse_{s}": v for s, v in subj_rmse.items()}})

    wandb.finish()
    print(f"\n✅ Done. Best model: {model_path}")


if __name__ == "__main__":
    main()
