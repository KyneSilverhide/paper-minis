"""ComfyUI workflow (API format): character illustration -> raw paper-mini figure on a white background.

Only core ComfyUI nodes are used. Inputs are filled by name and filtered against /object_info,
so the same graph works across ComfyUI versions that add or drop optional inputs.
"""
import random

PROMPT = """Redraw the character from image 1 as a full-body tabletop paper miniature illustration: bold clean black ink linework, flat cel-shaded colors with simple shading, crisp comic-book fantasy illustration. Exaggerated heroic tabletop-miniature proportions, so the figure reads from across the table: an enlarged head (about one quarter of the body height) with big, bold, clearly readable facial features, large hands, and the character's equipment drawn clearly oversized so its silhouette stands out.

Keep the character faithful to image 1: same skin color, eye color, face, expression, age, hair, clothes, armor, colors and accessories. Every object of image 1 is kept with the same shape, held the same way, with its magic effect if it has one. Draw nothing that image 1 does not show.
If part of the body is not visible in image 1 (legs, feet), complete it with simple clothes and boots matching the outfit.

The whole character is visible, from the top of the head to the soles of the boots, standing upright on both feet, in a three-quarter view turned to the right (body and face rotated about 45 degrees toward the right, halfway between front view and side profile: both eyes visible, the face clearly readable, not facing the viewer straight on and not a flat side profile), centered, filling most of the image height with a small empty margin on every side. Nothing is cut off by the image edges. A thick, uniform black outline surrounds the entire silhouette.

Background: perfectly plain, flat, pure white. No ground, no floor, no cast shadow, no base, no pedestal, no scenery, no text, no checkerboard pattern."""

# Klein barely changes with the seed: a random pose line gives real variety between runs
POSES = (
    "calm, confident stance, weight on one leg",
    "ready stance, knees slightly bent, leaning forward",
    "proud heroic stance, chest out, chin raised",
    "action pose, actively using their main item or ability, arms in motion",
    "wary stance, slightly crouched, glancing to the side",
)

REQUIRED_NODES = ("UNETLoader", "CLIPLoader", "VAELoader", "LoadImage", "CLIPTextEncode", "ConditioningZeroOut",
                  "ImageScaleToTotalPixels", "VAEEncode", "ReferenceLatent", "CFGGuider", "RandomNoise", "KSamplerSelect",
                  "Flux2Scheduler", "EmptyFlux2LatentImage", "SamplerCustomAdvanced", "VAEDecode", "PreviewImage")


def missing_nodes(info):
    """Node classes (or the flux2 text encoder type) this ComfyUI lacks -> update ComfyUI."""
    missing = [n for n in REQUIRED_NODES if n not in info]
    if "CLIPLoader" in info:
        spec = info["CLIPLoader"]["input"]["required"]["type"]
        types = spec[1].get("options", []) if spec[0] == "COMBO" else spec[0]  # newer ComfyUI combo shape
        if "flux2" not in types:
            missing.append("CLIPLoader type flux2")
    return missing


def build(info, models, image, details=""):
    """-> (prompt in API format, chosen pose). `image` is the LoadImage value of the uploaded source."""
    pose = random.choice(POSES)
    text = PROMPT + f"\n\nPose: {pose}."
    if details:
        text += f"\n\nCharacter details from image 1, they must be visible: {details}"
    graph = {
        "1": ("UNETLoader", {"unet_name": models["diffusion_model"], "weight_dtype": "default"}),
        "2": ("CLIPLoader", {"clip_name": models["text_encoder"], "type": "flux2", "device": "default"}),
        "3": ("VAELoader", {"vae_name": models["vae"]}),
        "4": ("CLIPTextEncode", {"text": text, "clip": ["2", 0]}),
        "5": ("ConditioningZeroOut", {"conditioning": ["4", 0]}),
        "10": ("LoadImage", {"image": image}),
        "20": ("ImageScaleToTotalPixels", {"image": ["10", 0], "upscale_method": "lanczos", "megapixels": 0.5, "resolution_steps": 1}),
        "30": ("VAEEncode", {"pixels": ["20", 0], "vae": ["3", 0]}),
        "40": ("ReferenceLatent", {"conditioning": ["4", 0], "latent": ["30", 0]}),
        "50": ("ReferenceLatent", {"conditioning": ["5", 0], "latent": ["30", 0]}),
        "60": ("CFGGuider", {"model": ["1", 0], "positive": ["40", 0], "negative": ["50", 0], "cfg": 1.0}),
        "61": ("RandomNoise", {"noise_seed": random.randrange(2 ** 48)}),
        "62": ("KSamplerSelect", {"sampler_name": "euler"}),
        "63": ("Flux2Scheduler", {"steps": 4, "width": 832, "height": 1216}),
        "64": ("EmptyFlux2LatentImage", {"width": 832, "height": 1216, "batch_size": 1}),
        "65": ("SamplerCustomAdvanced", {"noise": ["61", 0], "guider": ["60", 0], "sampler": ["62", 0], "sigmas": ["63", 0], "latent_image": ["64", 0]}),
        "66": ("VAEDecode", {"samples": ["65", 0], "vae": ["3", 0]}),
        "67": ("PreviewImage", {"images": ["66", 0]}),
    }
    prompt = {}
    for node_id, (cls, inputs) in graph.items():
        declared = {**info[cls]["input"].get("required", {}), **info[cls]["input"].get("optional", {})}
        prompt[node_id] = {"class_type": cls, "inputs": {k: v for k, v in inputs.items() if k in declared}}
    return prompt, pose
