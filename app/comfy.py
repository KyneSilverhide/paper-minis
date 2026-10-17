"""Minimal ComfyUI HTTP client. Everything goes through the API, so ComfyUI can run on the host,
in another container or on another machine."""
import time
import uuid

import requests


class ComfyError(RuntimeError):
    pass


class Comfy:
    def __init__(self, url):
        self.url = url.rstrip("/")
        self.client_id = uuid.uuid4().hex

    def _get(self, path, timeout=30, **params):
        r = requests.get(self.url + path, params=params, timeout=timeout)
        r.raise_for_status()
        return r

    def system_stats(self):
        return self._get("/system_stats", timeout=5).json()

    def object_info(self):
        return self._get("/object_info").json()

    def folder_paths(self):
        """Where ComfyUI keeps each model folder, on its own machine. Internal ComfyUI route: {} if unavailable."""
        try:
            return self._get("/internal/folder_paths", timeout=5).json()
        except (requests.RequestException, ValueError):
            return {}

    def models(self, folder):
        return self._get(f"/models/{folder}").json()

    def upload(self, png, name):
        """Upload a source image to ComfyUI's input/paper-minis/ -> the LoadImage value."""
        r = requests.post(self.url + "/upload/image", files={"image": (name, png, "image/png")},
                          data={"subfolder": "paper-minis", "type": "input", "overwrite": "true"}, timeout=60)
        r.raise_for_status()
        d = r.json()
        return f"{d['subfolder']}/{d['name']}" if d.get("subfolder") else d["name"]

    def run(self, prompt, timeout=1800):
        """Queue a prompt and wait for its single output image -> PNG bytes."""
        r = requests.post(self.url + "/prompt", json={"prompt": prompt, "client_id": self.client_id}, timeout=30)
        if r.status_code == 400:
            raise ComfyError(f"ComfyUI rejected the workflow: {r.text}")
        r.raise_for_status()
        pid = r.json()["prompt_id"]
        end = time.monotonic() + timeout  # the first run also loads ~18 GB of models
        while not (h := self._get(f"/history/{pid}").json()):
            if time.monotonic() > end:
                raise ComfyError("ComfyUI did not finish in time")
            time.sleep(1)
        h = h[pid]
        if h["status"].get("status_str") == "error":
            msg = [m[1].get("exception_message", "") for m in h["status"].get("messages", []) if m[0] == "execution_error"]
            raise ComfyError("ComfyUI error: " + " ".join(msg).strip())
        img = next(i for o in h["outputs"].values() for i in o.get("images", []))
        return self._get("/view", filename=img["filename"], subfolder=img["subfolder"], type=img["type"]).content
