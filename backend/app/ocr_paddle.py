"""PaddleOCR engine (optional): pip install -r requirements-ocr.txt. Models download on first use."""
import numpy as np
from paddleocr import PaddleOCR

from app.ocr import OcrLine


class PaddleEngine:
    def __init__(self) -> None:
        # enable_mkldnn=False: paddlepaddle 3.3.1's oneDNN path crashes on these models
        # ("ConvertPirAttribute2RuntimeAttribute not support").
        self._ocr = PaddleOCR(
            lang="en",
            use_doc_orientation_classify=False,
            use_doc_unwarping=False,
            use_textline_orientation=False,
            enable_mkldnn=False,
        )

    def read(self, image: np.ndarray) -> list[OcrLine]:
        results = self._ocr.predict(image)
        if not results:
            return []
        r = results[0]
        return [
            OcrLine(text, float(score), *map(float, box))
            for text, score, box in zip(r["rec_texts"], r["rec_scores"], r["rec_boxes"])
        ]
