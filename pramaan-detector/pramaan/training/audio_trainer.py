"""
Audio forensic trainer.
Dataset CSV format:
    /path/to/audio.wav,1
    /path/to/audio.mp3,0
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional

import random
import numpy as np
import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader

from pramaan.detectors.audio_detector import (
    AudioForensicNet, _load_audio, TARGET_SR, CHUNK_SEC
)

_CHUNK_LEN = int(CHUNK_SEC * TARGET_SR)


def _augment_audio(wav: np.ndarray, sr: int) -> np.ndarray:
    """
    Apply random audio augmentations simulating real redistribution:
    - background noise addition
    - resampling (simulate bitrate/codec change)
    - amplitude scaling
    """
    # additive Gaussian noise (simulate codec noise / background)
    if random.random() < 0.5:
        noise_level = random.uniform(0.001, 0.015)
        wav = wav + noise_level * np.random.randn(*wav.shape).astype(np.float32)

    # simulate resampling artefact: downsample then upsample
    if random.random() < 0.3:
        try:
            from scipy.signal import resample
            orig_len = len(wav)
            target_sr = random.choice([8000, 11025, 22050])
            n_down = int(orig_len * target_sr / sr)
            wav_down = resample(wav, n_down)
            wav = resample(wav_down, orig_len).astype(np.float32)
        except ImportError:
            pass

    # random amplitude scaling
    if random.random() < 0.4:
        wav = wav * random.uniform(0.7, 1.3)

    return np.clip(wav, -1.0, 1.0).astype(np.float32)


class _AudioDataset(Dataset):
    def __init__(self, csv_path: str, augment: bool = False):
        import csv
        self.samples = []
        with open(csv_path) as f:
            for row in csv.reader(f):
                self.samples.append((row[0].strip(), int(row[1].strip())))
        self.augment = augment

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        path, label = self.samples[idx]
        wav, sr = _load_audio(path)
        if self.augment:
            wav = _augment_audio(wav, sr)
        # random chunk start during training, first chunk during eval
        if self.augment and len(wav) > _CHUNK_LEN:
            start = random.randint(0, len(wav) - _CHUNK_LEN)
            chunk = wav[start: start + _CHUNK_LEN]
        else:
            chunk = wav[:_CHUNK_LEN]
        if len(chunk) < _CHUNK_LEN:
            chunk = np.pad(chunk, (0, _CHUNK_LEN - len(chunk)))
        return torch.tensor(chunk, dtype=torch.float32), torch.tensor(label, dtype=torch.float32)


class AudioTrainer:
    def __init__(
        self,
        train_csv: str,
        val_csv: Optional[str] = None,
        weights_path: Optional[str] = None,
        output_path: str = "weights/audio_detector.pt",
        device: str = "cpu",
        epochs: int = 10,
        batch_size: int = 8,
        lr: float = 1e-4,
    ):
        self.output_path = output_path
        self.device = torch.device(device)
        self.epochs = epochs

        self.model = AudioForensicNet(pretrained=True)
        if weights_path and Path(weights_path).exists():
            self.model.load_state_dict(torch.load(weights_path, map_location=device))
        self.model.to(self.device)

        self.train_loader = DataLoader(
            _AudioDataset(train_csv, augment=True), batch_size=batch_size, shuffle=True
        )
        self.val_loader = (
            DataLoader(_AudioDataset(val_csv, augment=False), batch_size=batch_size)
            if val_csv else None
        )
        self.criterion = nn.BCEWithLogitsLoss()
        self.optimizer = torch.optim.AdamW(self.model.parameters(), lr=lr)

    def train(self) -> None:
        for epoch in range(1, self.epochs + 1):
            self.model.train()
            total_loss = 0.0
            for wavs, labels in self.train_loader:
                wavs, labels = wavs.to(self.device), labels.to(self.device)
                self.optimizer.zero_grad()
                loss = self.criterion(self.model(wavs).squeeze(1), labels)
                loss.backward()
                self.optimizer.step()
                total_loss += loss.item()

            val_acc = self._validate() if self.val_loader else None
            val_str = f"  val_acc={val_acc:.4f}" if val_acc is not None else ""
            print(f"Epoch {epoch}/{self.epochs}  loss={total_loss/len(self.train_loader):.4f}{val_str}")

        Path(self.output_path).parent.mkdir(parents=True, exist_ok=True)
        torch.save(self.model.state_dict(), self.output_path)
        print(f"Saved → {self.output_path}")

    def _validate(self) -> float:
        self.model.eval()
        correct = total = 0
        with torch.no_grad():
            for wavs, labels in self.val_loader:
                wavs, labels = wavs.to(self.device), labels.to(self.device)
                preds = (torch.sigmoid(self.model(wavs).squeeze(1)) >= 0.5).float()
                correct += (preds == labels).sum().item()
                total += len(labels)
        return correct / total if total else 0.0
