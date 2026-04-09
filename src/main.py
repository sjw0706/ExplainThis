import os
import sys
import json
from dataclasses import dataclass
from io import BytesIO
from typing import Optional

import requests
import pytesseract
from PIL import Image, ImageGrab
from PySide6.QtCore import Qt, QRect, QPoint, Signal, QSize
from PySide6.QtGui import QAction, QColor, QGuiApplication, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QFormLayout,
    QDialog,
    QDialogButtonBox,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QPlainTextEdit,
    QSizePolicy,
    QSplitter,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

APP_NAME = "ExplainThis"
DEFAULT_MODEL = "gpt-4o-mini"
DEFAULT_BASE_URL = "https://api.openai.com/v1"

PROMPT_TEMPLATE = """You are an assistant that explains captured screen text.
Return JSON with keys: summary, explanation, key_points.
Rules:
- summary: 2-4 concise Korean sentences.
- explanation: Explain the content in plain Korean.
- key_points: array of 3 to 6 short bullet items in Korean.
- If the text looks like code, explain purpose, core logic, and probable use case.
- If the text looks like an equation or academic writing, explain the meaning intuitively.
- Never include markdown fences.
"""

def app_base_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

def resource_path(*parts: str) -> str:
    return os.path.join(app_base_dir(), *parts)

def setup_bundled_tesseract() -> None:
    tesseract_root = resource_path("third_party", "tesseract")
    tesseract_exe = os.path.join(tesseract_root, "tesseract.exe")
    tessdata_dir = os.path.join(tesseract_root, "tessdata")

    if os.path.exists(tesseract_exe):
        pytesseract.pytesseract.tesseract_cmd = tesseract_exe

    if os.path.isdir(tessdata_dir):
        os.environ["TESSDATA_PREFIX"] = tessdata_dir

@dataclass
class AppConfig:
    api_key: str = os.getenv("OPENAI_API_KEY", "")
    model: str = os.getenv("EXPLAINTHIS_MODEL", DEFAULT_MODEL)
    base_url: str = os.getenv("OPENAI_BASE_URL", DEFAULT_BASE_URL)

class ConfigDialog(QDialog):
    def __init__(self, config: AppConfig, parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setWindowTitle("설정")
        self.resize(520, 180)
        self._config = config

        layout = QVBoxLayout(self)
        form = QFormLayout()

        self.api_key_input = QLineEdit(config.api_key)
        self.api_key_input.setEchoMode(QLineEdit.Password)
        self.model_input = QLineEdit(config.model)
        self.base_url_input = QLineEdit(config.base_url)

        form.addRow("API Key", self.api_key_input)
        form.addRow("Model", self.model_input)
        form.addRow("Base URL", self.base_url_input)

        helper = QLabel("배포형은 Tesseract를 내부 포함하므로 별도 OCR 경로 설정이 필요 없습니다.")
        helper.setWordWrap(True)
        helper.setStyleSheet("color: #666;")

        layout.addLayout(form)
        layout.addWidget(helper)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def get_config(self) -> AppConfig:
        return AppConfig(
            api_key=self.api_key_input.text().strip(),
            model=self.model_input.text().strip() or DEFAULT_MODEL,
            base_url=self.base_url_input.text().strip() or DEFAULT_BASE_URL,
        )

class RegionOverlay(QWidget):
    region_selected = Signal(tuple)
    selection_canceled = Signal()

    def __init__(self):
        super().__init__()
        self.start_point = QPoint()
        self.end_point = QPoint()
        self.is_selecting = False
        self.screen_pixmap = self._capture_full_desktop()

        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setWindowState(Qt.WindowFullScreen)
        self.setCursor(Qt.CrossCursor)
        self.setAttribute(Qt.WA_TranslucentBackground)

    def _capture_full_desktop(self) -> QPixmap:
        screen = QGuiApplication.primaryScreen()
        if not screen:
            return QPixmap()
        return screen.grabWindow(0)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.drawPixmap(0, 0, self.screen_pixmap)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 90))

        rect = QRect(self.start_point, self.end_point).normalized()
        if not rect.isNull():
            painter.drawPixmap(rect, self.screen_pixmap, rect)
            pen = QPen(QColor(0, 170, 255), 2)
            painter.setPen(pen)
            painter.drawRect(rect)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.start_point = event.position().toPoint()
            self.end_point = self.start_point
            self.is_selecting = True
            self.update()

    def mouseMoveEvent(self, event):
        if self.is_selecting:
            self.end_point = event.position().toPoint()
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton and self.is_selecting:
            self.end_point = event.position().toPoint()
            self.is_selecting = False
            rect = QRect(self.start_point, self.end_point).normalized()
            self.hide()
            if rect.width() < 10 or rect.height() < 10:
                self.selection_canceled.emit()
            else:
                self.region_selected.emit((rect.left(), rect.top(), rect.right(), rect.bottom()))
            self.close()

    def keyPressEvent(self, event):
        if event.key() == Qt.Key_Escape:
            self.hide()
            self.selection_canceled.emit()
            self.close()

