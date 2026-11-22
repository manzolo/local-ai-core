"""The three ComfyUI workflows, in ComfyUI's API format (node id → class and inputs)."""


def generate(prompt, negative, width, height, steps, seed, checkpoint, lora, prefix="lac"):
    """SDXL with the Lightning LoRA: 4 steps, cfg 1, euler with the sgm_uniform schedule."""
    return {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": checkpoint}},
        "2": {"class_type": "LoraLoader", "inputs": {"model": ["1", 0], "clip": ["1", 1], "lora_name": lora,
                                                     "strength_model": 1.0, "strength_clip": 1.0}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["2", 1]}},
        "4": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["2", 1]}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "6": {"class_type": "KSampler", "inputs": {"model": ["2", 0], "positive": ["3", 0], "negative": ["4", 0],
                                                   "latent_image": ["5", 0], "seed": seed, "steps": steps,
                                                   "cfg": 1.0, "sampler_name": "euler",
                                                   "scheduler": "sgm_uniform", "denoise": 1.0}},
        "7": {"class_type": "VAEDecode", "inputs": {"samples": ["6", 0], "vae": ["1", 2]}},
        "8": {"class_type": "SaveImage", "inputs": {"images": ["7", 0], "filename_prefix": prefix}},
    }


def upscale(image, scale, model, prefix="lac-upscale"):
    """RealESRGAN ×4; ×2 is ×4 then halved with lanczos (sharper than a ×2 model)."""
    wf = {
        "1": {"class_type": "LoadImage", "inputs": {"image": image}},
        "2": {"class_type": "UpscaleModelLoader", "inputs": {"model_name": model}},
        "3": {"class_type": "ImageUpscaleWithModel", "inputs": {"upscale_model": ["2", 0], "image": ["1", 0]}},
    }
    last = ["3", 0]
    if scale == 2:
        wf["4"] = {"class_type": "ImageScaleBy", "inputs": {"image": last, "upscale_method": "lanczos",
                                                            "scale_by": 0.5}}
        last = ["4", 0]
    wf["9"] = {"class_type": "SaveImage", "inputs": {"images": last, "filename_prefix": prefix}}
    return wf


def cutout(image, model, prefix="lac-cutout", invert=True):
    """BiRefNet. Its mask is 1 on the subject, while JoinImageWithAlpha takes 1 = what to remove: the mask
    must be inverted, or the subject vanishes and the background stays (the easy mistake; `invert` exists
    only so the self-test can prove it is caught)."""
    wf = {
        "1": {"class_type": "LoadImage", "inputs": {"image": image}},
        "2": {"class_type": "LoadBackgroundRemovalModel", "inputs": {"bg_removal_name": model}},
        "3": {"class_type": "RemoveBackground", "inputs": {"bg_removal_model": ["2", 0], "image": ["1", 0]}},
    }
    alpha = ["3", 0]
    if invert:
        wf["4"] = {"class_type": "InvertMask", "inputs": {"mask": ["3", 0]}}
        alpha = ["4", 0]
    wf["5"] = {"class_type": "JoinImageWithAlpha", "inputs": {"image": ["1", 0], "alpha": alpha}}
    wf["9"] = {"class_type": "SaveImage", "inputs": {"images": ["5", 0], "filename_prefix": prefix}}
    return wf
