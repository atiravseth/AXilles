import os
import pandas as pd
import numpy as np
import joblib
from sklearn.preprocessing import StandardScaler
from scipy.signal import butter, filtfilt


CONFIG = {
    "base_path": "csv_output",
    "terrain": "treadmill",
    "sensors": ["fp", "gon", "id", "imu"],
    "output_folder": "processed_zscore_data",
    "target_cols": ["ankle_angle_r_moment"],
    "fs": 100,
    "cutoff": 6,
    "apply_lowpass": False,
    "train_subjects": [
        "AB06", "AB07", "AB08", "AB11", "AB12", "AB13",
        "AB14", "AB15", "AB16", "AB17", "AB18", "AB19", "AB20",
    ],
}

os.makedirs(CONFIG["output_folder"], exist_ok=True)


def apply_lowpass_filter(df, cutoff, fs, order=5):
    if df.empty:
        return df

    nyq = 0.5 * fs
    b, a = butter(order, cutoff / nyq, btype="low")

    filtered = df.copy()
    for col in df.select_dtypes(include=[np.number]).columns:
        if len(df) > order * 3:
            signal = df[col].interpolate().fillna(0)
            filtered[col] = filtfilt(b, a, signal)

    return filtered


def read_trial(participant_path, trial):
    terrain = CONFIG["terrain"]
    terrain_path = os.path.join(participant_path, terrain)

    imu_path = os.path.join(terrain_path, "imu", trial, "data.csv")
    if not os.path.exists(imu_path):
        return None, None

    imu_df = pd.read_csv(imu_path)
    imu_df = imu_df.iloc[::2].reset_index(drop=True)

    imu_cols = [c for c in imu_df.columns if "trunk" not in c.lower() and c.lower() != "header"]
    features = imu_df[["Header"] + imu_cols].add_prefix("imu_")

    targets = pd.DataFrame()

    for sensor in CONFIG["sensors"]:
        if sensor == "imu":
            continue

        path = os.path.join(terrain_path, sensor, trial, "data.csv")
        if not os.path.exists(path):
            continue

        df = pd.read_csv(path)

        step = 10 if sensor in ["fp", "gon"] else 2
        df = df.iloc[::step].reset_index(drop=True)
        df = df.iloc[: len(imu_df)].reset_index(drop=True)

        if sensor == "gon":
            cols = [c for c in df.columns if "sagittal" in c.lower()]
            df[cols] = np.radians(df[cols])
            features = pd.concat([features, df[cols].add_prefix("gon_")], axis=1)

        elif sensor == "fp":
            cols = ["Treadmill_R_vy", "Treadmill_R_px", "Treadmill_R_pz"]
            valid = [c for c in cols if c in df.columns]
            features = pd.concat([features, df[valid].add_prefix("fp_")], axis=1)

        elif sensor == "id":
            cols = [c for c in df.columns if any(t in c.lower() for t in CONFIG["target_cols"])]
            if cols:
                targets = df[cols]

    return features.drop(columns=["imu_Header"]), targets


# def main():
#     base_path = CONFIG["base_path"]
#     participants = [
#         d for d in os.listdir(base_path)
#         if os.path.isdir(os.path.join(base_path, d))
#     ]
#
#     all_features, all_targets, names = [], [], []
#
#     print(f"Loading {len(participants)} participants...")
#
#     for p in participants:
#         p_path = os.path.join(base_path, p)
#         imu_dir = os.path.join(p_path, CONFIG["terrain"], "imu")
#
#         if not os.path.exists(imu_dir):
#             continue
#
#         for trial in os.listdir(imu_dir):
#             X, y = read_trial(p_path, trial)
#
#             if X is None or y is None or X.empty or y.empty:
#                 continue
#
#             if CONFIG["apply_lowpass"]:
#                 X = apply_lowpass_filter(X, CONFIG["cutoff"], CONFIG["fs"])
#
#             start = y.iloc[:, 0].first_valid_index()
#             end = y.iloc[:, 0].last_valid_index()
#             if start is None:
#                 continue
#
#             X = X.iloc[start:end + 1].reset_index(drop=True)
#             y = y.iloc[start:end + 1].reset_index(drop=True)
#
#             all_features.append(X)
#             all_targets.append(y)
#             names.append(f"{p}_{trial}")
#
#     if not all_features:
#         print("No valid trials found.")
#         return
#
#     print("Fitting scalers...")
#     scaler_x = StandardScaler().fit(pd.concat(all_features))
#     scaler_y = StandardScaler().fit(pd.concat(all_targets))
#
#     joblib.dump(scaler_x, os.path.join(CONFIG["output_folder"], "scaler_x.pkl"))
#     joblib.dump(scaler_y, os.path.join(CONFIG["output_folder"], "scaler_y.pkl"))
#
#     print(f"Saving {len(names)} trials...")
#     for i, name in enumerate(names):
#         z_x = scaler_x.transform(all_features[i])
#         z_y = scaler_y.transform(all_targets[i])
#
#         pd.DataFrame(z_x, columns=all_features[i].columns).to_csv(
#             os.path.join(CONFIG["output_folder"], f"{name}_features.csv"),
#             index=False,
#         )
#
#         pd.DataFrame(z_y, columns=all_targets[i].columns).to_csv(
#             os.path.join(CONFIG["output_folder"], f"{name}_targets.csv"),
#             index=False,
#         )
#
#     print(f"Done. Saved in: {CONFIG['output_folder']}")
#     print(f"Feature dim: {all_features[0].shape[1]} | Target dim: {all_targets[0].shape[1]}")

