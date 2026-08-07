from .viteraser import build as build_viteraser_model
from .build_seg import build as build_seg_model


def build_model2(args):
    return build_viteraser_model(args)


def build_model1(args):
    return build_seg_model(args)
