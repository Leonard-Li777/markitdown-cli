import os
import sys
import numpy as np
from typing import BinaryIO, Optional, List, Tuple
from dataclasses import dataclass

from ._ocr_service import OCRResult


class ONNXPPOCRService:
    """
    Lightweight PP-OCR service using ONNXRuntime + OpenCV (no PaddlePaddle dependency).
    """

    def __init__(
        self,
        det_model_path: Optional[str] = None,
        rec_model_path: Optional[str] = None,
        keys_path: Optional[str] = None,
        model_size: Optional[str] = None,
    ):
        self._available = False
        self.det_session = None
        self.rec_session = None
        self.char_list = []
        self.model_size = (model_size or "").lower()

        try:
            import cv2
            import onnxruntime as ort

            self._cv2 = cv2
            self._ort = ort

            det_path, rec_path, k_path = self._resolve_model_paths(
                det_model_path, rec_model_path, keys_path, self.model_size
            )

            if det_path and rec_path and k_path and os.path.isfile(det_path) and os.path.isfile(rec_path) and os.path.isfile(k_path):
                providers = ["CPUExecutionProvider"]
                sess_options = ort.SessionOptions()
                sess_options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
                cpu_count = os.cpu_count() or 4
                sess_options.intra_op_num_threads = min(8, cpu_count)

                self.det_session = ort.InferenceSession(det_path, sess_options, providers=providers)
                self.rec_session = ort.InferenceSession(rec_path, sess_options, providers=providers)

                with open(k_path, "r", encoding="utf-8") as f:
                    lines = [line.strip("\r\n") for line in f.readlines()]
                    self.char_list = [""] + lines + [" "]

                self._available = True
        except Exception as e:
            import sys
            print(f"[ONNXPPOCRService Error] {e}", file=sys.stderr)
            self._available = False

    def _resolve_model_paths(
        self,
        det_path: Optional[str],
        rec_path: Optional[str],
        keys_path: Optional[str],
        model_size: Optional[str] = None,
    ) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        exe_dir = os.path.dirname(sys.executable)
        meipass = getattr(sys, "_MEIPASS", exe_dir)
        repo_root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

        search_dirs = [
            os.path.join(exe_dir, "models"),
            os.path.join(meipass, "models"),
            os.path.join(repo_root, "models"),
            os.path.join(exe_dir),
            os.path.join(meipass),
            os.path.join(repo_root),
        ]

        def find_file(specified: Optional[str], default_names: List[str]) -> Optional[str]:
            if specified and os.path.isfile(specified):
                return specified
            for d in search_dirs:
                for name in default_names:
                    p = os.path.join(d, name)
                    if os.path.isfile(p):
                        return p
            return None

        # Determine file priority lists according to model_size
        size = (model_size or "").lower()
        if size == "small":
            det_names = ["PP-OCRv6_det_small.onnx", "PP-OCRv6_det_tiny.onnx", "PP-OCRv6_det_medium.onnx", "PP-OCRv4_det.onnx"]
            rec_names = ["PP-OCRv6_rec_small.onnx", "PP-OCRv6_rec_tiny.onnx", "PP-OCRv6_rec_medium.onnx", "PP-OCRv4_rec.onnx"]
            key_names = ["ppocr_keys_v6_small.txt", "ppocr_keys_v6_tiny.txt", "ppocr_keys_v6_medium.txt", "ppocr_keys_v4.txt", "ppocr_keys.txt"]
            dl_size = "small"
        elif size == "medium":
            det_names = ["PP-OCRv6_det_medium.onnx", "PP-OCRv6_det_small.onnx", "PP-OCRv6_det_tiny.onnx", "PP-OCRv4_det.onnx"]
            rec_names = ["PP-OCRv6_rec_medium.onnx", "PP-OCRv6_rec_small.onnx", "PP-OCRv6_rec_medium.onnx", "PP-OCRv4_rec.onnx"]
            key_names = ["ppocr_keys_v6_medium.txt", "ppocr_keys_v6_small.txt", "ppocr_keys_v6_tiny.txt", "ppocr_keys_v4.txt", "ppocr_keys.txt"]
            dl_size = "medium"
        else:
            # Default / omitted (or size == "tiny"): search order from small to large (tiny -> small -> medium)
            det_names = ["PP-OCRv6_det_tiny.onnx", "PP-OCRv6_det_small.onnx", "PP-OCRv6_det_medium.onnx", "PP-OCRv4_det.onnx"]
            rec_names = ["PP-OCRv6_rec_tiny.onnx", "PP-OCRv6_rec_small.onnx", "PP-OCRv6_rec_medium.onnx", "PP-OCRv4_rec.onnx"]
            key_names = ["ppocr_keys_v6_tiny.txt", "ppocr_keys_v6_small.txt", "ppocr_keys_v6_medium.txt", "ppocr_keys_v4.txt", "ppocr_keys.txt"]
            dl_size = "tiny" if size == "tiny" else "small"

        resolved_det = find_file(det_path, det_names)
        resolved_rec = find_file(rec_path, rec_names)
        resolved_keys = find_file(keys_path, key_names)

        # Auto-download from ModelScope if models not found
        if not (resolved_det and resolved_rec and resolved_keys):
            target_models_dir = os.path.join(exe_dir if os.path.isdir(os.path.join(exe_dir, "models")) else repo_root, "models")
            self._try_download_from_modelscope(target_models_dir, dl_size)
            resolved_det = find_file(det_path, det_names)
            resolved_rec = find_file(rec_path, rec_names)
            resolved_keys = find_file(keys_path, key_names)

        return resolved_det, resolved_rec, resolved_keys

    def _try_download_from_modelscope(self, target_dir: str, model_size: str = "small"):
        """Auto-downloads PP-OCR ONNX models from ModelScope (RapidAI/RapidOCR repo)."""
        try:
            import shutil
            from modelscope.hub.file_download import model_file_download

            os.makedirs(target_dir, exist_ok=True)
            size = (model_size or "small").lower()
            dict_name = "ppocrv6_dict.txt" if size == "medium" else f"ppocrv6_{size}_dict.txt"
            files = {
                f"PP-OCRv6_det_{size}.onnx": ("RapidAI/RapidOCR", f"onnx/PP-OCRv6/det/PP-OCRv6_det_{size}.onnx"),
                f"PP-OCRv6_rec_{size}.onnx": ("RapidAI/RapidOCR", f"onnx/PP-OCRv6/rec/PP-OCRv6_rec_{size}.onnx"),
                f"ppocr_keys_v6_{size}.txt": ("RapidAI/RapidOCR", f"paddle/PP-OCRv6/rec/PP-OCRv6_rec_{size}/{dict_name}"),
            }
            for local_name, (model_id, file_path) in files.items():
                dest = os.path.join(target_dir, local_name)
                if not os.path.isfile(dest):
                    downloaded = model_file_download(model_id=model_id, file_path=file_path)
                    shutil.copy2(downloaded, dest)
        except Exception:
            pass

    @property
    def available(self) -> bool:
        return self._available

    def _preprocess_det(self, img, max_side=960):
        cv2 = self._cv2
        h, w = img.shape[:2]
        scale = min(1.0, max_side / max(h, w))
        new_w = max(32, int(round(w * scale / 32) * 32))
        new_h = max(32, int(round(h * scale / 32) * 32))

        resized = cv2.resize(img, (new_w, new_h))
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)

        mean = np.array([0.485, 0.456, 0.406], dtype=np.float32)
        std = np.array([0.229, 0.224, 0.225], dtype=np.float32)
        norm = (rgb.astype(np.float32) / 255.0 - mean) / std

        chw = norm.transpose(2, 0, 1)
        return np.expand_dims(chw, axis=0), (w / new_w, h / new_h)

    def _preprocess_rec(self, crop_img, rec_h=48):
        cv2 = self._cv2
        ch, cw = crop_img.shape[:2]
        if ch == 0 or cw == 0:
            return None

        rec_w = int(round(rec_h * cw / ch))
        rec_w = max(8, min(rec_w, 2400))

        resized = cv2.resize(crop_img, (rec_w, rec_h))
        rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
        norm = (rgb.astype(np.float32) / 255.0 - 0.5) / 0.5

        chw = norm.transpose(2, 0, 1)
        return np.expand_dims(chw, axis=0)

    def _ctc_decode(self, rec_preds) -> Tuple[str, float]:
        preds_idx = rec_preds.argmax(axis=2)[0]
        preds_prob = rec_preds.max(axis=2)[0]

        text = ""
        confidences = []
        prev_idx = 0

        for idx, prob in zip(preds_idx, preds_prob):
            if idx != 0 and idx != prev_idx:
                if idx < len(self.char_list):
                    text += self.char_list[idx]
                    confidences.append(prob)
            prev_idx = idx

        avg_conf = float(np.mean(confidences)) if confidences else 0.0
        return text, avg_conf

    def _group_boxes_into_lines(self, boxes_with_text: List[Tuple[Tuple[int, int, int, int], str, float]]) -> str:
        if not boxes_with_text:
            return ""
        boxes_sorted = sorted(boxes_with_text, key=lambda item: item[0][0])
        lines = []
        for box_info in boxes_sorted:
            (y0, x0, y1, x1), text, conf = box_info
            placed = False
            for line in lines:
                ly0 = min(b[0][0] for b in line)
                ly1 = max(b[0][2] for b in line)
                line_h = max(1, ly1 - ly0)
                y_center = (y0 + y1) / 2.0
                if ly0 - line_h * 0.4 <= y_center <= ly1 + line_h * 0.4:
                    line.append(box_info)
                    placed = True
                    break
            if not placed:
                lines.append([box_info])

        lines.sort(key=lambda line: sum(b[0][0] for b in line) / float(len(line)))
        formatted_lines = []
        for line in lines:
            line.sort(key=lambda b: b[0][1])
            line_text = "  ".join(b[1] for b in line)
            formatted_lines.append(line_text)
        return "\n".join(formatted_lines)

    def extract_text(
        self,
        image_stream: BinaryIO,
        prompt: Optional[str] = None,
        **kwargs,
    ) -> OCRResult:
        if not self._available:
            return OCRResult(
                text="",
                backend_used="onnx_ppocr",
                error="ONNX PP-OCR models or dependencies not available",
            )

        try:
            from PIL import Image

            image_stream.seek(0)
            pil_image = Image.open(image_stream)
            if pil_image.mode != "RGB":
                pil_image = pil_image.convert("RGB")

            img = self._cv2.cvtColor(np.array(pil_image), self._cv2.COLOR_RGB2BGR)
            h_img, w_img = img.shape[:2]

            # --- Detection ---
            det_input, (scale_x, scale_y) = self._preprocess_det(img)
            input_name = self.det_session.get_inputs()[0].name
            det_out = self.det_session.run(None, {input_name: det_input})[0]

            prob_map = det_out[0, 0]
            bitmap = (prob_map > 0.3).astype(np.uint8)
            contours, _ = self._cv2.findContours(bitmap, self._cv2.RETR_LIST, self._cv2.CHAIN_APPROX_SIMPLE)

            rec_items = []
            for cnt in contours:
                x, y, w, h = self._cv2.boundingRect(cnt)
                if w < 5 or h < 5:
                    continue

                padding_x = int(w * 0.1)
                padding_y = int(h * 0.1)
                x0 = int(max(0, (x - padding_x) * scale_x))
                y0 = int(max(0, (y - padding_y) * scale_y))
                x1 = int(min(w_img, (x + w + padding_x) * scale_x))
                y1 = int(min(h_img, (y + h + padding_y) * scale_y))

                crop_img = img[y0:y1, x0:x1]
                rec_items.append(((y0, x0, y1, x1), crop_img))

            def _recognize(item):
                box, crop = item
                rec_input = self._preprocess_rec(crop)
                if rec_input is None:
                    return None
                rec_input_name = self.rec_session.get_inputs()[0].name
                rec_out = self.rec_session.run(None, {rec_input_name: rec_input})[0]
                text, conf = self._ctc_decode(rec_out)
                if text.strip():
                    return (box, text.strip(), conf)
                return None

            boxes_with_text = []
            if rec_items:
                from concurrent.futures import ThreadPoolExecutor
                max_workers = min(4, len(rec_items))
                with ThreadPoolExecutor(max_workers=max_workers) as pool:
                    res_list = list(pool.map(_recognize, rec_items))
                boxes_with_text = [r for r in res_list if r is not None]

            full_text = self._group_boxes_into_lines(boxes_with_text)

            return OCRResult(
                text=full_text,
                backend_used="onnx_ppocr",
            )
        except Exception as e:
            return OCRResult(
                text="",
                backend_used="onnx_ppocr",
                error=str(e),
            )
        finally:
            image_stream.seek(0)
