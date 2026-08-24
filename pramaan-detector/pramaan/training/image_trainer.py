"""
Image forensic trainer.
Dataset CSV format:
    /path/to/image.jpg,1
    /path/to/image.png,0
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional

import torch
import torch.nn as nn
from torch.utils.data import Dataset, DataLoader
from PIL import Image

import io, random
from pramaan.detectors.image_detector import ImageForensicNet, _transform, _train_aug


def _jpeg_recompress(img: Image.Image, quality_range=(50, 95)) -> Image.Image:
    """Simulate JPEG recompression artifact (redistribution via screenshot/share)."""
    buf = io.BytesIO()
    q = random.randint(*quality_range)
    img.save(buf, format="JPEG", quality=q)
    buf.seek(0)
    return Image.open(buf).copy()


class _ImageDataset(Dataset):
    def __init__(self, csv_path: str, augment: bool = False):
        import csv
        self.samples = []
        with open(csv_path) as f:
            for row in csv.reader(f):
                self.samples.append((row[0].strip(), int(row[1].strip())))
        self.augment   = augment
        self.transform = _train_aug if augment else _transform

    def __len__(self):
        return len(self.samples)

    def __getitem__(self, idx):
        path, label = self.samples[idx]
        img = Image.open(path).convert("RGB")
        # JPEG recompression simulation (50% chance during training)
        if self.augment and random.random() < 0.5:
            img = _jpeg_recompress(img)
        return self.transform(img), torch.tensor(label, dtype=torch.float32)


class ImageTrainer:
    def __init__(
        self,
        train_csv: str,
        val_csv: Optional[str] = None,
        weights_path: Optional[str] = None,
        output_path: str = "weights/image_detector.pt",
        device: str = "cpu",
        epochs: int = 10,
        batch_size: int = 16,
        lr: float = 1e-4,
    ):
        self.output_path = output_path
        self.device = torch.device(device)
        self.epochs = epochs

        self.model = ImageForensicNet(pretrained=True)
        if weights_path and Path(weights_path).exists():
            self.model.load_state_dict(torch.load(weights_path, map_location=device))
        self.model.to(self.device)

        self.train_loader = DataLoader(
            _ImageDataset(train_csv, augment=True), batch_size=batch_size, shuffle=True
        )
        self.val_loader = (
            DataLoader(_ImageDataset(val_csv), batch_size=batch_size)
            if val_csv else None
        )
        self.criterion = nn.BCEWithLogitsLoss()
        self.optimizer = torch.optim.AdamW(self.model.parameters(), lr=lr)

    def train(self) -> None:
        for epoch in range(1, self.epochs + 1):
            self.model.train()
            total_loss = 0.0
            for imgs, labels in self.train_loader:
                imgs, labels = imgs.to(self.device), labels.to(self.device)
                self.optimizer.zero_grad()
                loss = self.criterion(self.model(imgs).squeeze(1), labels)
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
            for imgs, labels in self.val_loader:
                imgs, labels = imgs.to(self.device), labels.to(self.device)
                preds = (torch.sigmoid(self.model(imgs).squeeze(1)) >= 0.5).float()
                correct += (preds == labels).sum().item()
                total += len(labels)
        return correct / total if total else 0.0
