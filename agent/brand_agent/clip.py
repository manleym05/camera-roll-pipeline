"""Local CLIP embeddings and zero-shot tags (open_clip ViT-B-32, 512-d)."""

from functools import cached_property

import numpy as np
import torch
from PIL import Image

from brand_agent.config import settings

MODEL = "ViT-B-32"
PRETRAINED = "laion2b_s34b_b79k"

# Zero-shot tag vocabulary. Pillars first, then scene detail for search and ranking.
TAG_PROMPTS = {
    "pillar:travel": "a travel photo of a landmark, city street, beach or mountain",
    "pillar:fitness": "a photo of someone working out, running, lifting weights or at a gym",
    "pillar:work": "a photo of an office, laptop, meeting, conference or someone presenting",
    "pillar:tech": "a photo of computers, code on a screen, gadgets or technology",
    "scene:food": "a photo of food or a meal at a restaurant",
    "scene:people": "a photo of a group of friends",
    "scene:selfie": "a selfie",
    "scene:nature": "a landscape photo of nature",
    "scene:night": "a photo taken at night",
    "scene:water": "a photo of the ocean, a lake or a boat",
    "scene:architecture": "a photo of buildings and architecture",
    "scene:event": "a photo of a crowd at a concert, game or event",
}
# Screenshots and paper are never posted.
DOC_PROMPTS = {
    "document": "a photo of a document, receipt, whiteboard, or paper with text",
    "screen": "a screenshot of a phone or computer screen",
    "photo": "a candid photo of people, places or things",
}


class Clip:
    @cached_property
    def device(self) -> str:
        if settings.clip_device != "auto":
            return settings.clip_device
        return "cuda" if torch.cuda.is_available() else "cpu"

    @cached_property
    def _model(self):
        import open_clip

        model, _, preprocess = open_clip.create_model_and_transforms(MODEL, pretrained=PRETRAINED, device=self.device)
        model.eval()
        return model, preprocess, open_clip.get_tokenizer(MODEL)

    def _text(self, prompts: list[str]) -> torch.Tensor:
        model, _, tok = self._model
        with torch.no_grad():
            t = model.encode_text(tok(prompts).to(self.device)).float()
        return t / t.norm(dim=-1, keepdim=True)

    @cached_property
    def _tag_text(self) -> torch.Tensor:
        return self._text(list(TAG_PROMPTS.values()))

    @cached_property
    def _doc_text(self) -> torch.Tensor:
        return self._text(list(DOC_PROMPTS.values()))

    def embed(self, images: list[Image.Image]) -> np.ndarray:
        model, preprocess, _ = self._model
        batch = torch.stack([preprocess(im) for im in images]).to(self.device)
        with torch.no_grad():
            e = model.encode_image(batch).float()
        return (e / e.norm(dim=-1, keepdim=True)).cpu().numpy()

    def tags(self, emb: np.ndarray) -> list[dict]:
        """Per image: {tag: score} for tags above chance, plus document-likelihood."""
        e = torch.from_numpy(emb).to(self.device)
        tag_logits = (100 * e @ self._tag_text.T).softmax(dim=-1).cpu().numpy()
        doc_probs = (100 * e @ self._doc_text.T).softmax(dim=-1).cpu().numpy()
        keys, floor = list(TAG_PROMPTS), 1.5 / len(TAG_PROMPTS)
        out = []
        for row, doc in zip(tag_logits, doc_probs):
            tags = {k: round(float(v), 3) for k, v in zip(keys, row) if v >= floor}
            tags["_doc"] = round(float(doc[0] + doc[1]), 3)
            out.append(tags)
        return out
