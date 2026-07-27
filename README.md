🛡️ Medi-Audit AI: Medical billing dark box against corruption

Medi-Audit Pro is an executive-grade platform designed to detect overbilling and fraudulent medical claims. It uses OpenAI's GPT models and Tesseract OCR to cross-reference hospital bills and pharmacy receipts against the CGHS 2026 Legal Price Gazette.

🚀 Key Features
🏥 Hospital & Pharma Forensic Scan: Automatic extraction of bill items with real-time comparison against legal price ceilings.
🗺️ National Fraud Radar: A heatmap powered by Pydeck that visualizes financial leakage across major cities.
📊 Executive Dashboard: High-level analytics showing variance, leakage trends, and audit accuracy.
🛡️ Insurance Armor: Detects underpayments and policy breaches in insurance settlement letters.
💬 Forensic Co-Pilot: Integrated AI assistant to help users understand complex medical billing regulations.

🛠️ Tech Stack
Frontend: Streamlit
AI Engine: OpenAI GPT-3.5 Turbo / GPT-4o
OCR: Tesseract OCR
Visualization: Plotly Express & Graph Objects
PDF Parsing: PyMuPDF (Fitz)

📋 Installation
1. Prerequisites
Ensure you have Tesseract OCR installed on your system:
Windows: Download Tesseract
Linux: sudo apt install tesseract-ocr

2. Setup Environment
Create a .env file in the root directory:
Code snippet
OPENAI_API_KEY=your_key_here
SENDER_EMAIL=your_gmail@gmail.com
SENDER_PASSWORD=your_app_password

3. Install Dependencies
Bash
pip install streamlit openai pytesseract pandas plotly pydeck pymupdf pillow python-dotenv streamlit-mic-recorder

4. Run Application
Bash
streamlit run app.py
📂 Project Structure
Plaintext
├── app.py                # Main Application Code
├── users.json            # Encrypted User Database (Generated)
├── .env                  # Environment Variables
├── data/
│   └── raw_gazette/
│       └── cghs_rates_2026.pdf  # Legal Reference PDF
└── README.md             # Project Documentation


⚖️ Disclaimer
This software is a forensic tool for identifying billing discrepancies. All results should be verified by a legal or medical professional before taking formal action.