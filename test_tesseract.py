import os
import sys
import pytesseract
from PIL import Image

def resource_path(*parts):
    if getattr(sys, 'frozen', False):
        base_path = sys._MEIPASS
    else:
        base_path = os.path.abspath(".")
    return os.path.join(base_path, *parts)

# 1. 경로 설정
tesseract_exe = resource_path("third_party", "tesseract", "tesseract.exe")
tessdata_dir = resource_path("third_party", "tesseract", "tessdata")

print("=== 경로 체크 ===")
print("tesseract.exe:", tesseract_exe)
print("존재 여부:", os.path.exists(tesseract_exe))

print("tessdata:", tessdata_dir)
print("존재 여부:", os.path.exists(tessdata_dir))

# 2. 환경 변수 적용
pytesseract.pytesseract.tesseract_cmd = tesseract_exe
os.environ["TESSDATA_PREFIX"] = tessdata_dir

# 3. 간단 테스트 이미지 생성
test_image = Image.new("RGB", (200, 80), color="white")

from PIL import ImageDraw
draw = ImageDraw.Draw(test_image)
draw.text((10, 20), "Hello 123", fill="black")

test_image.save("test.png")

# 4. OCR 실행
print("\n=== OCR 테스트 ===")
try:
    result = pytesseract.image_to_string(test_image, lang="eng")
    print("OCR 결과:", result.strip())
    print("👉 SUCCESS: Tesseract 정상 동작")
except Exception as e:
    print("👉 ERROR:", e)