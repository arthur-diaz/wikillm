from llama_cpp import Llama

# ─── Configuration ───────────────────────────────────────────────────────────

MODEL_PATH = "models/Meta-Llama-3.1-8B-Instruct-Q4_K_M.gguf"

# Taille de la fenêtre de contexte (en tokens).
# Plus c'est grand, plus ça consomme de VRAM.
N_CTX = 4096

# Nombre de couches à offloader sur le GPU.
# -1 = toutes les couches (recommandé si assez de VRAM)
#  0 = CPU uniquement
N_GPU_LAYERS = -1

# ─────────────────────────────────────────────────────────────────────────────

llm = Llama(
    model_path=MODEL_PATH,
    n_ctx=N_CTX,
    n_gpu_layers=N_GPU_LAYERS,
    verbose=True,
)

prompt = "Explique-moi ce qu'est un modèle de langage en 3 phrases."

print(f"\nPrompt : {prompt}\n")
print("Réponse :\n")

response = llm.create_chat_completion(
    messages=[
        {"role": "system", "content": "Tu es un assistant utile et concis."},
        {"role": "user", "content": prompt},
    ],
    max_tokens=512,
    temperature=0.7,
    stream=True,
)

for chunk in response:
    delta = chunk["choices"][0]["delta"]
    if "content" in delta:
        print(delta["content"], end="", flush=True)

print()