def main():
    base_path = CONFIG["base_path"]
    train_subjects = set(CONFIG["train_subjects"])

    participants = [
        d for d in os.listdir(base_path)
        if os.path.isdir(os.path.join(base_path, d))
    ]

    # Separate buckets: train (fit scalers on these) vs all (transform all)
    train_features, train_targets = [], []
    all_features, all_targets, names = [], [], []

    print(f"Loading {len(participants)} participants...")

    for p in sorted(participants):
        p_path = os.path.join(base_path, p)
        imu_dir = os.path.join(p_path, CONFIG["terrain"], "imu")

        if not os.path.exists(imu_dir):
            continue

        for trial in os.listdir(imu_dir):
            X, y = read_trial(p_path, trial)

            if X is None or y is None or X.empty or y.empty:
                continue

            if CONFIG["apply_lowpass"]:
                X = apply_lowpass_filter(X, CONFIG["cutoff"], CONFIG["fs"])

            start = y.iloc[:, 0].first_valid_index()
            end   = y.iloc[:, 0].last_valid_index()
            if start is None:
                continue

            X = X.iloc[start:end + 1].reset_index(drop=True)
            y = y.iloc[start:end + 1].reset_index(drop=True)

            all_features.append(X)
            all_targets.append(y)
            names.append(f"{p}_{trial}")

            # Only accumulate train subjects for scaler fitting
            if p in train_subjects:
                train_features.append(X)
                train_targets.append(y)

    if not all_features:
        print("No valid trials found.")
        return

    if not train_features:
        raise ValueError("No training subjects found — check train_subjects in CONFIG.")

    print(f"Fitting scalers on {len(train_features)} training trials "
          f"({len(set(CONFIG['train_subjects']))} subjects)...")

    scaler_x = StandardScaler().fit(pd.concat(train_features))
    scaler_y = StandardScaler().fit(pd.concat(train_targets))

    joblib.dump(scaler_x, os.path.join(CONFIG["output_folder"], "scaler_x.pkl"))
    joblib.dump(scaler_y, os.path.join(CONFIG["output_folder"], "scaler_y.pkl"))

    print(f"Saving {len(names)} trials (all splits)...")
    for i, name in enumerate(names):
        z_x = scaler_x.transform(all_features[i])
        z_y = scaler_y.transform(all_targets[i])

        pd.DataFrame(z_x, columns=all_features[i].columns).to_csv(
            os.path.join(CONFIG["output_folder"], f"{name}_features.csv"),
            index=False,
        )
        pd.DataFrame(z_y, columns=all_targets[i].columns).to_csv(
            os.path.join(CONFIG["output_folder"], f"{name}_targets.csv"),
            index=False,
        )

    train_names = [n for n in names if n.split("_")[0] in train_subjects]
    other_names = [n for n in names if n.split("_")[0] not in train_subjects]
    print(f"Done. Saved in: {CONFIG['output_folder']}")
    print(f"  Scaler fit on : {len(train_names)} train trials")
    print(f"  Transformed   : {len(other_names)} val/test trials (no leakage)")
    print(f"Feature dim: {all_features[0].shape[1]} | Target dim: {all_targets[0].shape[1]}")


if __name__ == "__main__":
    main()
