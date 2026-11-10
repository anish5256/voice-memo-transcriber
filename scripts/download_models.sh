#!/usr/bin/env bash
# Downloads the small speaker models (~90 MB). Whisper large-v3 (~5.8 GB) is fetched by
# mlx-whisper from Hugging Face on first run.
set -euo pipefail
DIR="${1:-models}"
mkdir -p "$DIR" && cd "$DIR"
R=https://github.com/k2-fsa/sherpa-onnx/releases/download

curl -fL -o seg.tar.bz2 "$R/speaker-segmentation-models/sherpa-onnx-pyannote-segmentation-3-0.tar.bz2"
tar xjf seg.tar.bz2 && rm seg.tar.bz2

for m in 3dspeaker_speech_eres2net_base_sv_zh-cn_3dspeaker_16k.onnx nemo_en_titanet_small.onnx; do
  [ -s "$m" ] || curl -fL -o "$m" "$R/speaker-recongition-models/$m"   # "recongition" is upstream's spelling
done
ls -la
