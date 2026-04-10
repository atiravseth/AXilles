import os
import torch
import pandas as pd
import numpy as np
from torch.utils.data import Dataset
from typing import List, Dict, Tuple, Optional


class ExoDatagen(Dataset):
    _HEIGHT_MEAN, _HEIGHT_STD = 1.7055, 0.0724
    _WEIGHT_MEAN, _WEIGHT_STD = 68.5018, 11.0719

    def __init__(
        self,
        cfg,
        split: str = "train",
        verbose: bool = False,
        augment: bool = False,
        noise_std: float = 0.01,
    ):
        self.cfg        = cfg
        self.split      = split
        self.verbose    = verbose
        self.augment    = augment
        self.noise_std  = noise_std
        self.data_root_dir = cfg["paths"]["data_root_dir"]
        self.window_length = cfg["model"].get("min_segment_length", 300)
        self.stride        = cfg["model"].get("stride", 10)
        self.subject_split = cfg["model"]["subject_split"]
        self.feature_cols: Optional[List[str]] = self._resolve_feature_cols()
        
        ah_cfg = cfg["model"].get("action_horizon", {})
        
        self.action_horizon_enabled = ah_cfg.get("enabled", False)
        self.H = int(ah_cfg.get("H", 10)) if self.action_horizon_enabled else 0
        self.use_subject_embedding = cfg["model"].get("use_subject_embedding", False)

        self._demo_vec: Dict[str, np.ndarray] = {}
        self._load_demographics(cfg["paths"]["demographics_csv"])
        self.trial_names = self._get_trial_names()
        self._cache: Dict[str, Tuple[np.ndarray, np.ndarray]] = {}
        self._load_all_trials()
        self.valid_segments: List[Tuple[str, int, int]] = []
        self._generate_segments()

        if self.verbose:
            label = self._active_set_name() or "all"
            n_feat = len(self.feature_cols) if self.feature_cols else "all"
            print(f"[{self.split.upper()}] Feature set       : {label} ({n_feat} features)")
            print(f"[{self.split.upper()}] Subject embedding : {'enabled' if self.use_subject_embedding else 'disabled'}")
            print(f"[{self.split.upper()}] Total windows     : {len(self.valid_segments)}")

    def __len__(self):
        return len(self.valid_segments)

    def __getitem__(self, idx):
        trial_name, start_idx, end_idx = self.valid_segments[idx]
        feat_arr, targ_arr = self._cache[trial_name]

        x = torch.from_numpy(feat_arr[start_idx : end_idx + 1].T.copy())  # (C_feat, T)
        y = torch.from_numpy(targ_arr[start_idx : end_idx + 1].T.copy())  # (C_targ, T)

        if self.augment and self.split == "train":
            x = x + torch.randn_like(x) * self.noise_std

        subject_id = trial_name.split("_")[0]
        return x, y, trial_name, subject_id

    def _load_demographics(self, csv_path: str):
        df = pd.read_csv(csv_path)
        df["Subject"] = df["Subject"].str.strip()
        for _, row in df.iterrows():
            gender_male   = 1.0 if row["Gender"].strip() == "M" else 0.0
            gender_female = 1.0 if row["Gender"].strip() == "F" else 0.0
            height_norm   = (row["Height"] - self._HEIGHT_MEAN) / self._HEIGHT_STD
            weight_norm   = (row["Weight"] - self._WEIGHT_MEAN) / self._WEIGHT_STD
            self._demo_vec[row["Subject"]] = np.array(
                [height_norm, weight_norm, gender_male, gender_female],
                dtype=np.float32
            )

    def get_demo_vec(self, subject_id: str) -> np.ndarray:
        return self._demo_vec[subject_id]

    def _active_set_name(self) -> Optional[str]:
        fs_cfg = self.cfg.get("feature_sets", {})
        return fs_cfg.get("active", None)

    def _resolve_feature_cols(self) -> Optional[List[str]]:
        fs_cfg = self.cfg.get("feature_sets", {})
        if not fs_cfg:
            return None
        active = fs_cfg.get("active")
        if not active:
            return None
        cols = fs_cfg.get(active)
        if cols is None:
            raise ValueError(
                f"feature_sets.active='{active}' but no matching key found under feature_sets. "
                f"Available sets: {[k for k in fs_cfg if k != 'active']}"
            )
        return list(cols)

    def _get_trial_names(self) -> List[str]:
        all_files = sorted(
            f for f in os.listdir(self.data_root_dir)
            if f.endswith("_features.csv")
        )

        def subject_from_name(fname: str) -> str:
            return fname.split("_")[0]

        split_map = {
            "train": self.subject_split.get("train_subjects", []),
            "val":   self.subject_split.get("val_subjects",   []),
            "test":  self.subject_split.get("test_subjects",  []),
        }
        if self.split not in split_map:
            raise ValueError(f"Unknown split: {self.split}")

        subjects = split_map[self.split]
        selected = [f for f in all_files if subject_from_name(f) in subjects]

        if self.verbose:
            print(f"[{self.split.upper()}] Subjects : {subjects}")
            print(f"[{self.split.upper()}] Trials   : {len(selected)}")

        if not selected:
            raise ValueError(f"No trials found for split={self.split}")

        return selected

    def _load_all_trials(self):
        for trial_name in self.trial_names:
            feat_path = os.path.join(self.data_root_dir, trial_name)
            targ_path = feat_path.replace("_features.csv", "_targets.csv")

            if not os.path.exists(targ_path):
                if self.verbose:
                    print(f"  Missing target file for {trial_name}, skipping.")
                continue

            feat_df  = pd.read_csv(feat_path)
            feat_arr = self._select_features(feat_df, trial_name)
            targ_arr = pd.read_csv(targ_path).values.astype(np.float32)

            self._cache[trial_name] = (feat_arr, targ_arr)

        if self.verbose:
            print(f"[{self.split.upper()}] Cached {len(self._cache)} trials in RAM.")

    # def _select_features(self, df: pd.DataFrame, trial_name: str) -> np.ndarray:
    #     if self.feature_cols is None:
    #         return df.values.astype(np.float32)
    #
    #     missing = [c for c in self.feature_cols if c not in df.columns]
    #     if missing:
    #         raise ValueError(
    #             f"Trial '{trial_name}' is missing columns for feature set "
    #             f"'{self._active_set_name()}': {missing}"
    #         )
    #
    #     return df[self.feature_cols].values.astype(np.float32)

    def _select_features(self, df: pd.DataFrame, trial_name: str) -> np.ndarray:
        if self.feature_cols is None:
            return df.values.astype(np.float32)

        missing = [c for c in self.feature_cols if c not in df.columns]
        if missing:
            raise ValueError(
                f"Trial '{trial_name}' is missing columns for feature set "
                f"'{self._active_set_name()}': {missing}"
            )

        selected_df = df[self.feature_cols].copy()
        fp_col = "fp_Treadmill_R_vy"
        if fp_col in selected_df.columns:
            print(f"===============Converting {trial_name} to Binary ===============")
            min_val = selected_df[fp_col].min()
            threshold = min_val + 1.5  

            selected_df[fp_col] = (selected_df[fp_col] > threshold).astype(np.float32)
            # print(selected_df[fp_col])

        return selected_df.values.astype(np.float32)

    def _generate_segments(self):
        for trial_name, (feat_arr, targ_arr) in self._cache.items():
            valid_mask = ~np.isnan(targ_arr).any(axis=1)
            T = len(valid_mask)

            start = None
            for i, valid in enumerate(valid_mask):
                if valid and start is None:
                    start = i
                elif not valid and start is not None:
                    self._add_windows(trial_name, start, i - 1, T)
                    start = None
            if start is not None:
                self._add_windows(trial_name, start, T - 1, T)

        if not self.valid_segments:
            raise ValueError("No valid sliding windows found!")

    def _add_windows(self, trial_name: str, start: int, end: int, total_len: int):
        length = end - start + 1
        if length < self.window_length:
            return
        last_start = end - self.window_length + 1
        for s in range(start, last_start + 1, self.stride):
            win_end = s + self.window_length - 1
            if win_end + self.H < total_len:
                self.valid_segments.append((trial_name, s, win_end))