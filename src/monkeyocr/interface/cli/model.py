"""Download MonkeyOCR model weights from a supported registry."""

import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--type", "-t", choices=["huggingface", "modelscope"], default="huggingface"
    )
    parser.add_argument("--name", "-n", default="MonkeyOCRv2-B-Parsing")
    parser.add_argument("--output-root", type=Path, default=Path.cwd() / "model_weight")
    args = parser.parse_args()

    model_dir = args.output_root.expanduser().resolve() / args.name
    model_dir.mkdir(parents=True, exist_ok=True)
    if args.type == "huggingface":
        from huggingface_hub import snapshot_download

        snapshot_download(repo_id=f"zenosai/{args.name}", local_dir=model_dir)
    else:
        from modelscope import snapshot_download

        snapshot_download(repo_id=f"zenosai/{args.name}", local_dir=model_dir)


if __name__ == "__main__":
    main()
