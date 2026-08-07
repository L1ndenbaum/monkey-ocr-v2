import torch
import torch.nn as nn
from monkeyocr.infrastructure.modeling.preprocessing.models.seg import U2NETP


class Net(nn.Module):
    def __init__(self):
        super(Net, self).__init__()
        self.msk = U2NETP(3, 1)

    def forward(self, x):
        msk, _1, _2, _3, _4, _5, _6 = self.msk(x)
        return msk


def reload_seg_model(model, path=""):
    if not bool(path):
        return model
    else:
        model_dict = model.state_dict()
        # pretrained_dict = torch.load(path, map_location='cuda:0')
        pretrained_dict = torch.load(path, map_location="cpu")
        print(len(pretrained_dict.keys()))
        pretrained_dict = {k[6:]: v for k, v in pretrained_dict.items() if k[6:] in model_dict}
        print(len(pretrained_dict.keys()))
        model_dict.update(pretrained_dict)
        model.load_state_dict(model_dict)

        return model


def build(args):
    model = Net()
    device = torch.device(args.device)
    model = model.to(device)

    if args.distributed:
        model = torch.nn.parallel.DistributedDataParallel(
            model,
            device_ids=[args.gpu],
            # find_unused_parameters=True
        )

    return model
