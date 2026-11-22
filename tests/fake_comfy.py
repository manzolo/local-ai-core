"""A fake ComfyUI for the CPU tests: an httpx transport that runs the workflows with Pillow, following the
real nodes' rules. In particular JoinImageWithAlpha makes transparent what its mask marks, so a cut-out
whose mask is not inverted loses its subject here exactly as it would on the real thing."""
import io
import json
import random
import re

import httpx
from PIL import Image


class FakeComfy:
    def __init__(self, checkpoints=("sd_xl_base_1.0.safetensors",)):
        self.checkpoints = set(checkpoints)
        self.uploads, self.outputs, self.history = {}, {}, {}
        self.prompts = 0
        self.frees = 0
        self.transport = httpx.MockTransport(self.handle)

    def handle(self, request):
        path = request.url.path
        if path == "/upload/image":
            body = request.content
            name = re.search(rb'filename="([^"]+)"', body).group(1).decode()
            start = body.index(b"\r\n\r\n", body.index(b'name="image"')) + 4
            end = body.index(b"\r\n--", start)
            self.uploads[name] = body[start:end]
            return httpx.Response(200, json={"name": name, "subfolder": "", "type": "input"})
        if path == "/prompt":
            wf = json.loads(request.content)["prompt"]
            for nid, node in wf.items():
                ckpt = node["inputs"].get("ckpt_name")
                if node["class_type"] == "CheckpointLoaderSimple" and ckpt not in self.checkpoints:
                    return httpx.Response(400, json={
                        "error": {"message": "Prompt outputs failed validation"},
                        "node_errors": {nid: {"class_type": "CheckpointLoaderSimple", "errors": [
                            {"message": "Value not in list",
                             "details": f"ckpt_name: '{node['inputs']['ckpt_name']}' not in list"}]}}})
            self.prompts += 1
            pid = f"p{self.prompts}"
            img = self.run(wf)
            fname = f"out_{pid}.png"
            buf = io.BytesIO()
            img.save(buf, "PNG")
            self.outputs[fname] = buf.getvalue()
            self.history[pid] = {"status": {"status_str": "success", "completed": True},
                                 "outputs": {"9": {"images": [{"filename": fname, "subfolder": "", "type": "output"}]}}}
            return httpx.Response(200, json={"prompt_id": pid})
        if path == "/free":
            self.frees += 1
            return httpx.Response(200)
        if path.startswith("/history/"):
            pid = path.rsplit("/", 1)[1]
            return httpx.Response(200, json={pid: self.history[pid]} if pid in self.history else {})
        if path == "/view":
            return httpx.Response(200, content=self.outputs[request.url.params["filename"]])
        return httpx.Response(404)

    def run(self, wf):
        memo = {}

        def val(ref):
            nid, _ = ref
            if nid not in memo:
                memo[nid] = node(wf[nid])
            return memo[nid]

        def node(n):
            c, i = n["class_type"], n["inputs"]
            if c == "EmptyLatentImage":
                rnd = random.Random(0)
                img = Image.new("RGB", (i["width"], i["height"]))
                img.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
                             for _ in range(i["width"] * i["height"])])
                return img
            if c in ("KSampler", "VAEDecode"):
                return val(i.get("latent_image") or i["samples"])
            if c == "SaveImage":
                return val(i["images"])
            if c == "LoadImage":
                return Image.open(io.BytesIO(self.uploads[i["image"]])).convert("RGB")
            if c == "ImageUpscaleWithModel":
                img = val(i["image"])
                return img.resize((img.width * 4, img.height * 4))
            if c == "ImageScaleBy":
                img = val(i["image"])
                return img.resize((int(img.width * i["scale_by"]), int(img.height * i["scale_by"])))
            if c == "RemoveBackground":  # BiRefNet: the mask is 1 on the subject (here: the red pixels)
                img = val(i["image"])
                return Image.frombytes("L", img.size, bytes(255 if (r > 150 and b < 100) else 0
                                                            for r, _g, b in img.getdata()))
            if c == "InvertMask":
                return Image.eval(val(i["mask"]), lambda v: 255 - v)
            if c == "JoinImageWithAlpha":  # alpha = 1 - mask: the mask says what to remove
                img = val(i["image"]).convert("RGBA")
                img.putalpha(Image.eval(val(i["alpha"]), lambda v: 255 - v))
                return img
            return None  # loaders: nothing to compute
        out = next(n for n in wf.values() if n["class_type"] == "SaveImage")
        return node(out)
