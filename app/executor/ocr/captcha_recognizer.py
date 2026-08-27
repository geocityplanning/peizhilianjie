# -*- coding: utf-8 -*-
"""验证码识别模块：使用 ddddocr 进行验证码识别，支持多预处理重试。"""
import ddddocr
from PIL import Image
import numpy as np
from typing import Union, Optional, Dict, Any
import time

from .image_processor import ImageProcessor


class CaptchaRecognizer:
    """验证码识别器"""

    def __init__(self, use_gpu: bool = False):
        self.ocr = ddddocr.DdddOcr(show_ad=False, use_gpu=use_gpu)
        self.processor = ImageProcessor()

    def recognize(self, image: Union[str, bytes, Image.Image, np.ndarray],
                  preprocess: bool = True, preprocess_method: str = "auto",
                  save_debug: bool = False, debug_path: Optional[str] = None) -> Dict[str, Any]:
        start = time.time()
        try:
            if preprocess:
                img_array = self.processor.preprocess(image, method=preprocess_method)
                if save_debug and debug_path:
                    self.processor.save(img_array, debug_path)
                image_bytes = self._pil_to_bytes(self.processor.to_pil(img_array))
            else:
                if isinstance(image, str):
                    with open(image, "rb") as f:
                        image_bytes = f.read()
                elif isinstance(image, bytes):
                    image_bytes = image
                else:
                    img_pil = self.processor.to_pil(image) if isinstance(image, np.ndarray) else image
                    image_bytes = self._pil_to_bytes(img_pil)

            text = self.ocr.classification(image_bytes).strip()
            return {
                "text": text,
                "confidence": None,
                "success": True,
                "process_time": round(time.time() - start, 3),
                "preprocessed": preprocess,
                "length": len(text),
            }
        except Exception as e:
            return {
                "text": "",
                "success": False,
                "process_time": round(time.time() - start, 3),
                "preprocessed": preprocess,
                "error": str(e),
            }

    def recognize_with_retry(self, image, max_retries: int = 3,
                              methods: list = None) -> Dict[str, Any]:
        if methods is None:
            methods = ["simple", "auto", "aggressive"]
        results = []
        r = self.recognize(image, preprocess=False)
        if r["success"] and r["text"]:
            results.append(r)
        for method in methods[:max_retries]:
            r = self.recognize(image, preprocess=True, preprocess_method=method)
            if r["success"] and r["text"]:
                results.append(r)
        if not results:
            return {"text": "", "success": False, "error": "所有识别尝试均失败"}
        best = max(results, key=lambda x: x.get("length", 0))
        best["retry_count"] = len(results) - 1
        return best

    @staticmethod
    def _pil_to_bytes(img: Image.Image) -> bytes:
        import io
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()
