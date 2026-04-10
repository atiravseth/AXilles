import os
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
from scipy.signal import butter, filtfilt

BASE_PATH = "csv_output"
SENSORS = ['fp', 'gon', 'id', 'imu']
TERRAIN = 'treadmill'
TARGET_COLS = ['ankle_angle_r_moment']
FS = 200
CUTOFF = 6


def apply_lowpass_filter(df, cutoff=6, fs=200, order=5):
    if df.empty: return df
    nyquist = 0.5 * fs
    normal_cutoff = cutoff / nyquist
    b, a = butter(order, normal_cutoff, btype='low', analog=False)
    filtered_df = df.copy()
    for col in df.columns:
        if np.issubdtype(df[col].dtype, np.number) and len(df) > order * 3:
            series = df[col].interpolate(method='linear').fillna(0)
            filtered_df[col] = filtfilt(b, a, series)
    return filtered_df


def read_and_combine_synced(participant_path, trial_name):
    terrain_path = os.path.join(participant_path, TERRAIN)
    imu_path = os.path.join(terrain_path, 'imu', trial_name, 'data.csv')
    if not os.path.exists(imu_path): return pd.DataFrame(), pd.DataFrame()

    master_df = pd.read_csv(imu_path)
    imu_cols = [c for c in master_df.columns if 'trunk' not in c.lower() and c.lower() != 'header']
    combined_features = master_df[['Header'] + imu_cols].add_prefix("imu_")
    combined_targets = pd.DataFrame()

    for sensor in [s for s in SENSORS if s != 'imu']:
        csv_path = os.path.join(terrain_path, sensor, trial_name, 'data.csv')
        if not os.path.exists(csv_path): continue
        df = pd.read_csv(csv_path)
        step = 5 if sensor in ['fp', 'gon'] else 1
        df_resampled = df.iloc[::step, :].reset_index(drop=True)
        df_final = df_resampled.iloc[:len(master_df)].reset_index(drop=True)

        if sensor == 'gon':
            cols = [c for c in df_final.columns if 'sagittal' in c.lower()]
            combined_features = pd.concat([combined_features, df_final[cols].add_prefix("gon_")], axis=1)
        elif sensor == 'fp':
            tread_cols = ['Treadmill_R_vy', 'Treadmill_R_px', 'Treadmill_R_pz']
            valid = [c for c in tread_cols if c in df_final.columns]
            if valid: combined_features = pd.concat([combined_features, df_final[valid].add_prefix("fp_")], axis=1)
        elif sensor == 'id':
            target_match = [c for c in df_final.columns if any(t in c.lower() for t in TARGET_COLS)]
            if target_match: combined_targets = pd.concat([df_final[['Header']], df_final[target_match]], axis=1)

    return combined_features, combined_targets


def visualize_random_trial():
    participants = [d for d in os.listdir(BASE_PATH)
                    if os.path.isdir(os.path.join(BASE_PATH, d)) and not d.startswith('.')]

    if not participants:
        print("No participants found.")
        return

    participant = participants[0]
    p_path = os.path.join(BASE_PATH, participant)
    imu_dir = os.path.join(p_path, TERRAIN, 'imu')

    if not os.path.exists(imu_dir):
        print(f"No IMU directory found for {participant} at {imu_dir}")
        return

    trials = [t for t in os.listdir(imu_dir) if not t.startswith('.')]

    if not trials:
        print(f"No valid trials found in {imu_dir}")
        return

    trial = trials[0]
    print(f"--- Visualizing Data for: {participant} | Trial: {trial} ---")

    features, targets = read_and_combine_synced(p_path, trial)

    if features.empty or targets.empty:
        print(f"Error: Combined data is empty for trial {trial}. Check if data.csv exists in all sensor folders.")
        return

    fig1, axes1 = plt.subplots(3, 1, figsize=(12, 10), sharex=True)

    accel_cols = [col for col in features.columns if 'accel' in col.lower()]
    if accel_cols:
        axes1[0].plot(features[accel_cols[0]], label='Raw IMU Accel', alpha=0.7, color='gray')
    axes1[0].set_title("IMU Acceleration (Raw)")
    axes1[0].set_ylabel("m/s²")
    axes1[0].grid(True, alpha=0.3)
    axes1[0].legend()

    gon_cols = [c for c in features.columns if 'gon_' in c]
    for col in gon_cols:
        axes1[1].plot(features[col], label=col)
    axes1[1].set_title("Joint Angles (Goniometers)")
    axes1[1].set_ylabel("Degrees (°)")
    axes1[1].legend(loc='upper right', bbox_to_anchor=(1.15, 1), fontsize='small')
    axes1[1].grid(True, alpha=0.3)

    target_data = targets.drop(columns=['Header'], errors='ignore')
    if not target_data.empty:
        axes1[2].plot(target_data.iloc[:, 0], color='red', linewidth=1.5)
        axes1[2].set_title(f"Torque: {TARGET_COLS[0]}")
        axes1[2].set_ylabel("Nm")
    axes1[2].set_xlabel("Samples")
    axes1[2].grid(True, alpha=0.3)

    plt.tight_layout()
    plt.show()

# plting 
    if accel_cols:
        raw_imu = features[accel_cols].copy()
        filtered_imu = apply_lowpass_filter(raw_imu, cutoff=CUTOFF, fs=FS)

        fig2, ax2 = plt.subplots(figsize=(12, 4))
        for col in raw_imu.columns:
            ax2.plot(raw_imu[col], label=f'{col} Raw', alpha=0.3, color='gray')
            ax2.plot(filtered_imu[col], label=f'{col} Filtered', linewidth=1.2)

        ax2.set_title(f"IMU Acceleration: Raw vs Filtered ({CUTOFF}Hz)")
        ax2.set_ylabel("m/s²")
        ax2.set_xlabel("Samples")
        ax2.grid(True, alpha=0.3)
        ax2.legend(loc='upper right', fontsize='small')

        plt.tight_layout()
        plt.show()
    else:
        print("No accelerometer columns found for filtered IMU plot.")


if __name__ == "__main__":
    visualize_random_trial()