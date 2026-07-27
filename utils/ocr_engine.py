import base64
from pathlib import Path

def encode_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode('utf-8')

def process_medical_doc(file_path):
    # If PDF, you might need pdf2image, but for a hackathon, assume images for now
    return encode_image(file_path)