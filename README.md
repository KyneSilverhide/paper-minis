# paper-minis

Turn character illustrations into **fold-over paper miniatures** for tabletop games, using your own
[ComfyUI](https://github.com/Comfy-Org/ComfyUI) and the FLUX.2 [klein] 9B image model.

Upload character images, each with an optional description. paper-minis draws a clean full-body figure of each one, then gives you print-ready sheets:

- **PDF / PNG sheets** at true size. Each figure gets a black border, a black base, and a mirrored back side joined at the base.
- A **laser-cut SVG** matching each sheet, with a registration frame, the cut lines and the fold lines.

The web page walks you through everything, in English or French: connecting ComfyUI, installing the models, then
generating, printing, cutting and assembling.

## Quick start

1. Install [Docker](https://docs.docker.com/get-docker/).
2. Get paper-minis and start it:
   ```bash
   git clone https://github.com/KyneSilverhide/paper-minis.git
   cd paper-minis
   docker compose up -d --build
   ```
3. Open <http://localhost:8080> and follow the steps on the page.

You also need ComfyUI (free) running on a computer with a capable graphics card: about 24 GB of video memory is comfortable,
12–16 GB works more slowly. The page explains how to install ComfyUI, connect it and get the models.

## How it works

```
browser ──► paper-minis (Docker, port 8080) ──HTTP──► your ComfyUI ──► your GPU
```

The container only holds the web page and the image post-processing, so it needs no GPU and doesn't care which GPU ComfyUI runs on.
Image generation runs in your own ComfyUI, using core nodes only: there is nothing to install in ComfyUI.

## Settings

All optional, in `docker-compose.yml` or as environment variables:

| Setting | Default | |
|---|---|---|
| `PORT` | `8080` | Web page port, e.g. `PORT=8090 docker compose up -d`. |
| `COMFYUI_URL` | `http://host.docker.internal:8188` | Initial ComfyUI address. You can change it from the page. |
| `COMFYUI_MODELS_DIR` | unset | Set to `/models` and mount your `ComfyUI/models` folder there to download models from the page. |
| `PUID` / `PGID` | `1000` | User the app runs as (`id -u`, `id -g`), so downloaded models belong to you. |
| `MAX_UPLOAD_MB` | `25` | Largest accepted upload. |

Figures and sheets are kept in the `paper-minis-data` Docker volume. To update: `git pull`, then `docker compose up -d --build`.

The app has no login: anyone who can reach its port can use it. Keep it on your own computer or home network.

## Licenses

- paper-minis: MIT (see `LICENSE`).
- The FLUX.2 [klein] 9B model is free for non-commercial use under the
  [FLUX Non-Commercial License](https://huggingface.co/black-forest-labs/FLUX.2-klein-9b-fp8). Each user accepts it on Hugging Face before downloading.
  paper-minis does not distribute any model weights.

## En français

paper-minis transforme des illustrations de personnages en **figurines en papier pliables** pour le jeu de rôle,
avec ton propre ComfyUI. Installe Docker, lance `docker compose up -d --build` dans ce dossier, ouvre <http://localhost:8080> :
la page, disponible en français, te guide pas à pas. Elle explique comment installer et connecter ComfyUI, récupérer les modèles, puis générer, imprimer, découper et assembler.
