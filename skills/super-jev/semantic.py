"""Optional meaning search: extra candidate files for a question whose words are not the note's words.

Offline and optional. A small sentence-embedding model (all-MiniLM-L6-v2, Apache-2.0, ONNX) runs on the CPU through
onnxruntime. At refresh time the updater embeds each reviewed file once per sha (update()); an ask embeds only the
question and ranks the stored vectors (search()). The result only ADDS candidate paths: the caller keeps every word hit
in its own order and puts a few meaning hits behind them. No note text leaves the machine; nothing here is a judge.

Absent pieces (no onnxruntime / tokenizers / numpy, no model files, no stored vectors) mean no meaning hits and no error.
Model folder: $SUPERJEV_EMBED_MODEL, else ~/.local/share/super-jev/models/all-MiniLM-L6-v2 holding tokenizer.json and
onnx/model_quantized.onnx (or onnx/model.onnx). fetch_model() downloads them on request (python3 semantic.py --fetch).
"""
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

STORE_FILE = "semantic.npz"
VERSION = 1            # bump when the chunking or the model changes: stored vectors are then rebuilt
CHUNK_WORDS = 160      # words per embedded chunk (the model reads about 256 tokens)
MAX_CHUNKS = 3         # chunks per file: the first ones, where a note states what it is about
MAX_TOKENS = 256
MODEL_FILES = ("tokenizer.json", "onnx/model_quantized.onnx")
MODEL_URL = "https://huggingface.co/Xenova/all-MiniLM-L6-v2/resolve/main/"
_NOISE = re.compile(r"[#*|>`_]+")
_state = {}


def model_dir() -> Path:
    return Path(os.environ.get("SUPERJEV_EMBED_MODEL") or Path.home() / ".local/share/super-jev/models/all-MiniLM-L6-v2")


def _model_file() -> Path:
    d = model_dir() / "onnx"
    for name in (os.environ.get("SUPERJEV_EMBED_ONNX"), "model_quantized.onnx", "model.onnx"):
        if name and (d / name).is_file():
            return d / name
    return d / "model_quantized.onnx"


def _load():
    """(tokenizer, session) once per process, or None when anything is missing."""
    if "m" not in _state:
        _state["m"] = None
        try:
            import numpy  # noqa: F401
            import onnxruntime as ort
            from tokenizers import Tokenizer
            tok_path, onnx_path = model_dir() / "tokenizer.json", _model_file()
            if tok_path.is_file() and onnx_path.is_file():
                tk = Tokenizer.from_file(str(tok_path))
                tk.enable_truncation(MAX_TOKENS)
                tk.enable_padding()
                so = ort.SessionOptions()
                so.intra_op_num_threads = int(os.environ.get("SUPERJEV_EMBED_THREADS", "2"))
                so.log_severity_level = 3
                _state["m"] = (tk, ort.InferenceSession(str(onnx_path), so, providers=["CPUExecutionProvider"]))
        except Exception:  # noqa: BLE001 -- optional: any failure to load is "no meaning search"
            _state["m"] = None
    return _state["m"]


def ready() -> bool:
    return _load() is not None


def embed(texts, batch: int = 32):
    """L2-normalised float32 vectors, one row per text."""
    import numpy as np
    tk, sess = _load()
    out = []
    for i in range(0, len(texts), batch):
        enc = tk.encode_batch(texts[i:i + batch])
        ids = np.array([e.ids for e in enc], dtype=np.int64)
        mask = np.array([e.attention_mask for e in enc], dtype=np.int64)
        h = sess.run(None, {"input_ids": ids, "attention_mask": mask, "token_type_ids": np.zeros_like(ids)})[0]
        m = mask[..., None].astype(np.float32)
        v = (h * m).sum(1) / np.maximum(m.sum(1), 1e-9)
        out.append(v / np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-9))
    return np.concatenate(out) if out else np.zeros((0, 384), dtype=np.float32)


def chunks_of(path: str, text: str) -> list:
    """The texts to embed for one file: its folder and name words with the first words of the note, then the next
    chunks. The same rule for every file."""
    name = re.sub(r"[-_/.]+", " ", os.path.join(os.path.basename(os.path.dirname(path)), os.path.splitext(os.path.basename(path))[0]))
    w = _NOISE.sub(" ", text[:6000]).split()
    out = []
    for c in range(MAX_CHUNKS):
        seg = w[c * CHUNK_WORDS:(c + 1) * CHUNK_WORDS]
        if not seg and c:
            break
        out.append((name + ". " if c == 0 else "") + " ".join(seg))
    return out


