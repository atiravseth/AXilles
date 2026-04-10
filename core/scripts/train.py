import os
import time
import random
from datetime import datetime
from typing import List

import yaml
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.utils.data import DataLoader
import wandb
import numpy as np
import joblib

from core.model.model_v3 import TCN
from core.datagen.exo_dataloader import ExoDatagen
from utils import (
    load_subject_mass,
    subject_from_trial,
    compute_rmse_nmpkg,
    compute_mae_nmpkg,
    compute_normalized_mae_nmpkg,
    plot_torque_sequence,
)


CONFIG_PATH      = "/Users/narayanan/PycharmProjects/MRSD_exoskeleton/cfg/cfg.yaml"
DEMOGRAPHICS_CSV = "/Users/narayanan/PycharmProjects/MRSD_exoskeleton/csv_output/subject_info.csv"
DEVICE           = torch.device("mps" if torch.backends.mps.is_available() else "cpu")

BATCH_SIZE     = 32
EPOCHS         = 10
LR             = 5e-5
NUM_WORKERS    = 0
BATCH_LOG_FREQ = 10

PROJECT_NAME = "Exoskeleton_MRSD"
RUN_NAME     = "TCN_mid_learnable_embedding_13_train_subjects_binary_fp"

NUM_CHANNELS  = [80] * 5
KERNEL_SIZE   = 5
ACTIVATION    = "ReLU"
NORM_TYPE     = "WeightNorm"
DROPOUT_TYPE  = "Spatial"
DROPOUT       = 0.15
L1_REG        = 0.0
L2_REG        = 0.0
EMB_DIM       = 4


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-8))


def find_closest_training_subject(test_id: str, train_ids: List[str], dataset) -> str:
    """Return the training subject with highest cosine similarity to test subject."""
    test_vec = dataset.get_demo_vec(test_id)
    best_id, best_sim = None, -1.0
    for sid in train_ids:
        sim = cosine_similarity(test_vec, dataset.get_demo_vec(sid))
        if sim > best_sim:
            best_sim, best_id = sim, sid
    print(f"  Unseen subject {test_id} → closest training subject: {best_id} (cosine={best_sim:.4f})")
    return best_id


def build_subject_index(subject_ids: list) -> dict:
    """Map subject_id string → integer index for nn.Embedding."""
    return {sid: idx for idx, sid in enumerate(sorted(set(subject_ids)))}


def get_subject_idx_tensor(subject_ids: list, subject_to_idx: dict, device) -> torch.Tensor:
    return torch.tensor(
        [subject_to_idx[sid] for sid in subject_ids],
        dtype=torch.long, device=device
    )


def train_epoch(model, loader, optimizer, device, epoch, scaler_y, subject_mass,
                subject_to_idx, use_subject_embedding):
    model.train()
    running_loss    = 0.0
    steps_per_epoch = len(loader)

    for batch_idx, (features, targets, trial_names, subject_ids) in enumerate(loader, 1):
        features = features.to(device)
        targets  = targets.to(device)

        subject_idx = get_subject_idx_tensor(subject_ids, subject_to_idx, device) \
                      if use_subject_embedding else None

        optimizer.zero_grad()
        outputs = model(features, subject_idx)
        loss    = nn.functional.mse_loss(outputs, targets) + model.regularization_loss()
        loss.backward()
        optimizer.step()

        batch_rmse = float(np.mean([
            compute_rmse_nmpkg(outputs[i], targets[i], scaler_y,
                               subject_mass[subject_from_trial(tn)])
            for i, tn in enumerate(trial_names)
        ]))

        global_step = (epoch - 1) * steps_per_epoch + batch_idx
        wandb.log({
            "batch/loss":       loss.item(),
            "batch/rmse_nmpkg": batch_rmse,
            "global_step":      global_step,
        })

        running_loss += loss.item() * features.size(0)

        if batch_idx % BATCH_LOG_FREQ == 0:
            print(f"Epoch {epoch} [{batch_idx}/{steps_per_epoch}] "
                  f"Loss: {loss.item():.6f}  RMSE: {batch_rmse:.4f} Nm/kg")

    return running_loss / len(loader.dataset)


