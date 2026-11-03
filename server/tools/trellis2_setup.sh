#!/usr/bin/env bash
# Prepara TRELLIS.2 (Microsoft, MIT) su un PC Linux con GPU NVIDIA da 24 GB.
# Uso:  bash server/tools/trellis2_setup.sh [cartella_trellis]   (default: ~/TRELLIS.2)
# NON è stato eseguito in questo repository: l'ambiente di sviluppo non ha GPU.
set -euo pipefail
DIR="${1:-$HOME/TRELLIS.2}"
HERE="$(cd "$(dirname "$0")" && pwd)"

command -v nvidia-smi >/dev/null || { echo "ERRORE: nvidia-smi non trovato. Serve una GPU NVIDIA con driver installato."; exit 1; }
command -v git >/dev/null || { echo "ERRORE: git mancante"; exit 1; }
command -v conda >/dev/null || { echo "ERRORE: conda mancante. Installa Miniconda o Miniforge e riprova."; exit 1; }

MEM=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader,nounits | head -1)
echo "GPU: $(nvidia-smi --query-gpu=name --format=csv,noheader | head -1), memoria ${MEM} MiB"
if [ "$MEM" -lt 23000 ]; then
  echo "ATTENZIONE: TRELLIS.2 dichiara almeno 24 GB di VRAM. Con meno memoria il caricamento probabilmente fallirà."
fi

[ -d "$DIR/.git" ] || git clone --depth 1 https://github.com/microsoft/TRELLIS.2 "$DIR"
cd "$DIR"

# setup.sh ufficiale: crea l'env 'trellis2' (Python 3.10, PyTorch 2.6.0 + CUDA 12.4) e compila le dipendenze CUDA.
# Richiede CUDA Toolkit 12.4 per compilare flash-attn e le estensioni; la compilazione può richiedere ~30-60 minuti.
eval "$(conda shell.bash hook)"
source ./setup.sh --new-env --basic --flash-attn --nvdiffrast --nvdiffrec --cumesh --o-voxel --flexgemm

conda activate trellis2
cp "$HERE/trellis2_worker.py" "$DIR/model3d_worker.py"
python -c "import trellis2, o_voxel; print('Import OK')"

cat <<MSG

Installazione completata.
Il primo avvio scarica i pesi microsoft/TRELLIS.2-4B da Hugging Face (serve rete e spazio disco).

Nel file .env dell'app (nella cartella model-3d) imposta:
  TRELLIS2_DIR=$DIR
  MODEL3D_LOCAL_CMD=bash -lc 'eval "\$(conda shell.bash hook)" && conda activate trellis2 && python $DIR/model3d_worker.py --image {image} --out {out}'

Test manuale (da fare prima dell'app):
  conda activate trellis2 && python $DIR/model3d_worker.py --image $DIR/assets/example_image/T.png --out /tmp/t.glb
MSG