def store_path(sdir) -> Path:
    base = os.environ.get("SUPERJEV_SEMANTIC_DIR")  # tests and offline experiments: <dir>/<principal>/semantic.npz
    return (Path(base) / Path(sdir).name if base else Path(sdir)) / STORE_FILE


def _load_store(p: Path):
    import numpy as np
    try:
        z = np.load(p, allow_pickle=False)
        meta = json.loads(str(z["meta"]))
        if meta.get("version") != VERSION or meta.get("model") != _model_file().name:
            return None
        return {"paths": meta["paths"], "shas": meta["shas"], "owner": z["owner"], "vecs": z["vecs"]}
    except Exception:  # noqa: BLE001 -- no store, or a damaged one: rebuilt by the next update
        return None


def update(sdir, entries, read_text, deadline: float = 0.0) -> dict:
    """Embed what is new or changed. entries: [(path, sha)] of the files that should be searchable by meaning (reviewed,
    unchanged, not secret-held); read_text(path) -> str or None. Vectors of unchanged (path, sha) pairs are reused; files
    that left the list are dropped. Saved atomically every few hundred files so a stopped pass resumes. deadline: a
    time.time() after which the pass stops and saves what it has (0: no limit). Returns counts."""
    import numpy as np
    if not ready():
        return {"skipped": "no model"}
    p = store_path(sdir)
    old = _load_store(p)
    keep = dict(entries)
    rows = {}  # path -> (sha, vecs)
    if old:
        by_owner = {}
        for k, o in enumerate(old["owner"].tolist()):
            by_owner.setdefault(o, []).append(k)
        for i, (path, sha) in enumerate(zip(old["paths"], old["shas"])):
            if keep.get(path) == sha:
                rows[path] = (sha, old["vecs"][by_owner[i]])
    todo = [(path, sha) for path, sha in entries if path not in rows]
    built, last_save = 0, 0

    def save():
        paths = list(rows)
        owner = np.concatenate([np.full(len(rows[x][1]), i, dtype=np.int32) for i, x in enumerate(paths)]) if paths else np.zeros(0, np.int32)
        vecs = np.concatenate([rows[x][1] for x in paths]).astype(np.float16) if paths else np.zeros((0, 384), np.float16)
        meta = json.dumps({"version": VERSION, "model": _model_file().name, "paths": paths, "shas": [rows[x][0] for x in paths]})
        p.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=str(p.parent), suffix=".tmp")
        os.close(fd)
        with open(tmp, "wb") as fh:
            np.savez(fh, meta=np.array(meta), owner=owner, vecs=vecs)
        os.replace(tmp, p)

    for path, sha in todo:
        if deadline and time.time() > deadline:
            break
        text = read_text(path)
        if not text:
            continue
        rows[path] = (sha, embed(chunks_of(path, text)).astype(np.float16))
        built += 1
        if built - last_save >= 300:
            save()
            last_save = built
    if built or (old and len(rows) != len(old["paths"])) or (not old and rows):
        save()
    return {"built": built, "kept": len(rows) - built, "pending": len(todo) - built}


def search(sdir, question: str, k: int = 8, min_cos: float = 0.0) -> list:
    """[(path, cosine, sha)] best k files by their best chunk, or [] (no model, no store, nothing above min_cos)."""
    import numpy as np
    p = store_path(sdir)
    if not p.is_file() or not ready():
        return []
    st = _load_store(p)
    if not st or not len(st["paths"]):
        return []
    q = embed([question])[0]
    s = st["vecs"].astype(np.float32) @ q
    best = np.full(len(st["paths"]), -1.0, dtype=np.float32)
    np.maximum.at(best, st["owner"], s)
    order = np.argsort(-best)[:k]
    return [(st["paths"][i], float(best[i]), st["shas"][i]) for i in order if best[i] >= min_cos]


def fetch_model() -> int:
    import urllib.request
    d = model_dir()
    for name in MODEL_FILES:
        dest = d / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(MODEL_URL + name, timeout=120) as r, open(str(dest) + ".part", "wb") as fh:
            fh.write(r.read())
        os.replace(str(dest) + ".part", dest)
        print(f"fetched {dest}")
    return 0


if __name__ == "__main__":
    sys.exit(fetch_model() if "--fetch" in sys.argv else 2)
