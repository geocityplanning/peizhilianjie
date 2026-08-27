# -*- coding: utf-8 -*-
"""图像预处理模块：使用 OpenCV 对验证码图片预处理，提高识别准确率。"""
import cv2
import numpy as np
from PIL import Image
from typing import Union, Optional


class ImageProcessor:
    """图像预处理器"""

    def __init__(self):
        pass

    def preprocess(self, image: Union[str, bytes, Image.Image, np.ndarray],
                   method: str = "auto") -> np.ndarray:
        if method == "auto":
            return self._auto_preprocess(self._to_numpy(image))
        elif method == "simple":
            return self._simple_preprocess(self._to_numpy(image))
        elif method == "aggressive":
            return self._aggressive_preprocess(self._to_numpy(image))
        else:
            raise ValueError(f"未知的预处理方法: {method}")

    def _to_numpy(self, image) -> np.ndarray:
        if isinstance(image, np.ndarray):
            return image
        elif isinstance(image, str):
            return cv2.imread(image)
        elif isinstance(image, bytes):
            nparr = np.frombuffer(image, np.uint8)
            return cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        elif isinstance(image, Image.Image):
            return cv2.cvtColor(np.array(image), cv2.COLOR_RGB2BGR)
        else:
            raise ValueError(f"不支持的图像类型: {type(image)}")

    def _auto_preprocess(self, img: np.ndarray) -> np.ndarray:
        return self._simple_preprocess(img.copy())

    def _simple_preprocess(self, img: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img
        denoised = cv2.GaussianBlur(gray, (3, 3), 0)
        return cv2.adaptiveThreshold(denoised, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                     cv2.THRESH_BINARY, 11, 2)

    def _aggressive_preprocess(self, img: np.ndarray) -> np.ndarray:
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY) if len(img.shape) == 3 else img
        equalized = cv2.equalizeHist(gray)
        denoised = cv2.medianBlur(equalized, 3)
        _, binary = cv2.threshold(denoised, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        kernel = np.ones((2, 2), np.uint8)
        return cv2.morphologyEx(binary, cv2.MORPH_OPEN, kernel)

    def to_pil(self, img: np.ndarray) -> Image.Image:
        if len(img.shape) == 2:
            return Image.fromarray(img)
        return Image.fromarray(cv2.cvtColor(img, cv2.COLOR_BGR2RGB))

    def save(self, img: np.ndarray, path: str):
        cv2.imwrite(path, img)