def eval_epoch(model, loader, device, epoch, scaler_y, subject_mass,
               subject_to_idx, use_subject_embedding, plot_dir, split="val"):
    model.eval()
    running_loss  = 0.0
    rmse_vals     = []
    mae_vals      = []
    norm_mae_vals = []
    plot_batch    = random.randint(0, len(loader) - 1)

    with torch.no_grad():
        for batch_idx, (features, targets, trial_names, subject_ids) in enumerate(loader):
            features = features.to(device)
            targets  = targets.to(device)

            subject_idx = get_subject_idx_tensor(subject_ids, subject_to_idx, device) \
                          if use_subject_embedding else None

            outputs = model(features, subject_idx)
            loss    = nn.functional.mse_loss(outputs, targets)

            for i, tn in enumerate(trial_names):
                mass = subject_mass[subject_from_trial(tn)]
                rmse_vals.append(compute_rmse_nmpkg(outputs[i], targets[i], scaler_y, mass))
                mae_vals.append(compute_mae_nmpkg(outputs[i],   targets[i], scaler_y, mass))
                norm_mae_vals.append(compute_normalized_mae_nmpkg(outputs[i], targets[i], scaler_y, mass))

            if batch_idx == plot_batch:
                sample_idx  = random.randint(0, features.size(0) - 1)
                sample_mass = subject_mass[subject_from_trial(trial_names[sample_idx])]
                plot_torque_sequence(
                    outputs, targets, epoch, split,
                    scaler_y, sample_mass, plot_dir, sample_idx
                )

            running_loss += loss.item() * features.size(0)

    return (
        running_loss / len(loader.dataset),
        float(np.mean(rmse_vals)),
        float(np.mean(mae_vals)),
        float(np.mean(norm_mae_vals)),
    )


