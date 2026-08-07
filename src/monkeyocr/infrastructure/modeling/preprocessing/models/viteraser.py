import torch
import torch.nn as nn
import torch.nn.functional as F

from .decoder import build_decoder
from .encoder import build_encoder


class SegHead(nn.Module):
    def __init__(self, in_channel, encoder_stride):
        super(SegHead, self).__init__()
        self.decoder = nn.Sequential(
            nn.Conv2d(in_channels=in_channel, out_channels=encoder_stride**2, kernel_size=1),
            nn.PixelShuffle(encoder_stride),
        )

    def forward(self, feature):
        seg_res = self.decoder(feature)
        return seg_res


class pointHead(nn.Module):
    def __init__(self, in_channel, encoder_stride):
        super(pointHead, self).__init__()
        self.decoder = nn.Sequential(
            nn.Conv2d(in_channels=in_channel, out_channels=2 * (encoder_stride**2), kernel_size=1),
            nn.PixelShuffle(encoder_stride),
        )

    def forward(self, feature):
        seg_res = self.decoder(feature)
        return seg_res


class ViTEraser(nn.Module):
    # def __init__(self, encoder, decoder, vgg16):
    def __init__(self, encoder, decoder):
        super(ViTEraser, self).__init__()
        self.encoder = encoder
        self.decoder = decoder
        self.pixel_embed = nn.Linear(encoder.num_channels, decoder.embed_dim)

        # tiny
        self.seg_mask = SegHead(in_channel=96 * 8, encoder_stride=32)
        self.seg_point_2 = pointHead(in_channel=96 * 8, encoder_stride=2)

        # base
        # self.seg_mask = SegHead(in_channel=1024,  encoder_stride=32)
        # self.seg_point_2 = pointHead(in_channel=1024,  encoder_stride=2)

    def forward(self, images):
        enc_ms_feats = self.encoder(images)

        pred_mask_coarse = self.seg_mask(enc_ms_feats[-1])
        pred_point_coarse = self.seg_point_2(enc_ms_feats[-1])

        enc_feat = self.pixel_embed(enc_ms_feats[-1].permute(0, 2, 3, 1)).permute(0, 3, 1, 2)

        outputs, pred_mask = self.decoder(enc_feat, enc_ms_feats)

        if not self.training:
            return outputs[-1], pred_mask

        return {
            "outputs": outputs,
            "pred_point_coarse": pred_point_coarse,
            "pred_mask_coarse": pred_mask_coarse,
            "pred_mask": pred_mask,
        }


class MLP(nn.Module):
    """Very simple multi-layer perceptron (also called FFN)"""

    def __init__(self, input_dim, hidden_dim, output_dim, num_layers):
        super().__init__()
        self.num_layers = num_layers
        h = [hidden_dim] * (num_layers - 1)
        self.layers = nn.ModuleList(
            nn.Linear(n, k) for n, k in zip([input_dim] + h, h + [output_dim])
        )

    def forward(self, x):
        for i, layer in enumerate(self.layers):
            x = F.relu(layer(x)) if i < self.num_layers - 1 else layer(x)
        return x


def load_pretrained_model(model, weight_path, ignore_encoder=False):
    weight = torch.load(weight_path, map_location="cpu")["model"]
    model_dict = model.state_dict()

    loaded_keys = []
    ignore_keys = []
    for k, v in weight.items():
        if "relative_coords_table" in k or "relative_position_index" in k:
            ignore_keys.append(k)
            continue

        if ignore_encoder and k.startswith("encoder."):
            ignore_keys.append(k)
            continue

        if k in model_dict.keys():
            model_dict[k] = v
            loaded_keys.append(k)
        else:
            ignore_keys.append(k)

    model.load_state_dict(model_dict)
    print(f"Load Model from {weight_path}")
    print("Loaded keys:", loaded_keys)
    print("Ignored keys:", ignore_keys)
    return model


def build(args):
    encoder = build_encoder(args)
    decoder = build_decoder(args)

    model = ViTEraser(encoder=encoder, decoder=decoder)

    if args.pretrained_model:
        model = load_pretrained_model(
            model, args.pretrained_model, args.load_pretrain_ignore_encoder
        )

    device = torch.device(args.device)
    model = model.to(device)

    if args.distributed:
        model = torch.nn.parallel.DistributedDataParallel(
            model,
            device_ids=[args.gpu],
            # find_unused_parameters=True
        )

    return model