class AIClient:
    def __init__(self, config: AppConfig):
        self.config = config

    def explain(self, text: str) -> dict:
        if not self.config.api_key:
            raise RuntimeError("API Key가 설정되지 않았습니다.")

        url = self.config.base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.config.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.config.model,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": PROMPT_TEMPLATE},
                {
                    "role": "user",
                    "content": f"Analyze the following OCR text and explain it in Korean.\n\n{text}",
                },
            ],
            "temperature": 0.3,
        }

        response = requests.post(url, headers=headers, json=payload, timeout=60)
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        parsed = json.loads(content)

        return {
            "summary": parsed.get("summary", ""),
            "explanation": parsed.get("explanation", ""),
            "key_points": parsed.get("key_points", []),
        }

class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.resize(1320, 860)

        setup_bundled_tesseract()

        self.config = AppConfig()
        self.captured_image: Optional[Image.Image] = None
        self.current_ocr_text: str = ""

        self._build_ui()
        self._build_menu()
        self.statusBar().showMessage("준비 완료")

    def _build_menu(self):
        menu = self.menuBar()
        file_menu = menu.addMenu("파일")
        settings_menu = menu.addMenu("설정")

        open_image_action = QAction("이미지 불러오기", self)
        open_image_action.triggered.connect(self.load_image)
        file_menu.addAction(open_image_action)

        save_text_action = QAction("결과 저장", self)
        save_text_action.triggered.connect(self.save_results)
        file_menu.addAction(save_text_action)

        config_action = QAction("API 설정", self)
        config_action.triggered.connect(self.open_settings)
        settings_menu.addAction(config_action)

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        main_layout = QVBoxLayout(root)
        main_layout.setContentsMargins(16, 16, 16, 16)
        main_layout.setSpacing(12)

        title = QLabel("ExplainThis")
        title.setStyleSheet("font-size: 28px; font-weight: 700;")
        subtitle = QLabel("화면 선택 → OCR → AI 설명을 한 번에 수행하는 Windows 유틸리티")
        subtitle.setStyleSheet("font-size: 14px; color: #666;")

        header_layout = QVBoxLayout()
        header_layout.addWidget(title)
        header_layout.addWidget(subtitle)
        main_layout.addLayout(header_layout)

        button_row = QHBoxLayout()
        self.capture_btn = QPushButton("화면 영역 선택")
        self.capture_btn.clicked.connect(self.start_capture)
        self.load_btn = QPushButton("이미지 불러오기")
        self.load_btn.clicked.connect(self.load_image)
        self.ocr_btn = QPushButton("OCR 실행")
        self.ocr_btn.clicked.connect(self.run_ocr)
        self.ai_btn = QPushButton("AI 설명 생성")
        self.ai_btn.clicked.connect(self.run_ai)
        self.clear_btn = QPushButton("초기화")
        self.clear_btn.clicked.connect(self.clear_all)

        for btn in [self.capture_btn, self.load_btn, self.ocr_btn, self.ai_btn, self.clear_btn]:
            btn.setMinimumHeight(40)
            button_row.addWidget(btn)

        main_layout.addLayout(button_row)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_left_panel())
        splitter.addWidget(self._build_right_panel())
        splitter.setStretchFactor(0, 4)
        splitter.setStretchFactor(1, 6)

        main_layout.addWidget(splitter)

        self.setStyleSheet(
            """
            QMainWindow { background: #f7f8fb; }
            QGroupBox {
                font-size: 14px;
                font-weight: 600;
                border: 1px solid #d9deea;
                border-radius: 12px;
                margin-top: 12px;
                background: white;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 12px;
                padding: 0 4px;
            }
            QTextEdit, QPlainTextEdit {
                border: 1px solid #d9deea;
                border-radius: 10px;
                padding: 8px;
                background: white;
                font-size: 13px;
            }
            QPushButton {
                background: #1e66f5;
                color: white;
                border: none;
                border-radius: 10px;
                padding: 10px 14px;
                font-size: 13px;
                font-weight: 600;
            }
            QPushButton:hover { background: #1958d6; }
            QLabel#previewLabel {
                border: 1px dashed #c3cada;
                border-radius: 12px;
                background: white;
            }
            """
        )

    def _build_left_panel(self) -> QWidget:
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        preview_group = QGroupBox("캡처 미리보기")
        preview_layout = QVBoxLayout(preview_group)
        self.preview_label = QLabel("아직 선택된 이미지가 없습니다.")
        self.preview_label.setObjectName("previewLabel")
        self.preview_label.setAlignment(Qt.AlignCenter)
        self.preview_label.setMinimumSize(QSize(480, 320))
        self.preview_label.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        preview_layout.addWidget(self.preview_label)
        layout.addWidget(preview_group, 6)

        ocr_group = QGroupBox("OCR 원문")
        ocr_layout = QVBoxLayout(ocr_group)
        self.ocr_text = QPlainTextEdit()
        self.ocr_text.setPlaceholderText("OCR 결과가 여기에 표시됩니다.")
        ocr_layout.addWidget(self.ocr_text)
        layout.addWidget(ocr_group, 4)

        return wrapper

    def _build_right_panel(self) -> QWidget:
        wrapper = QWidget()
        layout = QVBoxLayout(wrapper)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        summary_group = QGroupBox("요약")
        summary_layout = QVBoxLayout(summary_group)
        self.summary_text = QTextEdit()
        self.summary_text.setPlaceholderText("AI 요약 결과")
        summary_layout.addWidget(self.summary_text)

        explanation_group = QGroupBox("설명")
        explanation_layout = QVBoxLayout(explanation_group)
        self.explanation_text = QTextEdit()
        self.explanation_text.setPlaceholderText("AI가 생성한 쉬운 설명")
        explanation_layout.addWidget(self.explanation_text)

        keypoints_group = QGroupBox("핵심 포인트")
        keypoints_layout = QVBoxLayout(keypoints_group)
        self.keypoints_text = QTextEdit()
        self.keypoints_text.setPlaceholderText("핵심 포인트 목록")
        keypoints_layout.addWidget(self.keypoints_text)

        layout.addWidget(summary_group, 2)
        layout.addWidget(explanation_group, 4)
        layout.addWidget(keypoints_group, 2)

        return wrapper

    def start_capture(self):
        self.statusBar().showMessage("화면 캡처 대기 중... 드래그로 영역을 선택하세요.")
        self.hide()
        QApplication.processEvents()
        self.overlay = RegionOverlay()
        self.overlay.region_selected.connect(self.on_region_selected)
        self.overlay.selection_canceled.connect(self.on_capture_canceled)
        self.overlay.show()

    def on_capture_canceled(self):
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self.statusBar().showMessage("캡처가 취소되었습니다.")

    def on_region_selected(self, bbox: tuple):
        try:
            image = ImageGrab.grab(bbox=bbox, all_screens=True)
            self.captured_image = image
            self.update_preview(image)
            self.statusBar().showMessage("영역 캡처 완료")
        except Exception as exc:
            QMessageBox.critical(self, "캡처 오류", str(exc))
            self.statusBar().showMessage("캡처 실패")
        finally:
            self.showNormal()
            self.raise_()
            self.activateWindow()

    def update_preview(self, image: Image.Image):
        preview = image.copy()
        preview.thumbnail((720, 480))
        buffer = BytesIO()
        preview.save(buffer, format="PNG")
        pixmap = QPixmap()
        pixmap.loadFromData(buffer.getvalue(), "PNG")
        self.preview_label.setPixmap(pixmap)
        self.preview_label.setScaledContents(False)

    def load_image(self):
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "이미지 불러오기",
            "",
            "Images (*.png *.jpg *.jpeg *.bmp *.webp)",
        )
        if not file_path:
            return
        try:
            image = Image.open(file_path)
            self.captured_image = image
            self.update_preview(image)
            self.statusBar().showMessage(f"이미지 로드 완료: {file_path}")
        except Exception as exc:
            QMessageBox.critical(self, "로드 오류", str(exc))

    def preprocess_image_for_ocr(self, image: Image.Image) -> Image.Image:
        gray = image.convert("L")
        return gray.point(lambda p: 255 if p > 180 else 0)

    def clean_ocr_text(self, text: str) -> str:
        lines = [line.strip() for line in text.splitlines()]
        lines = [line for line in lines if line]
        merged = []
        for line in lines:
            if merged and not merged[-1].endswith((".", ":", ";", "?", "!")):
                merged[-1] += " " + line
            else:
                merged.append(line)
        return "\n".join(merged).strip()

    def run_ocr(self):
        if self.captured_image is None:
            QMessageBox.information(self, "안내", "먼저 화면 영역을 선택하거나 이미지를 불러오세요.")
            return

        try:
            processed = self.preprocess_image_for_ocr(self.captured_image)
            text = pytesseract.image_to_string(processed, lang="eng+kor")
            cleaned = self.clean_ocr_text(text)
            self.current_ocr_text = cleaned
            self.ocr_text.setPlainText(cleaned)
            self.statusBar().showMessage("OCR 완료")
        except Exception as exc:
            QMessageBox.critical(
                self,
                "OCR 오류",
                f"OCR 처리 중 오류가 발생했습니다.\n\n{exc}\n\n배포된 third_party/tesseract 폴더를 확인하세요.",
            )
            self.statusBar().showMessage("OCR 실패")

    def run_ai(self):
        if not self.current_ocr_text.strip():
            QMessageBox.information(self, "안내", "먼저 OCR을 실행해 텍스트를 준비하세요.")
            return

        try:
            self.statusBar().showMessage("AI 설명 생성 중...")
            QApplication.setOverrideCursor(Qt.WaitCursor)

            client = AIClient(self.config)
            result = client.explain(self.current_ocr_text)

            self.summary_text.setPlainText(result.get("summary", ""))
            self.explanation_text.setPlainText(result.get("explanation", ""))

            key_points = result.get("key_points", [])
            if isinstance(key_points, list):
                self.keypoints_text.setPlainText("\n".join(f"- {item}" for item in key_points))
            else:
                self.keypoints_text.setPlainText(str(key_points))

            self.statusBar().showMessage("AI 설명 생성 완료")
        except Exception as exc:
            QMessageBox.critical(self, "AI 오류", str(exc))
            self.statusBar().showMessage("AI 설명 생성 실패")
        finally:
            QApplication.restoreOverrideCursor()

    def save_results(self):
        file_path, _ = QFileDialog.getSaveFileName(
            self,
            "결과 저장",
            "explainthis_result.txt",
            "Text Files (*.txt)",
        )
        if not file_path:
            return

        content = (
            "[OCR 원문]\n"
            + self.ocr_text.toPlainText()
            + "\n\n[요약]\n"
            + self.summary_text.toPlainText()
            + "\n\n[설명]\n"
            + self.explanation_text.toPlainText()
            + "\n\n[핵심 포인트]\n"
            + self.keypoints_text.toPlainText()
        )

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(content)
            self.statusBar().showMessage(f"저장 완료: {file_path}")
        except Exception as exc:
            QMessageBox.critical(self, "저장 오류", str(exc))

    def open_settings(self):
        dialog = ConfigDialog(self.config, self)
        if dialog.exec() == QDialog.Accepted:
            self.config = dialog.get_config()
            self.statusBar().showMessage("설정 저장 완료")

    def clear_all(self):
        self.captured_image = None
        self.current_ocr_text = ""
        self.preview_label.setText("아직 선택된 이미지가 없습니다.")
        self.preview_label.setPixmap(QPixmap())
        self.ocr_text.clear()
        self.summary_text.clear()
        self.explanation_text.clear()
        self.keypoints_text.clear()
        self.statusBar().showMessage("초기화 완료")

def main() -> None:
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()