def main():
    from typing import List

    with open(CONFIG_PATH, "r") as f:
        cfg = yaml.safe_load(f)

    subject_mass          = load_subject_mass(DEMOGRAPHICS_CSV)
    use_subject_embedding = cfg["model"].get("use_subject_embedding", False)

    timestamp  = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    run_dir    = os.path.join("runs", timestamp)
    plot_dir   = os.path.join(run_dir, "val_torque_plots")
    model_path = os.path.join(run_dir, "best_tcn.pt")
    os.makedirs(plot_dir, exist_ok=True)

    with open(os.path.join(run_dir, "cfg.yaml"), "w") as f:
        yaml.dump(cfg, f)

    scaler_path = os.path.join(cfg["paths"]["data_root_dir"], "scaler_y.pkl")
    if not os.path.exists(scaler_path):
        print(f"Scaler not found at {scaler_path}. Exiting.")
        return
    scaler_y = joblib.load(scaler_path)

    train_dataset = ExoDatagen(cfg, split="train", verbose=True)
    val_dataset   = ExoDatagen(cfg, split="val",   verbose=True)
    test_dataset  = ExoDatagen(cfg, split="test",  verbose=True)

    # Build subject index from training subjects only
    train_subjects = cfg["model"]["subject_split"]["train_subjects"]
    val_subjects   = cfg["model"]["subject_split"]["val_subjects"]
    test_subjects  = cfg["model"]["subject_split"]["test_subjects"]
    all_known      = sorted(set(train_subjects))
    subject_to_idx = build_subject_index(all_known)
    num_train_subjects = len(all_known)

    # For unseen val/test subjects: find closest training subject by cosine similarity
    # and map them to that training subject's index
    if use_subject_embedding:
        for sid in val_subjects + test_subjects:
            closest = find_closest_training_subject(sid, train_subjects, train_dataset)
            subject_to_idx[sid] = subject_to_idx[closest]

    wandb.init(project=PROJECT_NAME, name=f"{RUN_NAME}_{timestamp}", config={
        **cfg,
        "num_channels":          NUM_CHANNELS,
        "kernel_size":           KERNEL_SIZE,
        "activation":            ACTIVATION,
        "norm_type":             NORM_TYPE,
        "dropout_type":          DROPOUT_TYPE,
        "dropout":               DROPOUT,
        "l1_reg":                L1_REG,
        "l2_reg":                L2_REG,
        "lr":                    LR,
        "batch_size":            BATCH_SIZE,
        "epochs":                EPOCHS,
        "use_subject_embedding": use_subject_embedding,
        "emb_dim":               EMB_DIM if use_subject_embedding else 0,
    })

    train_loader = DataLoader(train_dataset, batch_size=BATCH_SIZE, shuffle=True,  num_workers=NUM_WORKERS)
    val_loader   = DataLoader(val_dataset,   batch_size=BATCH_SIZE, shuffle=False, num_workers=NUM_WORKERS)
    test_loader  = DataLoader(test_dataset,  batch_size=BATCH_SIZE, shuffle=False, num_workers=NUM_WORKERS)

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
        l1_reg                = L1_REG,
        l2_reg                = L2_REG,
        use_subject_embedding = use_subject_embedding,
        num_subjects          = num_train_subjects,
        emb_dim               = EMB_DIM,
    ).to(DEVICE)

    optimizer     = torch.optim.Adam(model.parameters(), lr=LR)
    best_val_loss = float("inf")

    for epoch in range(1, EPOCHS + 1):
        print(f"\n--- Epoch {epoch}/{EPOCHS} ---")
        t0 = time.time()

        train_loss = train_epoch(
            model, train_loader, optimizer, DEVICE,
            epoch, scaler_y, subject_mass,
            subject_to_idx, use_subject_embedding
        )
        val_loss, val_rmse, val_mae, val_norm_mae = eval_epoch(
            model, val_loader, DEVICE,
            epoch, scaler_y, subject_mass,
            subject_to_idx, use_subject_embedding,
            plot_dir, "val"
        )

        wandb.log({
            "epoch":              epoch,
            "train/epoch_loss":   train_loss,
            "val/loss":           val_loss,
            "val/rmse_nmpkg":     val_rmse,
            "val/mae_nmpkg":      val_mae,
            "val/norm_mae_nmpkg": val_norm_mae,
            "epoch/time":         time.time() - t0,
        })

        print(f"Train Loss: {train_loss:.6f} | Val Loss: {val_loss:.6f} | "
              f"Val RMSE: {val_rmse:.4f} Nm/kg | Val MAE: {val_mae:.4f} Nm/kg | "
              f"Val Peak to Peak MAE: {val_norm_mae:.4f} Nm/kg")

        if val_loss < best_val_loss:
            best_val_loss = val_loss
            torch.save(model.state_dict(), model_path)
            print("Best model saved.")

    model.load_state_dict(torch.load(model_path, map_location=DEVICE))
    test_loss, test_rmse, test_mae, test_norm_mae = eval_epoch(
        model, test_loader, DEVICE,
        EPOCHS, scaler_y, subject_mass,
        subject_to_idx, use_subject_embedding,
        plot_dir, "test"
    )

    print(f"Test Loss: {test_loss:.6f} | Test RMSE: {test_rmse:.4f} Nm/kg | "
          f"Test MAE: {test_mae:.4f} Nm/kg | Test Peak to Peak MAE: {test_norm_mae:.4f} Nm/kg")
    wandb.log({
        "test/loss":          test_loss,
        "test/rmse_nmpkg":    test_rmse,
        "test/mae_nmpkg":     test_mae,
        "test/norm_mae_nmpkg": test_norm_mae,
    })
    wandb.finish()
    print(f"Training complete. Best model: {model_path}")


if __name__ == "__main__":
    main()