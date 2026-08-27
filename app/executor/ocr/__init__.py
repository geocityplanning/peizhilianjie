# -*- coding: utf-8 -*-
"""OCR package: captcha recognition via ddddocr + OpenCV preprocessing."""
from .captcha_recognizer import CaptchaRecognizer
from .image_processor import ImageProcessor

__all__ = ["CaptchaRecognizer", "ImageProcessor"]
