"""Models the workflow needs, which file ComfyUI should use for each role, and an optional downloader.

Downloading only happens when the user asks for it from the web page, and only when ComfyUI's models
directory is mounted into this container. Otherwise the page shows the links and target folders.
"""
import os
import threading

import requests

MODELS = {
    "diffusion_model": {
        "folder": "diffusion_models",
        "file": "flux-2-klein-9b-fp8.safetensors",
        "url": "https://huggingface.co/black-forest-labs/FLUX.2-klein-9b-fp8/resolve/main/flux-2-klein-9b-fp8.safetensors",
        "page": "https://huggingface.co/black-forest-labs/FLUX.2-klein-9b-fp8",
        "size": "9.4 GB",
        "gated": True,  # FLUX Non-Commercial License: accept it on the page, then use a Hugging Face read token
    },
    "text_encoder": {
        "folder": "text_encoders",
        "file": "qwen_3_8b_fp8mixed.safetensors",
        "url": "https://huggingface.co/Comfy-Org/vae-text-encorder-for-flux-klein-9b/resolve/main/split_files/text_encoders/qwen_3_8b_fp8mixed.safetensors",
        "page": "https://huggingface.co/Comfy-Org/vae-text-encorder-for-flux-klein-9b",
        "size": "8.7 GB",
        "gated": False,
    },
    "vae": {
        "folder": "vae",
        "file": "flux2-vae.safetensors",
        "url": "https://huggingface.co/Comfy-Org/vae-text-encorder-for-flux-klein-9b/resolve/main/split_files/vae/flux2-vae.safetensors",
        "page": "https://huggingface.co/Comfy-Org/vae-text-encorder-for-flux-klein-9b",
        "size": "336 MB",
        "gated": False,
    },
}


def pick(role, available, saved):
    """Choice for a role: the user's saved choice, else the official file, else nothing."""
    if saved in available:
        return saved
    return MODELS[role]["file"] if MODELS[role]["file"] in available else None


def check_access(token):
    """Can this token download the gated model? -> "" if yes, else an error code shown by the page."""
    if not token:
        return "no_token"
    spec = next(m for m in MODELS.values() if m["gated"])
    r = requests.head(spec["url"], headers={"Authorization": f"Bearer {token}"}, allow_redirects=False, timeout=30)
    if r.status_code == 401:
        return "bad_token"
    if r.status_code == 403:
        return "license"
    return "" if r.status_code < 400 else f"HTTP {r.status_code}"


class Downloader:
    def __init__(self, models_dir):
        self.models_dir = models_dir if models_dir and os.path.isdir(models_dir) else None
        # mounted but not writable: usually created by Docker as root, or the container runs as another user
        self.read_only = bool(self.models_dir) and not os.access(self.models_dir, os.W_OK)
        self.state = {role: {"status": "idle", "done": 0, "total": 0, "error": ""} for role in MODELS}
        self.lock = threading.Lock()

    def start(self, roles, token):
        """Download the given roles one after another in a background thread. The token stays in memory."""
        with self.lock:
            roles = [r for r in roles if r in MODELS and self.state[r]["status"] not in ("queued", "downloading")]
            for r in roles:
                self.state[r] = {"status": "queued", "done": 0, "total": 0, "error": ""}
        threading.Thread(target=self._run, args=(roles, token), daemon=True).start()

    def _run(self, roles, token):
        for role in roles:
            st = self.state[role]
            try:
                self._fetch(MODELS[role], st, token)
                st["status"] = "done"
            except Exception as e:  # shown to the user on the page; the download can be resumed
                st["status"], st["error"] = "error", str(e)

    def _fetch(self, spec, st, token):
        dest = os.path.join(self.models_dir, spec["folder"], spec["file"])
        part = dest + ".part"
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        have = os.path.getsize(part) if os.path.exists(part) else 0
        headers = {"Range": f"bytes={have}-"} if have else {}
        if spec["gated"]:
            # requests drops this header when Hugging Face redirects to its CDN, so it never leaves huggingface.co
            headers["Authorization"] = f"Bearer {token}"
        st["status"] = "downloading"
        with requests.get(spec["url"], headers=headers, stream=True, timeout=60) as r:
            if r.status_code in (401, 403):
                raise PermissionError("license")
            r.raise_for_status()
            if r.status_code != 206:
                have = 0
            st["done"], st["total"] = have, have + int(r.headers.get("content-length", 0))
            with open(part, "ab" if have else "wb") as f:
                for chunk in r.iter_content(1 << 20):
                    f.write(chunk)
                    st["done"] += len(chunk)
        if os.path.getsize(part) != st["total"]:
            raise IOError("interrupted")
        os.replace(part, dest)
