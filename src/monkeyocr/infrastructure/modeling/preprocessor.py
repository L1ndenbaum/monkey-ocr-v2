import os
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import torch
from PIL import Image

import torch.nn.functional as F


def bilinear_preprocessing(warped_img, point_positions, img_size):
    """
    Utility function that preprocesss an image.
    preprocess warped_img based on the 2D grid point_positions with a size img_size.
    Args:
        warped_img  :       torch.Tensor of shape BxCxHxW (dtype float)
        point_positions:    torch.Tensor of shape Bx2xGhxGw (dtype float)
        img_size:           tuple of int [w, h]
    """
    upsampled_grid = F.interpolate(
        point_positions, size=(img_size[1], img_size[0]), mode="bilinear", align_corners=True
    )
    preprocessed_img = F.grid_sample(
        warped_img, upsampled_grid.transpose(1, 2).transpose(2, 3), align_corners=True
    )
    return preprocessed_img


def tensor_to_cv2image_mask(tensor, remove_padding=True):
    image = tensor.numpy()
    image = image.transpose((1, 2, 0))
    image = image * 255
    if remove_padding:
        image_ = np.sum(image, -1)
        image_h = np.sum(image_, 1)
        if 0 in image_h:
            h_border = np.min(np.where(image_h == 0)[0])
        else:
            h_border = image.shape[0]
        image_w = np.sum(image_, 0)
        if 0 in image_w:
            w_border = np.min(np.where(image_w == 0)[0])
        else:
            w_border = image.shape[1]
        image = image[:h_border, :w_border]
    image = image.astype(np.uint8)
    return image


from monkeyocr.infrastructure.modeling.preprocessing.models import build_model1, build_model2


def _default_preprocessor_args(device: str):
    return SimpleNamespace(
        resume="",
        print_freq=5,
        save_interval=2,
        lr=1e-4,
        lr_encoder_ratio=0.2,
        batch_size=1,
        weight_decay=1e-4,
        epochs=250,
        warmup_min_lr=0.0001,
        min_lr=0.000001,
        warmup_epochs=10,
        milestones=[80],
        segmim_finetune=False,
        eval=True,
        output_dir="",
        device=device,
        seed=42,
        clip_max_norm=0,
        layer_decay=0.75,
        pretrained_model="",
        load_pretrain_ignore_encoder=False,
        pretrained_encoder="",
        pretrained_decoder="",
        pretrained_vgg16="",
        encoder="swinv2",
        decoder="swinv2",
        swin_dec_depths=[2, 6, 2, 2, 2],
        swin_dec_num_heads=[24, 12, 6, 3, 2],
        swin_dec_window_size=16,
        swin_dec_drop_path_rate=0.2,
        swin_dec_pretrained_ws=8,
        swin_enc_depths=[2, 2, 6, 2],
        swin_enc_num_heads=[3, 6, 12, 24],
        swin_enc_drop_path_rate=0.2,
        swin_enc_embed_dim=96,
        swin_enc_pretrained_ws=8,
        swin_enc_window_size=16,
        pred_mask=True,
        intermediate_erase=True,
        swin_use_checkpoint=False,
        swin_enc_use_checkpoint=False,
        swin_dec_use_checkpoint=False,
        distributed=False,
        gpu=0,
    )


def _load_state_dict(model, checkpoint_path: str):
    checkpoint = torch.load(checkpoint_path, map_location="cpu", weights_only=False)
    if isinstance(checkpoint, dict) and "model" in checkpoint:
        checkpoint = checkpoint["model"]
    model.load_state_dict(checkpoint, strict=False)


class Preprocessor:
    def __init__(self, model_path: str, device: str | None = None, batch_size: int = 16):
        self.model_path = Path(model_path)
        self.device = torch.device(device or ("cuda" if torch.cuda.is_available() else "cpu"))
        self.batch_size = max(1, int(batch_size))
        args = _default_preprocessor_args(str(self.device))

        self.model1 = build_model1(args)
        self.model2 = build_model2(args)

        _load_state_dict(self.model1, str(self.model_path / "preprocessor1.pth"))
        _load_state_dict(self.model2, str(self.model_path / "preprocessor2.pth"))

        self.model1.eval()
        self.model2.eval()

    @torch.no_grad()
    def preprocess_image(self, image: Image.Image):
        return self.preprocess_images([image])[0]

    @torch.no_grad()
    def preprocess_images(self, images: list[Image.Image], batch_size: int | None = None):
        if not images:
            return []

        batch_size = max(1, int(batch_size or self.batch_size))
        preprocessed_images = []
        img_size = [512, 512]

        for start in range(0, len(images), batch_size):
            batch_images = images[start : start + batch_size]
            img_arrays = [
                np.asarray(image.convert("RGB")).astype(np.float32) / 255.0
                for image in batch_images
            ]
            inp_batch = torch.stack(
                [
                    torch.from_numpy(cv2.resize(img, img_size).transpose(2, 0, 1))
                    for img in img_arrays
                ],
                dim=0,
            ).to(self.device)

            pred_mask_batch = self.model1(inp_batch)
            pred_mask_01_batch = (pred_mask_batch > 0.8).float()

            largest_masks = []
            for mask_tensor in pred_mask_01_batch:
                mask = mask_tensor.squeeze().cpu().numpy().astype(np.uint8)
                num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(
                    mask, connectivity=8
                )
                if num_labels > 1:
                    largest_label = 1 + np.argmax(stats[1:, cv2.CC_STAT_AREA])
                    largest_mask = (labels == largest_label).astype(np.uint8)
                else:
                    largest_mask = mask
                largest_masks.append(torch.from_numpy(largest_mask).float().unsqueeze(0))

            largest_mask_batch = torch.stack(largest_masks, dim=0).to(self.device)
            outputs_batch, _ = self.model2(inp_batch * largest_mask_batch)

            for img, point_positions in zip(img_arrays, outputs_batch):
                size = img.shape[:2][::-1]
                preprocessed = bilinear_preprocessing(
                    warped_img=torch.from_numpy(img.transpose(2, 0, 1))
                    .unsqueeze(0)
                    .to(self.device),
                    point_positions=point_positions.unsqueeze(0),
                    img_size=tuple(size),
                )
                preprocessed = (
                    preprocessed[0].detach().cpu().numpy().transpose(1, 2, 0) * 255
                ).astype(np.uint8)
                preprocessed_images.append(Image.fromarray(preprocessed).convert("RGB"))

            del inp_batch, pred_mask_batch, pred_mask_01_batch, largest_mask_batch, outputs_batch
            if self.device.type == "cuda":
                torch.cuda.empty_cache()

        return preprocessed_images

    def preprocess_doc(self, doc: dict):
        return {**doc, "images": self.preprocess_images(doc["images"])}

    def preprocess_docs(self, docs: list[dict], batch_size: int | None = None):
        if not docs:
            return []

        all_images = []
        doc_image_counts = []
        for doc in docs:
            images = doc["images"]
            doc_image_counts.append(len(images))
            all_images.extend(images)

        all_preprocessed = self.preprocess_images(all_images, batch_size=batch_size)

        preprocessed_docs = []
        offset = 0
        for doc, count in zip(docs, doc_image_counts):
            preprocessed_docs.append({**doc, "images": all_preprocessed[offset : offset + count]})
            offset += count

        return preprocessed_docs
