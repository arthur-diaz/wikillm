from huggingface_hub import hf_hub_download
import os

# ─── Configuration ───────────────────────────────────────────────────────────
# Modifiez ces trois variables pour télécharger un autre modèle.

REPO_ID = "bartowski/Meta-Llama-3.1-8B-Instruct-GGUF"
FILENAME = "Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf"
DEST_DIR = "models"

# ─────────────────────────────────────────────────────────────────────────────

os.makedirs(DEST_DIR, exist_ok=True)

print(f"Téléchargement de '{FILENAME}' depuis '{REPO_ID}'...")
print(f"Destination : {DEST_DIR}/\n")

path = hf_hub_download(
    repo_id=REPO_ID,
    filename=FILENAME,
    local_dir=DEST_DIR,
)

print(f"\n✓ Modèle téléchargé : {path}")
