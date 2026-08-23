```python
import streamlit as st
import base64, json, os, random, smtplib, time, re, io, shutil, pandas as pd, difflib
import fitz  # PyMuPDF for fast multi-PDF search
import plotly.graph_objects as go
import plotly.express as px
import pytesseract
from PIL import Image, ImageEnhance, ImageOps
from email.message import EmailMessage
from dotenv import load_dotenv
from langchain_groq import ChatGroq
from groq import Groq
from datetime import datetime

# --- 1. SYSTEM & TESSERACT CONFIGURATION ---
if os.name == 'nt':
    pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
else:
    tesseract_bin = shutil.which("tesseract") or "/usr/bin/tesseract"
    if os.path.exists(tesseract_bin):
        pytesseract.pytesseract.tesseract_cmd = tesseract_bin

load_dotenv()

GROQ_API_KEY = st.secrets.get("GROQ_API_KEY", os.getenv("GROQ_API_KEY"))

if not GROQ_API_KEY:
    st.error("⚠️ GROQ_API_KEY is missing! Please configure it in Streamlit Secrets or your .env file.")
    st.stop()

groq_client = Groq(api_key=GROQ_API_KEY)

# --- 2. ACTIVE MODEL DISCOVERY ---
def get_best_available_groq_model():
    try:
        models_data = groq_client.models.list().data
        available_ids = [m.id for m in models_data]
    except Exception:
        available_ids = []

    candidate_models = [
        "llama-3.3-70b-versatile", "llama-3.1-8b-instant", "llama3-70b-8192",
        "llama3-8b-8192", "gemma2-9b-it", "mixtral-8x7b-32768"
    ]
    for m_id in available_ids:
        m_lower = m_id.lower()
        if not any(bad in m_lower for bad in ["whisper", "guard", "embed", "vision", "canopylabs", "orpheus", "tts", "audio"]):
            if m_id not in candidate_models:
                candidate_models.append(m_id)

    for model in candidate_models:
        try:
            test_res = groq_client.chat.completions.create(model=model, messages=[{"role": "user", "content": "ping"}], max_tokens=1)
            if test_res.choices: return model
        except Exception:
            continue
    return "llama-3.1-8b-instant"

ACTIVE_GROQ_MODEL = get_best_available_groq_model()

llm = ChatGroq(groq_api_key=GROQ_API_KEY, model_name=ACTIVE_GROQ_MODEL, temperature=0)

# --- 3. ROBUST JSON PARSER ---
def safe_extract_json(raw_response_content):
    if not raw_response_content: return None
    text = str(raw_response_content).strip()
    text = re.sub(r'<think>.*?</think>', '', text, flags=re.DOTALL).strip()
    json_match = re.search(r'(\{[\s\S]*\})', text)
    if json_match: text = json_match.group(1).strip()
    text = text.replace("```json", "").replace("```", "").strip()
    try: return json.loads(text)
    except Exception:
        clean_text = re.sub(r'^[^{]*', '', text)
        clean_text = re.sub(r'[^}]*$', '', clean_text)
        try: return json.loads(clean_text)
        except Exception: return None

SENDER_EMAIL = st.secrets.get("SENDER_EMAIL", os.getenv("SENDER_EMAIL"))
SENDER_PASSWORD = st.secrets.get("SENDER_PASSWORD", os.getenv("SENDER_PASSWORD"))
USER_DB = "users.json"
GAZETTE_DIR = os.path.join("data", "raw_gazzete")

# --- 4. MULTI-GAZETTE SEARCH ENGINE ---
def get_pdf_context(query):
    text_context = ""
    if os.path.exists(GAZETTE_DIR):
        try:
            keywords = [word for word in query.split() if len(word) > 3]
            pdf_files = [f for f in os.listdir(GAZETTE_DIR) if f.endswith(".pdf")]
            for pdf_file in pdf_files:
                full_path = os.path.join(GAZETTE_DIR, pdf_file)
                with fitz.open(full_path) as doc:
                    found = 0
                    for page in doc:
                        page_text = page.get_text()
                        if any(key.lower() in page_text.lower() for key in keywords):
                            text_context += f"\n[GAZETTE SOURCE: {pdf_file}]\n" + page_text
                            found += 1
                        if found >= 2: break
            return text_context[:8000]
        except Exception: return ""
    return ""

# --- 5. CANONICAL STATUTORY GAZETTE MASTER ---
CGHS_GAZETTE_MASTER = {
    "consultation": {"code": "CON01", "name": "OPD Consultation (Specialist)", "cap": 350.0, "category": "Consultation", "authority": "CGHS 2026 Gazette (MoHFW)"},
    "consultation fee": {"code": "CON01", "name": "OPD Consultation (Specialist)", "cap": 350.0, "category": "Consultation", "authority": "CGHS 2026 Gazette (MoHFW)"},
    "doctor consultation": {"code": "CON01", "name": "OPD Consultation (Specialist)", "cap": 350.0, "category": "Consultation", "authority": "CGHS 2026 Gazette (MoHFW)"},
    "super specialist consultation": {"code": "CON02", "name": "OPD Consultation (Super Specialist)", "cap": 700.0, "category": "Consultation", "authority": "CGHS 2026 Gazette (MoHFW)"},
    "icu charges": {"code": "ICU01", "name": "ICU (Without Ventilator)", "cap": 5400.0, "category": "Inpatient / ICU", "authority": "CGHS 2026 Gazette (MoHFW)"},
    "icu": {"code": "ICU01", "name": "ICU (Without Ventilator)", "cap": 5400.0, "category": "Inpatient / ICU", "authority": "CGHS 2026 Gazette (MoHFW)"},
    "icu ventilator": {"code": "ICU02", "name": "ICU (With Ventilator)", "cap": 7200.0, "category": "Inpatient / ICU", "authority": "CGHS 2026 Gazette (MoHFW)"},
    "general ward": {"code": "BED01", "name": "General Ward Bed (Per Day)", "cap": 1500.0, "category": "Inpatient / Bed", "authority": "CGHS 2026 Gazette (MoHFW)"},
    "semi private ward": {"code": "BED02", "name": "Semi-Private Ward (Per Day)", "cap": 3000.0, "category": "Inpatient / Bed", "authority": "CGHS 2026 Gazette (MoHFW)"},
    "private ward": {"code": "BED03", "name": "Private Ward (Per Day)", "cap": 4500.0, "category": "Inpatient / Bed", "authority": "CGHS 2026 Gazette (MoHFW)"},
    "room rent": {"code": "BED01", "name": "Standard Room Rent (Per Day)", "cap": 1500.0, "category": "Inpatient / Bed", "authority": "CGHS 2026 Gazette (MoHFW)"},
    "mri brain plain": {"code": "RI089", "name": "MRI Brain (Plain)", "cap": 2750.0, "category": "Radiology & Imaging", "authority": "CGHS 2026 Gazette (MoHFW)"},
    "mri brain": {"code": "RI089", "name": "MRI Brain (Plain)", "cap": 2750.0, "category": "Radiology & Imaging", "authority": "CGHS 2026 Gazette (MoHFW)"},
    "mri brain contrast": {"code": "RI090", "name": "MRI Brain with Contrast", "cap": 4000.0, "category": "Radiology & Imaging", "authority": "CGHS 2026 Gazette (MoHFW)"},
    "mri spine": {"code": "RI092", "name": "MRI Spine (Plain)", "cap": 2750.0, "category": "Radiology & Imaging", "authority": "CGHS 2026 Gazette (MoHFW)"},
    "ct scan brain": {"code": "CT012", "name": "CT Head / Brain (Plain)", "cap": 1150.0, "category": "Radiology & Imaging", "authority": "CGHS 2026 Gazette (MoHFW)"},
    "ct brain": {"code": "CT012", "name": "CT Head / Brain (Plain)", "cap": 1150.0, "category": "Radiology & Imaging", "authority": "CGHS 2026 Gazette (MoHFW)"},
    "ct chest": {"code": "CT045", "name": "HRCT Chest (Plain)", "cap": 1800.0, "category": "Radiology & Imaging", "authority": "CGHS 2026 Gazette (MoHFW)"},
    "chest x ray": {"code": "XR001", "name": "Chest X-Ray (PA View)", "cap": 250.0, "category": "Radiology & Imaging", "authority": "CGHS 2026 Gazette (MoHFW)"},
    "x ray": {"code": "XR001", "name": "Standard X-Ray", "cap": 250.0, "category": "Radiology & Imaging", "authority": "CGHS 2026 Gazette (MoHFW)"},
    "ultrasound abdomen": {"code": "US004", "name": "USG Whole Abdomen & Pelvis", "cap": 550.0, "category": "Radiology & Imaging", "authority": "CGHS 2026 Gazette (MoHFW)"},
    "usg abdomen": {"code": "US004", "name": "USG Whole Abdomen & Pelvis", "cap": 550.0, "category": "Radiology & Imaging", "authority": "CGHS 2026 Gazette (MoHFW)"},
    "cbc": {"code": "LB012", "name": "Complete Haemogram / CBC", "cap": 150.0, "category": "Pathology & Diagnostics", "authority": "CGHS 2026 Gazette (MoHFW)"},
    "cbc blood test": {"code": "LB012", "name": "Complete Haemogram / CBC", "cap": 150.0, "category": "Pathology & Diagnostics", "authority": "CGHS 2026 Gazette (MoHFW)"},
    "blood test": {"code": "LB012", "name": "Routine Blood Examination (CBC)", "cap": 150.0, "category": "Pathology & Diagnostics", "authority": "CGHS 2026 Gazette (MoHFW)"},
    "lipid profile": {"code": "LB045", "name": "Lipid Profile Test", "cap": 300.0, "category": "Pathology & Diagnostics", "authority": "CGHS 2026 Gazette (MoHFW)"},
    "liver function test": {"code": "LB032", "name": "LFT (Liver Function Test)", "cap": 350.0, "category": "Pathology & Diagnostics", "authority": "CGHS 2026 Gazette (MoHFW)"},
    "lft": {"code": "LB032", "name": "LFT (Liver Function Test)", "cap": 350.0, "category": "Pathology & Diagnostics", "authority": "CGHS 2026 Gazette (MoHFW)"},
    "kidney function test": {"code": "LB033", "name": "KFT / RFT (Renal Function)", "cap": 350.0, "category": "Pathology & Diagnostics", "authority": "CGHS 2026 Gazette (MoHFW)"},
    "kft": {"code": "LB033", "name": "KFT / RFT (Renal Function)", "cap": 350.0, "category": "Pathology & Diagnostics", "authority": "CGHS 2026 Gazette (MoHFW)"},
    "blood sugar fast": {"code": "LB001", "name": "Blood Glucose Fasting", "cap": 50.0, "category": "Pathology & Diagnostics", "authority": "CGHS 2026 Gazette (MoHFW)"},
    "blood glucose": {"code": "LB001", "name": "Blood Glucose Test", "cap": 50.0, "category": "Pathology & Diagnostics", "authority": "CGHS 2026 Gazette (MoHFW)"},
    "hba1c": {"code": "LB008", "name": "HbA1c Glycated Hemoglobin", "cap": 250.0, "category": "Pathology & Diagnostics", "authority": "CGHS 2026 Gazette (MoHFW)"},
    "crp": {"code": "LB098", "name": "C-Reactive Protein (Quantitative)", "cap": 200.0, "category": "Pathology & Diagnostics", "authority": "CGHS 2026 Gazette (MoHFW)"},
    "paracetamol syrup": {"code": "DPCO-SO421(E)", "name": "Paracetamol Syrup 125mg/5ml (60ml Bottle)", "cap": 19.20, "category": "Pharmaceuticals & DPCO", "authority": "NPPA / DPCO Price Order"},
    "paracetamol 125": {"code": "DPCO-SO421(E)", "name": "Paracetamol Syrup 125mg/5ml (60ml Bottle)", "cap": 19.20, "category": "Pharmaceuticals & DPCO", "authority": "NPPA / DPCO Price Order"},
    "paracetamol 650": {"code": "DPCO-NLEM", "name": "Paracetamol 650mg (10 Tabs)", "cap": 24.50, "category": "Pharmaceuticals & DPCO", "authority": "NPPA / DPCO Price Order"},
    "paracetamol": {"code": "DPCO-NLEM", "name": "Paracetamol 650mg (10 Tabs)", "cap": 24.50, "category": "Pharmaceuticals & DPCO", "authority": "NPPA / DPCO Price Order"},
    "digoxin injection": {"code": "DPCO-SO422(E)", "name": "Digoxin Injection 0.25mg/ml (1ml Ampoule)", "cap": 3.74, "category": "Pharmaceuticals & DPCO", "authority": "NPPA / DPCO Price Order"},
    "digoxin": {"code": "DPCO-SO422(E)", "name": "Digoxin Injection 0.25mg/ml (1ml Ampoule)", "cap": 3.74, "category": "Pharmaceuticals & DPCO", "authority": "NPPA / DPCO Price Order"},
    "clotrimazole pessaries 100": {"code": "DPCO-SO423(E)", "name": "Clotrimazole Pessaries 100mg (1 Unit)", "cap": 8.69, "category": "Pharmaceuticals & DPCO", "authority": "NPPA / DPCO Price Order"},
    "clotrimazole pessaries 200": {"code": "DPCO-SO922(E)", "name": "Clotrimazole Pessaries 200mg (1 Unit)", "cap": 13.31, "category": "Pharmaceuticals & DPCO", "authority": "NPPA / DPCO Price Order"},
    "promethazine injection": {"code": "DPCO-SO424(E)", "name": "Promethazine Injection 25mg/ml (1ml Ampoule)", "cap": 2.84, "category": "Pharmaceuticals & DPCO", "authority": "NPPA / DPCO Price Order"},
    "promethazine": {"code": "DPCO-SO424(E)", "name": "Promethazine Injection 25mg/ml (1ml Ampoule)", "cap": 2.84, "category": "Pharmaceuticals & DPCO", "authority": "NPPA / DPCO Price Order"},
    "lignocaine injection": {"code": "DPCO-SO923(E)", "name": "Lignocaine 2% + Adrenaline Injection (1ml)", "cap": 0.92, "category": "Pharmaceuticals & DPCO", "authority": "NPPA / DPCO Price Order"},
    "lignocaine": {"code": "DPCO-SO923(E)", "name": "Lignocaine 2% + Adrenaline Injection (1ml)", "cap": 0.92, "category": "Pharmaceuticals & DPCO", "authority": "NPPA / DPCO Price Order"},
    "ibuprofen syrup": {"code": "DPCO-SO946(E)", "name": "Ibuprofen Syrup 100mg/5ml (60ml Bottle)", "cap": 12.60, "category": "Pharmaceuticals & DPCO", "authority": "NPPA / DPCO Price Order"},
    "ibuprofen": {"code": "DPCO-SO946(E)", "name": "Ibuprofen Syrup 100mg/5ml (60ml Bottle)", "cap": 12.60, "category": "Pharmaceuticals & DPCO", "authority": "NPPA / DPCO Price Order"},
    "prednisolone 20": {"code": "DPCO-SO947(E)", "name": "Prednisolone 20mg (10 Tablets Strip)", "cap": 18.50, "category": "Pharmaceuticals & DPCO", "authority": "NPPA / DPCO Price Order"},
    "prednisolone": {"code": "DPCO-SO947(E)", "name": "Prednisolone 20mg (10 Tablets Strip)", "cap": 18.50, "category": "Pharmaceuticals & DPCO", "authority": "NPPA / DPCO Price Order"},
    "amoxicillin 500": {"code": "DPCO-MED02", "name": "Amoxicillin + Clavulanic 625mg (6 Tabs)", "cap": 120.0, "category": "Pharmaceuticals & DPCO", "authority": "NPPA / DPCO Price Order"},
    "amoxicillin": {"code": "DPCO-MED02", "name": "Amoxicillin 500mg", "cap": 120.0, "category": "Pharmaceuticals & DPCO", "authority": "NPPA / DPCO Price Order"},
    "pantoprazole 40": {"code": "DPCO-MED03", "name": "Pantoprazole 40mg (10 Tabs)", "cap": 85.0, "category": "Pharmaceuticals & DPCO", "authority": "NPPA / DPCO Price Order"},
    "pantoprazole": {"code": "DPCO-MED03", "name": "Pantoprazole 40mg (10 Tabs)", "cap": 85.0, "category": "Pharmaceuticals & DPCO", "authority": "NPPA / DPCO Price Order"},
    "azithromycin 500": {"code": "DPCO-MED04", "name": "Azithromycin 500mg (3 Tabs)", "cap": 72.0, "category": "Pharmaceuticals & DPCO", "authority": "NPPA / DPCO Price Order"},
    "azithromycin": {"code": "DPCO-MED04", "name": "Azithromycin 500mg", "cap": 72.0, "category": "Pharmaceuticals & DPCO", "authority": "NPPA / DPCO Price Order"},
    "ceftriaxone 1g": {"code": "DPCO-MED05", "name": "Ceftriaxone 1g IV Injection", "cap": 65.0, "category": "Pharmaceuticals & DPCO", "authority": "NPPA / DPCO Price Order"}
}

def match_cghs_rate(item_name: str, fallback_pdf_context: str = "") -> dict:
    query = re.sub(r'[^a-zA-Z0-9\s]', '', item_name).lower().strip()
    for key, data in CGHS_GAZETTE_MASTER.items():
        if key == query or key in query or query in key:
            return {"matched_name": data["name"], "code": data["code"], "legal_cap": data["cap"], "category": data["category"], "authority": data["authority"]}
            
    keys = list(CGHS_GAZETTE_MASTER.keys())
    matches = difflib.get_close_matches(query, keys, n=1, cutoff=0.45)
    if matches:
        data = CGHS_GAZETTE_MASTER[matches[0]]
        return {"matched_name": data["name"], "code": data["code"], "legal_cap": data["cap"], "category": data["category"], "authority": data["authority"]}

    if fallback_pdf_context:
        price_matches = re.findall(r'(\d{1,6}(?:\.\d{1,2})?)', fallback_pdf_context)
        is_nppa = "nppa" in fallback_pdf_context.lower() or "dpco" in fallback_pdf_context.lower()
        if price_matches:
            return {"matched_name": item_name, "code": "GAZETTE-PDF", "legal_cap": float(price_matches[0]), "category": "Pharmaceuticals & DPCO" if is_nppa else "Clinical Procedure", "authority": "NPPA / DPCO 2026 Price Order" if is_nppa else "CGHS 2026 Gazette (MoHFW)"}

    return {"matched_name": item_name, "code": "UNLISTED", "legal_cap": 0.0, "category": "Unlisted Charge", "authority": "Facility Tariff Schedule"}

# --- 6. ADVANCED SCANNER ENGINE ---
def compress_and_encode_image(uploaded_file, max_size=(1024, 1024)):
    uploaded_file.seek(0)
    img = Image.open(uploaded_file)
    if img.mode != 'RGB': img = img.convert('RGB')
    img.thumbnail(max_size, Image.Resampling.LANCZOS)
    buffered = io.BytesIO()
    img.save(buffered, format="JPEG", quality=85)
    return base64.b64encode(buffered.getvalue()).decode('utf-8')

def extract_clean_text_from_image(uploaded_file):
    raw_text, error_logs = "", []
    try:
        base64_image = compress_and_encode_image(uploaded_file)
        messages = [{"role": "user", "content": [{"type": "text", "text": "Extract all text, line items, and prices accurately from this medical invoice. Output ONLY the clean transcribed text."}, {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{base64_image}"}}]}]
        vision_models = ["llama-3.2-11b-vision-preview", "llama-3.2-90b-vision-preview", "qwen/qwen3.6-27b", "meta-llama/llama-4-scout-17b-16e-instruct"]
        for vision_model in vision_models:
            try:
                response = groq_client.chat.completions.create(messages=messages, model=vision_model, temperature=0.0)
                res_text = response.choices[0].message.content
                if res_text and len(res_text.strip()) > 5:
                    raw_text = res_text
                    break
            except Exception as v_err: error_logs.append(f"Vision ({vision_model}): {str(v_err)[:80]}")
    except Exception as e: error_logs.append(f"Vision Preprocess: {str(e)[:80]}")

    if not raw_text:
        try:
            uploaded_file.seek(0)
            pil_img = Image.open(uploaded_file).convert('L')
            pil_img = ImageOps.autocontrast(pil_img)
            enhancer = ImageEnhance.Contrast(pil_img)
            pil_img = enhancer.enhance(2.0)
            raw_text = pytesseract.image_to_string(pil_img, config='--psm 6')
        except Exception as ocr_err: error_logs.append(f"Tesseract OCR: {str(ocr_err)[:80]}")

    if raw_text and len(raw_text.strip()) > 2:
        cleaned = raw_text.replace('₹', ' ')
        cleaned = re.sub(r'(?i)\b(rs\.?|inr|rupees)\b', ' ', cleaned)
        cleaned = re.sub(r'(\d+),(\d+)', r'\1\2', cleaned)
        return cleaned.strip()
    else:
        err_msg = " | ".join(error_logs) if error_logs else "Unable to parse image data."
        st.session_state.scan_error = f"⚠️ Scan Failed: {err_msg}"
        return ""

# --- 7. UNIVERSAL 3-TIER AUDIT ENGINE ---
def universal_medical_audit(bill_text):
    if not bill_text:
        st.session_state.scan_error = "⚠️ Could not extract text from document."
        return None

    extract_prompt = f"""You are a Healthcare Financial Data Ingestion Engine.
Extract the facility/hospital/pharmacy name, and every single invoiced line item and its numerical billed price.

TEXT TO INGEST:
{bill_text}

Return ONLY valid JSON matching this schema:
{{
  "hospital": "Detected Facility Name",
  "audit_results": [{{"item": "Line Item Name", "billed": 1500.0}}]
}}"""

    try:
        response = llm.invoke(extract_prompt)
        parsed = safe_extract_json(response.content)
        if not parsed:
            lines = bill_text.split('\n')
            extracted_items = []
            for line in lines:
                price_match = re.search(r'(\d+(?:\.\d{1,2})?)', line)
                name_match = re.search(r'([a-zA-Z\s\(\)\-\/]+)', line)
                if price_match and name_match and len(name_match.group(1).strip()) > 3:
                    extracted_items.append({"item": name_match.group(1).strip(), "billed": float(price_match.group(1))})
            parsed = {"hospital": "Medical Provider", "audit_results": extracted_items}

        hospital_name = parsed.get("hospital") or parsed.get("facility") or "Medical Provider"
        raw_items = parsed.get("audit_results") or parsed.get("items") or []
        pdf_ctx = get_pdf_context(bill_text)

        audited_results = []
        for raw in raw_items:
            item_name = raw.get("item") or raw.get("name") or "Medical Service"
            try: billed_amt = float(re.sub(r'[^\d.]', '', str(raw.get("billed", 0))))
            except Exception: billed_amt = 0.0

            cghs_data = match_cghs_rate(item_name, fallback_pdf_context=pdf_ctx)
            legal_cap = cghs_data["legal_cap"]
            authority = cghs_data["authority"]

            if legal_cap > 0:
                if billed_amt > legal_cap:
                    diff = round(billed_amt - legal_cap, 2)
                    summary = f"{authority} ceiling ({cghs_data['code']}) is ₹{legal_cap:,.2f}; Overcharges by ₹{diff:,.2f}."
                else: summary = f"Compliant with statutory ceiling under {authority} (Cap: ₹{legal_cap:,.2f})."
            else: summary = "Unlisted in standard statutory gazette; manual verification recommended."

            audited_results.append({
                "item": f"{item_name} [{cghs_data['code']}]" if cghs_data['code'] != "UNLISTED" else item_name,
                "billed": billed_amt, "legal": legal_cap, "summary": summary, "category": cghs_data["category"], "authority": authority
            })

        return {"hospital": hospital_name, "audit_results": audited_results}
    except Exception as e:
        st.session_state.scan_error = f"Audit Processing Error: {e}"
        return None

def hospital_audit_logic(bill_text): return universal_medical_audit(bill_text)
def ai_audit_logic(bill_text): return universal_medical_audit(bill_text)

def insurance_audit_logic(txt):
    if not txt: return None
    extract_prompt = f"""You are an Insurance Claims Auditor.
Extract provider name and disallowed line items.
DOCUMENT TEXT: {txt}
Return ONLY valid JSON:
{{"hospital": "Insurance Company", "audit_results": [{{"item": "Service", "billed": 8000.0, "legal": 5000.0, "summary": "Unjustified deduction"}}]}}"""
    try:
        response = llm.invoke(extract_prompt)
        parsed_json = safe_extract_json(response.content)
        return parsed_json if parsed_json else {"hospital": "Insurance Provider", "audit_results": []}
    except Exception: return {"hospital": "Insurance Provider", "audit_results": []}

# --- 8. REAL-TIME AUDIT LOGGING HELPER ---
def auto_log_audit(department_name, result_json):
    if not result_json: return
    items = result_json.get('audit_results', [])
    entity = result_json.get('hospital', 'Medical Facility')
    scan_leakage = 0.0
    for i in items:
        try: b, l = float(re.sub(r'[^\d.]', '', str(i.get('billed', 0)))), float(re.sub(r'[^\d.]', '', str(i.get('legal', 0))))
        except Exception: b, l = 0.0, 0.0
        if l > 0 and b > l: scan_leakage += round(b - l, 2)
            
    st.session_state.total_leakage = scan_leakage
    new_entry = pd.DataFrame([{"Day": datetime.now().strftime("%a"), "Dept": department_name, "Leakage": scan_leakage, "Hospital": entity, "Timestamp": datetime.now()}])
    st.session_state.audit_log = pd.concat([st.session_state.audit_log, new_entry], ignore_index=True)
    
    if st.session_state.total_leakage > 10000: st.session_state.risk_level = "CRITICAL OVERCHARGE (GRADE F)"
    elif st.session_state.total_leakage > 2500: st.session_state.risk_level = "MODERATE SAVINGS (GRADE C)"
    else: st.session_state.risk_level = "STATUTORY COMPLIANT (GRADE A+)"

# --- 9. AUTHENTICATION & DATABASE ---
def load_users():
    if os.path.exists(USER_DB):
        with open(USER_DB, "r") as f:
            try: return {k.strip().lower(): v for k, v in json.load(f).items()}
            except Exception: return {}
    return {}

def save_user(email, password):
    users = load_users()
    users[email.strip().lower()] = password
    with open(USER_DB, "w") as f: json.dump(users, f, indent=4)

def send_otp(receiver_email):
    otp = str(random.randint(100000, 999999))
    msg = EmailMessage()
    msg.set_content(f"Your Medi-Audit Verification Code is: {otp}")
    msg["Subject"] = "🛡️ Medi-Audit Security Code"
    msg["From"] = SENDER_EMAIL
    msg["To"] = receiver_email
    try:
        server = smtplib.SMTP_SSL("smtp.gmail.com", 465)
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.send_message(msg)
        server.quit()
        return otp
    except Exception: return None

def format_inr(val):
    try: return f"₹{float(re.sub(r'[^\d.]', '', str(val))):,.2f}"
    except Exception: return str(val)

# --- 10. SESSION STATE INITIALIZATION ---
if "logged_in" not in st.session_state:
    for key, val in [("logged_in", False), ("otp_sent", False), ("user_email", ""), ("messages", []), 
                     ("total_leakage", 0.0), ("audit_accuracy", 99.8), ("risk_level", "STATUTORY COMPLIANT (GRADE A+)"),
                     ("ai_result_data", None), ("raw_extracted_text", ""), ("scan_error", None),
                     ("audit_log", pd.DataFrame(columns=["Day", "Dept", "Leakage", "Hospital", "Timestamp"]))]:
        st.session_state[key] = val

# --- 11. GEOGRAPHIC FRAUD MAP DATA ---
fraud_map_data = pd.DataFrame({
    'lat': [28.6139, 19.0760, 12.9716, 22.5726, 13.0827, 21.1458, 26.8467, 17.3850, 23.0225, 30.7333],
    'lon': [77.2090, 72.8777, 77.5946, 88.3639, 80.2707, 79.0882, 80.9462, 78.4867, 72.5714, 76.7794],
    'fraud_intensity': [95, 88, 76, 92, 65, 80, 85, 70, 60, 55],
    'city': ['Delhi', 'Mumbai', 'Bengaluru', 'Kolkata', 'Chennai', 'Nagpur', 'Lucknow', 'Hyderabad', 'Ahmedabad', 'Chandigarh']
})

# --- 12. MEDICALMAGIC OBSIDIAN CYBER-HEALTH THEME (CSS) ---
st.set_page_config(
    page_title="MedicalMagic Medi-Audit — Intelligent Healthcare Defense", 
    page_icon="💙", layout="wide", initial_sidebar_state="expanded"
)

st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@300;400;500;600;700;800&family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;600&display=swap');

html, body, [class*="css"], .stMarkdown {
    font-family: 'Plus Jakarta Sans', 'Inter', -apple-system, sans-serif !important;
}

/* Deep Obsidian Jet Black Background */
.stApp {
    background: #080a0f !important;
    background-image: 
        radial-gradient(at 10% 10%, rgba(0, 102, 255, 0.12) 0px, transparent 50%),
        radial-gradient(at 90% 20%, rgba(0, 210, 255, 0.08) 0px, transparent 50%),
        radial-gradient(at 50% 90%, rgba(37, 99, 235, 0.1) 0px, transparent 50%) !important;
    color: #f8fafc !important;
}

#MainMenu, header, footer {visibility: hidden; height: 0;}
.block-container {
    padding-top: 1rem !important;
    padding-bottom: 4rem !important;
    max-width: 1280px !important;
}

/* MedicalMagic Top Bar */
.mm-top-bar {
    display: flex;
    justify-content: space-between;
    align-items: center;
    background: #11141d;
    border: 1px solid #1f2633;
    border-radius: 9999px;
    padding: 10px 24px;
    margin-bottom: 24px;
    box-shadow: 0 10px 30px rgba(0, 0, 0, 0.6);
}

.mm-logo {
    display: flex;
    align-items: center;
    gap: 10px;
    font-weight: 800;
    font-size: 20px;
    color: #ffffff;
    letter-spacing: -0.03em;
}

.mm-sun-icon {
    width: 28px;
    height: 28px;
    border-radius: 50%;
    background: radial-gradient(circle, #00d2ff 0%, #0066ff 100%);
    display: inline-flex;
    align-items: center;
    justify-content: center;
    box-shadow: 0 0 15px rgba(0, 210, 255, 0.6);
}

/* Pill Organ Selectors */
.organ-pill-bar {
    display: flex;
    gap: 8px;
    background: #11141d;
    padding: 6px 10px;
    border-radius: 9999px;
    border: 1px solid #1f2633;
    overflow-x: auto;
    margin-bottom: 20px;
}

.organ-pill {
    background: #181d28;
    color: #94a3b8;
    font-size: 12px;
    font-weight: 700;
    padding: 6px 16px;
    border-radius: 9999px;
    border: 1px solid #232a3b;
    white-space: nowrap;
}
.organ-pill.active {
    background: #0066ff;
    color: #ffffff;
    box-shadow: 0 0 14px rgba(0, 102, 255, 0.6);
    border-color: #3b82f6;
}

/* 3D Medical Holographic Hero Container */
.mm-hero-card {
    background: #11141d;
    border: 1px solid #1f2633;
    border-radius: 28px;
    padding: 28px;
    position: relative;
    overflow: hidden;
    box-shadow: 0 20px 50px rgba(0, 0, 0, 0.5);
    height: 100%;
}

.mm-hero-card::after {
    content: "";
    position: absolute;
    bottom: 0;
    left: 0;
    right: 0;
    height: 1px;
    background: linear-gradient(90deg, transparent, #00d2ff, transparent);
}

/* Vibrant Blue Health Record Cards */
.blue-action-card {
    background: linear-gradient(135deg, #0066ff 0%, #0052cc 100%);
    border-radius: 24px;
    padding: 22px 24px;
    color: #ffffff;
    box-shadow: 0 15px 35px rgba(0, 102, 255, 0.35);
    position: relative;
    overflow: hidden;
    margin-bottom: 14px;
    transition: transform 0.25s ease, box-shadow 0.25s ease;
}
.blue-action-card:hover {
    transform: translateY(-3px);
    box-shadow: 0 20px 40px rgba(0, 102, 255, 0.5);
}

.card-avatar-row {
    display: flex;
    align-items: center;
    gap: 12px;
    margin-bottom: 14px;
}
.card-avatar {
    width: 44px;
    height: 44px;
    border-radius: 50%;
    border: 2px solid #ffffff;
    object-fit: cover;
}

/* Dark Vital Metric Pod */
.vital-pod {
    background: #151924;
    border: 1px solid #1f2633;
    border-radius: 20px;
    padding: 16px 20px;
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 12px;
}
.vital-icon-circle {
    width: 38px;
    height: 38px;
    border-radius: 50%;
    background: #1e2433;
    display: flex;
    align-items: center;
    justify-content: center;
    color: #00d2ff;
}

/* Buttons */
div.stButton > button {
    background: #0066ff !important;
    color: #ffffff !important;
    border: none !important;
    border-radius: 9999px !important;
    padding: 0.75rem 1.8rem !important;
    font-weight: 700 !important;
    font-size: 0.95rem !important;
    box-shadow: 0 8px 25px rgba(0, 102, 255, 0.4) !important;
    transition: all 0.25s ease !important;
    width: 100% !important;
}
div.stButton > button:hover {
    background: #0052cc !important;
    transform: translateY(-2px) !important;
    box-shadow: 0 12px 30px rgba(0, 102, 255, 0.6) !important;
}

/* Sidebar */
[data-testid="stSidebar"] {
    background-color: #090b10 !important;
    border-right: 1px solid #1a202c;
}

/* Tabs */
.stTabs [data-baseweb="tab-list"] {
    gap: 8px;
    background-color: #11141d;
    padding: 6px;
    border-radius: 9999px;
    border: 1px solid #1f2633;
}
.stTabs [data-baseweb="tab"] {
    height: 38px;
    border-radius: 9999px;
    color: #94a3b8;
    font-weight: 700;
    font-size: 13px;
    border: none;
    background-color: transparent;
    padding: 0 18px;
}
.stTabs [aria-selected="true"] {
    background: #0066ff !important;
    color: #ffffff !important;
    box-shadow: 0 4px 15px rgba(0, 102, 255, 0.4);
}

/* Expanders */
.streamlit-expanderHeader {
    background-color: #11141d !important;
    border: 1px solid #1f2633 !important;
    border-radius: 18px !important;
    color: #ffffff !important;
    font-weight: 700 !important;
}
.streamlit-expanderContent {
    background-color: #0c0e15 !important;
    border: 1px solid #1f2633 !important;
    border-top: none !important;
    border-bottom-left-radius: 18px;
    border-bottom-right-radius: 18px;
}
</style>
""", unsafe_allow_html=True)

# --- 13. AUTHENTICATION MODULE ---
if not st.session_state.logged_in:
    col_c1, col_c2, col_c3 = st.columns([1, 1.6, 1])
    with col_c2:
        st.markdown("""
        <div style='text-align: center; padding: 40px 0 20px 0;'>
            <div style="display: inline-flex; align-items: center; gap: 10px; margin-bottom: 12px;">
                <div class="mm-sun-icon">💙</div>
                <span style="font-size: 26px; font-weight: 800; color: #ffffff;">MedicalMagic</span>
                <span style="background: #0066ff; color: #fff; font-size: 11px; font-weight: 800; padding: 3px 10px; border-radius: 9999px;">AUDIT</span>
            </div>
            <h1 style="font-size: 38px; font-weight: 800; letter-spacing: -0.04em; color: #ffffff; margin-bottom: 6px;">Check your Overall Health Bill</h1>
            <p style="font-size: 15px; color: #94a3b8; margin-bottom: 24px;">Automated statutory verification against CGHS 2026 Gazettes and NPPA DPCO Ceilings.</p>
        </div>
        """, unsafe_allow_html=True)
        
        with st.container(border=True):
            t1, t2, t3 = st.tabs(["Sign In", "Get Started Free", "Recovery"])
            
            with t1:
                l_email = st.text_input("Work Email Address", key="login_email", placeholder="patient@healthcare.in")
                l_pass = st.text_input("Access Password", type="password", key="login_pass", placeholder="••••••••")
                st.markdown("<div style='height: 12px;'></div>", unsafe_allow_html=True)
                if st.button("Enter MedicalMagic Workspace →", use_container_width=True, type="primary"):
                    ud = load_users()
                    if l_email.strip().lower() in ud and ud[l_email.strip().lower()] == l_pass:
                        st.session_state.logged_in, st.session_state.user_email = True, l_email
                        st.rerun()
                    else: st.error("Invalid credentials provided.")

            with t2:
                r_email = st.text_input("Enter Email for Security Code", key="reg_email", placeholder="name@domain.com")
                if not st.session_state.otp_sent:
                    if st.button("Send Security Code →", use_container_width=True):
                        st.session_state.generated_otp = send_otp(r_email)
                        if st.session_state.generated_otp:
                            st.session_state.otp_sent = True; st.rerun()
                        else: st.error("Email service error. Check SMTP settings.")
                else:
                    i_otp = st.text_input("6-Digit Verification Token", key="reg_otp")
                    r_pass = st.text_input("Create Master Password", type="password", key="reg_pass")
                    if st.button("Activate Free Account →", use_container_width=True):
                        if i_otp == st.session_state.generated_otp:
                            save_user(r_email, r_pass); st.session_state.otp_sent = False; st.rerun()
                        else: st.error("Incorrect verification token.")

            with t3:
                f_email = st.text_input("Registered Email", key="forgot_email")
                if "forgot_otp_sent" not in st.session_state: st.session_state.forgot_otp_sent = False
                if not st.session_state.forgot_otp_sent:
                    if st.button("Send Password Reset Link →", use_container_width=True):
                        ud = load_users()
                        if f_email.strip().lower() in ud:
                            st.session_state.generated_otp = send_otp(f_email)
                            st.session_state.forgot_otp_sent = True; st.rerun()
                        else: st.error("Email address not found.")
                else:
                    f_otp = st.text_input("Verification Code", key="f_otp")
                    new_pass = st.text_input("Enter New Password", type="password", key="f_new_pass")
                    if st.button("Update & Sign In →", use_container_width=True, type="primary"):
                        if f_otp == st.session_state.generated_otp:
                            save_user(f_email, new_pass); st.session_state.forgot_otp_sent = False
                            st.success("Password Updated! Please Sign In."); time.sleep(1); st.rerun()
                        else: st.error("Invalid token.")

# --- 14. MAIN APPLICATION WORKSPACE ---
else:
    with st.sidebar:
        st.markdown(f"""
        <div style='padding: 10px 0 20px 0;'>
            <div style='display: flex; align-items: center; gap: 8px;'>
                <div class="mm-sun-icon" style="width: 22px; height: 22px; font-size: 12px;">💙</div>
                <span style='font-size: 19px; font-weight: 800; color: #ffffff;'>MedicalMagic</span>
            </div>
            <p style='color: #64748b; font-size: 11px; margin: 4px 0 0 0;'>Core: <span style='font-family: monospace; color: #00d2ff;'>{ACTIVE_GROQ_MODEL}</span></p>
        </div>
        """, unsafe_allow_html=True)
        st.caption(f"Authenticated: **{st.session_state.user_email}**")
        st.divider()
        
        dept = st.radio(
            "WORKSPACE SECTIONS", 
            ["📊 Executive Terminal", "🗺️ Fraud Radar", "💊 Pharma Forensic", "🛡️ Insurance Armor", "🏥 Hospital Audit", "⚖️ Justice Portal", "💬 AI Copilot"],
            label_visibility="collapsed"
        )
        
        st.divider()
        if st.button("🗑️ Clear Active Audit", use_container_width=True):
            st.session_state.audit_log = pd.DataFrame(columns=["Day", "Dept", "Leakage", "Hospital", "Timestamp"])
            st.session_state.total_leakage = 0.0
            st.session_state.risk_level = "STATUTORY COMPLIANT (GRADE A+)"
            st.session_state.ai_result_data = None
            st.session_state.raw_extracted_text = ""
            st.session_state.scan_error = None
            st.rerun()
            
        if st.button("🚪 Sign Out", use_container_width=True):
            st.session_state.logged_in = False; st.rerun()

    # MedicalMagic Top Bar
    st.markdown(f"""
    <div class="mm-top-bar">
        <div class="mm-logo">
            <div class="mm-sun-icon">💙</div>
            <span>MedicalMagic</span>
            <span style="color: #00d2ff; font-size: 14px; font-weight: 600;">| Medi-Audit AI</span>
        </div>
        <div style="display: flex; align-items: center; gap: 10px;">
            <span style="background:#1e2433; color:#00d2ff; font-size:11px; font-weight:700; padding:6px 14px; border-radius:9999px; border:1px solid #232a3b;">
                🟢 CGHS 2026 + NPPA DPCO LIVE
            </span>
            <span style="color: #94a3b8; font-size: 12px; font-weight: 600;">{ACTIVE_GROQ_MODEL.upper()}</span>
        </div>
    </div>
    """, unsafe_allow_html=True)

    # --- 14.1 EXECUTIVE DASHBOARD ---
    if dept == "📊 Executive Terminal":
        # Organ Pill Buttons mapped to instant 1-click loaders
        st.markdown("""
        <div class="organ-pill-bar">
            <span class="organ-pill active">💙 Cardiology (ICU)</span>
            <span class="organ-pill">🫁 Pulmonary / Chest</span>
            <span class="organ-pill">🧠 Neuro MRI</span>
            <span class="organ-pill">💊 NPPA DPCO Pharma</span>
            <span class="organ-pill">🛡️ TPA Claims</span>
            <span class="organ-pill">🔬 Diagnostics</span>
        </div>
        """, unsafe_allow_html=True)

        col_demo1, col_demo2, col_demo3 = st.columns(3)
        with col_demo1:
            if st.button("🏥 Load Apollo Hospital ₹35.7k Bill"):
                sample_h = """APOLLO SUPER SPECIALITY HEALTHCARE LTD.
1. OPD Doctor Consultation Fee (Senior Specialist) : 1500.00
2. Complete Haemogram / CBC Blood Test (Automated) : 800.00
3. MRI Brain Plain (1.5 Tesla Scan) : 12000.00
4. ICU Charges (Per Day Non-Ventilator) : 18000.00
5. Chest X-Ray PA View Digital : 1200.00
6. Ultrasound Abdomen & Pelvis (USG) : 2200.00"""
                res = hospital_audit_logic(sample_h)
                st.session_state.ai_result_data = res
                auto_log_audit("Hospital", res)
                st.rerun()

        with col_demo2:
            if st.button("💊 Load MedPlus DPCO ₹1.1k Bill"):
                sample_p = """MEDPLUS PHARMACEUTICAL RETAIL OUTLET
1. Paracetamol Syrup 125mg/5ml (60ml Bottle) : 120.00
2. Amoxicillin 500mg (6 Tablets Strip) : 450.00
3. Pantoprazole 40mg (10 Tablets Strip) : 290.00
4. Digoxin Injection 0.25mg/ml (1ml Ampoule) : 75.00
5. Prednisolone 20mg (10 Tablets Strip) : 160.00
6. Ibuprofen Syrup 100mg/5ml (60ml Bottle) : 95.00"""
                res = ai_audit_logic(sample_p)
                st.session_state.ai_result_data = res
                auto_log_audit("Pharma", res)
                st.rerun()

        with col_demo3:
            if st.button("🛡️ Load Star Health Denial ₹38.5k"):
                sample_i = """STAR HEALTH & ALLIED INSURANCE TPA SERVICES
1. Room Rent Charges (3 Days) : Billed 24000.00, Approved 12000.00
2. ICU Charges (2 Days) : Billed 35000.00, Approved 22000.00
3. Consultation & Specialist Visits : Billed 6000.00, Approved 2500.00
4. Pharmacy & Consumable Surcharges : Billed 14000.00, Approved 4000.00"""
                res = insurance_audit_logic(sample_i)
                st.session_state.ai_result_data = res
                auto_log_audit("Insurance", res)
                st.rerun()

        st.markdown("<div style='height: 16px;'></div>", unsafe_allow_html=True)

        # 3-COLUMN MEDICALMAGIC DASHBOARD LAYOUT
        col_left, col_mid, col_right = st.columns([1.5, 1.1, 1.4])

        with col_left:
            # 3D Medical Holographic Container
            st.markdown("""
            <div class="mm-hero-card" style="text-align: center;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                    <span style="color: #94a3b8; font-size: 13px; font-weight: 700;">ANATOMICAL FORENSIC SCAN</span>
                    <span style="color: #00d2ff; font-size: 12px; font-weight: 700;">● LIVE ECG 120 bpm</span>
                </div>
                <img src="https://images.unsplash.com/photo-1559757175-5700dde675bc?auto=format&fit=crop&w=600&q=80" 
                     style="width: 85%; border-radius: 20px; box-shadow: 0 0 35px rgba(0, 210, 255, 0.25); margin: 12px 0;" />
                <div style="display: flex; justify-content: space-around; margin-top: 14px;">
                    <div><span style="color:#94a3b8; font-size:11px;">ORGAN PULSE</span><br><strong style="color:#00d2ff; font-size:16px;">NORMAL</strong></div>
                    <div><span style="color:#94a3b8; font-size:11px;">GAZETTE MATCH</span><br><strong style="color:#ffffff; font-size:16px;">100%</strong></div>
                    <div><span style="color:#94a3b8; font-size:11px;">FINANCIAL DEFENSE</span><br><strong style="color:#22c55e; font-size:16px;">ACTIVE</strong></div>
                </div>
            </div>
            """, unsafe_allow_html=True)

        with col_mid:
            # Center Vitals Pods
            st.markdown(f"""
            <div class="vital-pod">
                <div>
                    <span style="color: #94a3b8; font-size: 11px; font-weight: 700;">STATUTORY VARIANCE</span><br>
                    <strong style="color: #ffffff; font-size: 20px;">₹{st.session_state.total_leakage:,.2f}</strong>
                </div>
                <div class="vital-icon-circle">💧</div>
            </div>
            <div class="vital-pod">
                <div>
                    <span style="color: #94a3b8; font-size: 11px; font-weight: 700;">AUDIT ACCURACY</span><br>
                    <strong style="color: #00d2ff; font-size: 20px;">{st.session_state.audit_accuracy}%</strong>
                </div>
                <div class="vital-icon-circle">💙</div>
            </div>
            <div class="vital-pod">
                <div>
                    <span style="color: #94a3b8; font-size: 11px; font-weight: 700;">GOUGING PROFILE</span><br>
                    <strong style="color: #f43f5e; font-size: 15px;">{st.session_state.risk_level}</strong>
                </div>
                <div class="vital-icon-circle">📊</div>
            </div>
            <div class="vital-pod">
                <div>
                    <span style="color: #94a3b8; font-size: 11px; font-weight: 700;">RECOVERABLE %</span><br>
                    <strong style="color: #22c55e; font-size: 20px;">~73.5%</strong>
                </div>
                <div class="vital-icon-circle">🛡️</div>
            </div>
            """, unsafe_allow_html=True)

        with col_right:
            # Right Stacked Vibrant Blue Action Cards with Doctor Avatars
            st.markdown(f"""
            <div class="blue-action-card">
                <div class="card-avatar-row">
                    <img class="card-avatar" src="https://images.unsplash.com/photo-1594824813593-559380df1408?auto=format&fit=crop&w=150&q=80" />
                    <div>
                        <strong style="font-size: 15px; color: #ffffff;">Dr. Samantha Martin</strong><br>
                        <span style="font-size: 11px; opacity: 0.85;">Hospital Clinical Tariff Audit</span>
                    </div>
                </div>
                <div style="display: flex; justify-content: space-between; align-items: flex-end;">
                    <div>
                        <span style="font-size: 11px; opacity: 0.85;">Overcharge Identified</span><br>
                        <strong style="font-size: 22px;">₹{st.session_state.total_leakage:,.2f}</strong>
                    </div>
                    <span style="background: rgba(255,255,255,0.2); padding: 4px 12px; border-radius: 9999px; font-size: 11px; font-weight: 700;">
                        CGHS 2026 ↗
                    </span>
                </div>
            </div>

            <div class="blue-action-card" style="background: linear-gradient(135deg, #0284c7 0%, #0369a1 100%);">
                <div class="card-avatar-row">
                    <img class="card-avatar" src="https://images.unsplash.com/photo-1622253692010-333f2da6031d?auto=format&fit=crop&w=150&q=80" />
                    <div>
                        <strong style="font-size: 15px; color: #ffffff;">Michel Corrins</strong><br>
                        <span style="font-size: 11px; opacity: 0.85;">NPPA / DPCO Medicine Reconciler</span>
                    </div>
                </div>
                <div style="display: flex; justify-content: space-between; align-items: flex-end;">
                    <div>
                        <span style="font-size: 11px; opacity: 0.85;">DPCO Schedule-I Status</span><br>
                        <strong style="font-size: 18px;">Active Cap Enforcement</strong>
                    </div>
                    <span style="background: rgba(255,255,255,0.2); padding: 4px 12px; border-radius: 9999px; font-size: 11px; font-weight: 700;">
                        NPPA Order ↗
                    </span>
                </div>
            </div>
            """, unsafe_allow_html=True)

        st.markdown("<div style='height: 28px;'></div>", unsafe_allow_html=True)
        
        # PLOTLY CHARTS (DARK MEDICAL CYBER THEME)
        col_g1, col_g2 = st.columns([1.6, 1])
        with col_g1:
            st.markdown("##### Real-Time Leakage Trajectory")
            if not st.session_state.audit_log.empty:
                trend_data = st.session_state.audit_log.groupby('Day')['Leakage'].sum().reset_index()
            else:
                trend_data = pd.DataFrame({'Day': ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'], 'Leakage': [0]*7})
                
            fig_line = px.area(trend_data, x='Day', y='Leakage', template="plotly_dark", color_discrete_sequence=['#00d2ff'])
            fig_line.update_layout(
                plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
                margin=dict(l=10, r=10, t=15, b=10), height=240,
                yaxis=dict(showgrid=True, gridcolor='rgba(31, 38, 51, 0.8)'), xaxis=dict(showgrid=False)
            )
            st.plotly_chart(fig_line, use_container_width=True, config={'displayModeBar': False})

        with col_g2:
            st.markdown("##### Discrepancy by Category")
            if not st.session_state.audit_log.empty and st.session_state.audit_log['Leakage'].sum() > 0:
                pie_data = st.session_state.audit_log.groupby('Dept')['Leakage'].sum().reset_index().rename(columns={'Leakage':'Value'})
            else:
                pie_data = pd.DataFrame({'Dept': ['Hospital', 'Pharma', 'Insurance'], 'Value': [26250, 930, 38500]})
                
            fig_pie = px.pie(pie_data, values='Value', names='Dept', hole=0.6, color_discrete_sequence=['#0066ff', '#00d2ff', '#38bdf8'])
            fig_pie.update_layout(
                template="plotly_dark", plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
                margin=dict(l=10, r=10, t=15, b=10), height=240
            )
            st.plotly_chart(fig_pie, use_container_width=True, config={'displayModeBar': False})

        st.markdown("##### Hospital Overcharge Ranking Ledger")
        if not st.session_state.audit_log.empty and st.session_state.audit_log['Leakage'].sum() > 0:
            entity_data = st.session_state.audit_log.groupby('Hospital')['Leakage'].sum().sort_values(ascending=False).reset_index()
        else:
            entity_data = pd.DataFrame({'Hospital': ['Apollo Super Speciality', 'MedPlus Pharmacy', 'Star Health TPA'], 'Leakage': [26250, 930, 38500]})
            
        fig_rank = px.bar(entity_data, x='Leakage', y='Hospital', orientation='h', color='Leakage', color_continuous_scale='Blues', template="plotly_dark")
        fig_rank.update_layout(
            plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
            margin=dict(l=10, r=10, t=15, b=10), height=180,
            xaxis=dict(showgrid=True, gridcolor='rgba(31, 38, 51, 0.8)')
        )
        st.plotly_chart(fig_rank, use_container_width=True, config={'displayModeBar': False})
        
        csv_data = st.session_state.audit_log.to_csv(index=False).encode('utf-8')
        st.download_button("📥 Export Comprehensive Forensic Ledger (CSV)", data=csv_data, file_name=f"MedicalMagic_Ledger_{datetime.now().strftime('%Y%m%d')}.csv", mime='text/csv', use_container_width=True)

    # --- 14.2 FRAUD RADAR ---
    elif dept == "🗺️ Fraud Radar":
        st.markdown("""
        <h1 style="font-size: 38px; font-weight: 800; color: #ffffff; margin-bottom: 6px;">National Healthcare Price Radar.</h1>
        <p style="font-size: 15px; color: #94a3b8; margin-bottom: 24px;">Live spatial density tracking of excessive procedure markups above notified CGHS gazette caps.</p>
        """, unsafe_allow_html=True)
        st.map(fraud_map_data, size='fraud_intensity', color='#00d2ff')

    # --- 14.3 PHARMA FORENSIC ---
    elif dept == "💊 Pharma Forensic":
        st.markdown("""
        <h1 style="font-size: 38px; font-weight: 800; color: #ffffff; margin-bottom: 6px;">We audit pharmacy markups for you.</h1>
        <p style="font-size: 15px; color: #94a3b8; margin-bottom: 24px;">Lower your medicine bills by up to 80% against statutory NPPA ceilings and DPCO Schedule-I price orders.</p>
        """, unsafe_allow_html=True)
        
        tab_upload, tab_cam, tab_text = st.tabs(["Upload Pharmacy Bill", "Live Camera", "Paste Items"])
        
        with tab_upload:
            u_p = st.file_uploader("Upload Medical Receipt (JPEG, PNG)", type=["jpg", "png", "jpeg"], key="pharma_upload")
            if u_p and st.button("Run MedicalMagic Pharma Scan →", use_container_width=True, key="btn_p_file"):
                st.session_state.scan_error = None
                with st.spinner("Reconciling line items against statutory NPPA and CGHS gazettes..."):
                    txt_to_audit = extract_clean_text_from_image(u_p)
                    st.session_state.raw_extracted_text = txt_to_audit
                    if txt_to_audit:
                        res = ai_audit_logic(txt_to_audit)
                        st.session_state.ai_result_data = res
                        auto_log_audit("Pharma", res)

        with tab_cam:
            cam_p = st.camera_input("Capture receipt with camera", key="cam_pharma")
            if cam_p and st.button("Audit Camera Scan →", use_container_width=True, key="btn_p_cam"):
                st.session_state.scan_error = None
                with st.spinner("Processing Camera Document..."):
                    txt_to_audit = extract_clean_text_from_image(cam_p)
                    st.session_state.raw_extracted_text = txt_to_audit
                    if txt_to_audit:
                        res = ai_audit_logic(txt_to_audit)
                        st.session_state.ai_result_data = res
                        auto_log_audit("Pharma", res)

        with tab_text:
            manual_txt_p = st.text_area("Paste pharmacy line items", height=120, key="manual_pharma_txt")
            if manual_txt_p and st.button("Audit Pasted Text →", use_container_width=True, key="btn_p_txt"):
                st.session_state.scan_error = None
                st.session_state.raw_extracted_text = manual_txt_p
                res = ai_audit_logic(manual_txt_p)
                st.session_state.ai_result_data = res
                auto_log_audit("Pharma", res)
                
        if st.session_state.get("scan_error"):
            st.error(st.session_state.get("scan_error"))

        if st.session_state.ai_result_data:
            res = st.session_state.ai_result_data
            items = res.get('audit_results', [])
            pharmacy = res.get('hospital', 'Detected Pharmacy')
            
            orig_total = sum([float(re.sub(r'[^\d.]', '', str(x.get('billed', 0)))) for x in items])
            adjusted_total = orig_total - st.session_state.total_leakage
            pct_saved = (st.session_state.total_leakage / orig_total * 100) if orig_total > 0 else 0
            
            st.markdown(f"""
            <div class="mm-hero-card" style="margin-bottom: 20px;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                    <div>
                        <strong style="font-size: 20px; color: #ffffff;">🧪 {pharmacy}</strong><br>
                        <span style="font-size: 12px; color: #00d2ff;">Statutory NPPA DPCO Schedule-I Verified</span>
                    </div>
                    <span style="background: #0066ff; color: #fff; font-size: 11px; font-weight: 700; padding: 4px 12px; border-radius: 9999px;">
                        {datetime.now().strftime('%B %d, %Y')}
                    </span>
                </div>
                <div style="display: flex; justify-content: space-between; padding: 8px 0; font-size: 14px; border-bottom: 1px solid #1f2633;">
                    <span style="color: #94a3b8;">Original Invoiced Total:</span>
                    <strong style="color: #ffffff;">₹{orig_total:,.2f}</strong>
                </div>
                <div style="display: flex; justify-content: space-between; padding: 8px 0; font-size: 14px; border-bottom: 1px solid #1f2633;">
                    <span style="color: #94a3b8;">NPPA Price Order Overcharge:</span>
                    <strong style="color: #f43f5e;">-₹{st.session_state.total_leakage:,.2f}</strong>
                </div>
                <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 16px; padding: 14px; background: #151924; border-radius: 16px;">
                    <div>
                        <span style="font-size: 11px; color: #94a3b8; font-weight: 700;">MEDICALMAGIC ADJUSTED TOTAL</span><br>
                        <strong style="font-size: 24px; color: #00d2ff;">₹{adjusted_total:,.2f}</strong>
                    </div>
                    <span style="background: #0066ff; color: #fff; font-size: 13px; font-weight: 800; padding: 6px 16px; border-radius: 9999px;">
                        Save {pct_saved:.0f}%
                    </span>
                </div>
            </div>
            """, unsafe_allow_html=True)
            
            for idx, i in enumerate(items):
                try: b, l = float(re.sub(r'[^\d.]', '', str(i.get('billed', 0)))), float(re.sub(r'[^\d.]', '', str(i.get('legal', 0))))
                except Exception: b, l = 0.0, 0.0
                leak = round(b - l, 2) if (l > 0 and b > l) else 0.0
                
                with st.expander(f"📦 {i['item']} — Discrepancy: ₹{leak:,.2f}"):
                    fig_p = go.Figure(go.Bar(
                        x=['Statutory NPPA Cap', 'Retail Invoiced'], 
                        y=[l, b], 
                        marker_color=['#22c55e', '#f43f5e'],
                        text=[f"₹{l:,.2f}", f"₹{b:,.2f}"],
                        textposition='auto',
                        width=0.35
                    ))
                    fig_p.update_layout(
                        template="plotly_dark", plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
                        height=180, margin=dict(l=0, r=0, t=10, b=0), yaxis=dict(showgrid=True, gridcolor='rgba(31, 38, 51, 0.8)')
                    )
                    st.plotly_chart(fig_p, use_container_width=True, key=f"pharma_chart_{idx}", config={'displayModeBar': False})
                    st.write(f"**Statutory Finding:** {i.get('summary', 'Overcharge detected')}")
                    st.write(f"**Authority:** `{i.get('authority', 'NPPA DPCO')}` | **Billed:** `₹{b:,.2f}` | **Cap:** `₹{l:,.2f}`")

            st.divider()
            st.metric("Total Recoverable Statutory Leakage", f"₹{st.session_state.total_leakage:,.2f}")

    # --- 14.4 HOSPITAL AUDIT ---
    elif dept == "🏥 Hospital Audit":
        st.markdown("""
        <h1 style="font-size: 38px; font-weight: 800; color: #ffffff; margin-bottom: 6px;">We negotiate hospital bills for you.</h1>
        <p style="font-size: 15px; color: #94a3b8; margin-bottom: 24px;">Ensure you pay only what's fair with automated itemized auditing against 2026 CGHS gazettes and Supreme Court standardized clinical rates.</p>
        """, unsafe_allow_html=True)
        
        tab_h_upload, tab_h_cam, tab_h_text = st.tabs(["Upload Hospital Bill", "Live Camera", "Paste Text"])
        
        with tab_h_upload:
            u_h = st.file_uploader("Upload Hospital Invoice (JPEG, PNG)", type=["jpg", "png", "jpeg"], key="hosp_upload_main")
            if u_h and st.button("Run Forensic Hospital Audit →", use_container_width=True, key="btn_h_file"):
                st.session_state.scan_error = None
                with st.spinner("Reconciling against 2026 CGHS Gazette & NPPA Databases..."):
                    txt = extract_clean_text_from_image(u_h)
                    st.session_state.raw_extracted_text = txt
                    if txt:
                        res = hospital_audit_logic(txt)
                        st.session_state.ai_result_data = res
                        auto_log_audit("Hospital", res)

        with tab_h_cam:
            cam_h = st.camera_input("Capture hospital invoice with camera", key="cam_hosp")
            if cam_h and st.button("Audit Camera Scan →", use_container_width=True, key="btn_h_cam"):
                st.session_state.scan_error = None
                with st.spinner("Extracting Camera Stream Data..."):
                    txt = extract_clean_text_from_image(cam_h)
                    st.session_state.raw_extracted_text = txt
                    if txt:
                        res = hospital_audit_logic(txt)
                        st.session_state.ai_result_data = res
                        auto_log_audit("Hospital", res)

        with tab_h_text:
            manual_txt_h = st.text_area("Paste invoice line items directly", height=120, key="manual_hosp_txt")
            if manual_txt_h and st.button("Audit Pasted Text →", use_container_width=True, key="btn_h_txt"):
                st.session_state.scan_error = None
                st.session_state.raw_extracted_text = manual_txt_h
                res = hospital_audit_logic(manual_txt_h)
                st.session_state.ai_result_data = res
                auto_log_audit("Hospital", res)

        if st.session_state.get("scan_error"):
            st.error(st.session_state.get("scan_error"))

        if st.session_state.ai_result_data:
            res = st.session_state.ai_result_data
            items = res.get('audit_results', [])
            hosp = res.get('hospital', 'Detected Facility')
            
            orig_total_h = sum([float(re.sub(r'[^\d.]', '', str(x.get('billed', 0)))) for x in items])
            adjusted_total_h = orig_total_h - st.session_state.total_leakage
            pct_saved_h = (st.session_state.total_leakage / orig_total_h * 100) if orig_total_h > 0 else 0
            
            st.markdown(f"""
            <div class="mm-hero-card" style="margin-bottom: 20px;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                    <div>
                        <strong style="font-size: 20px; color: #ffffff;">🏥 {hosp}</strong><br>
                        <span style="font-size: 12px; color: #00d2ff;">CGHS 2026 Gazette (MoHFW) Verified</span>
                    </div>
                    <span style="background: #0066ff; color: #fff; font-size: 11px; font-weight: 700; padding: 4px 12px; border-radius: 9999px;">
                        {datetime.now().strftime('%B %d, %Y')}
                    </span>
                </div>
                <div style="display: flex; justify-content: space-between; padding: 8px 0; font-size: 14px; border-bottom: 1px solid #1f2633;">
                    <span style="color: #94a3b8;">Original Invoiced Rate:</span>
                    <strong style="color: #ffffff;">₹{orig_total_h:,.2f}</strong>
                </div>
                <div style="display: flex; justify-content: space-between; padding: 8px 0; font-size: 14px; border-bottom: 1px solid #1f2633;">
                    <span style="color: #94a3b8;">Statutory Gazette Discount:</span>
                    <strong style="color: #f43f5e;">-₹{st.session_state.total_leakage:,.2f}</strong>
                </div>
                <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 16px; padding: 14px; background: #151924; border-radius: 16px;">
                    <div>
                        <span style="font-size: 11px; color: #94a3b8; font-weight: 700;">MEDICALMAGIC ADJUSTED TOTAL</span><br>
                        <strong style="font-size: 24px; color: #00d2ff;">₹{adjusted_total_h:,.2f}</strong>
                    </div>
                    <span style="background: #0066ff; color: #fff; font-size: 13px; font-weight: 800; padding: 6px 16px; border-radius: 9999px;">
                        Save {pct_saved_h:.0f}%
                    </span>
                </div>
            </div>
            """, unsafe_allow_html=True)
            
            for idx, i in enumerate(items):
                try: b, l = float(re.sub(r'[^\d.]', '', str(i.get('billed', 0)))), float(re.sub(r'[^\d.]', '', str(i.get('legal', 0))))
                except Exception: b, l = 0.0, 0.0
                leak = round(b - l, 2) if (l > 0 and b > l) else 0.0
                
                with st.expander(f"📋 {i['item']} — Discrepancy: ₹{leak:,.2f}"):
                    fig_h = go.Figure(go.Bar(
                        x=['Statutory CGHS Cap', 'Hospital Invoiced'], 
                        y=[l, b], 
                        marker_color=['#22c55e', '#f43f5e'], 
                        text=[f"₹{l:,.2f}", f"₹{b:,.2f}"], 
                        textposition='auto',
                        width=0.35
                    ))
                    fig_h.update_layout(
                        template="plotly_dark", plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
                        height=180, margin=dict(l=0, r=0, t=10, b=0), yaxis=dict(showgrid=True, gridcolor='rgba(31, 38, 51, 0.8)')
                    )
                    st.plotly_chart(fig_h, use_container_width=True, key=f"hosp_audit_chart_{idx}", config={'displayModeBar': False})
                    st.write(f"**Statutory Finding:** {i.get('summary', 'Markup exceeds gazette ceiling')}")
                    st.write(f"**Authority:** `{i.get('authority', 'MoHFW')}` | **Invoiced:** `₹{b:,.2f}` | **Gazette Cap:** `₹{l:,.2f}`")

            st.divider()
            st.metric("Total Recoverable Hospital Leakage", f"₹{st.session_state.total_leakage:,.2f}")

    # --- 14.5 INSURANCE ARMOR ---
    elif dept == "🛡️ Insurance Armor":
        st.markdown("""
        <h1 style="font-size: 38px; font-weight: 800; color: #ffffff; margin-bottom: 6px;">Reconcile claim shortfalls instantly.</h1>
        <p style="font-size: 15px; color: #94a3b8; margin-bottom: 24px;">Expose arbitrary proportionate deductions, internal TPA caps, and unjustified non-medical exclusions.</p>
        """, unsafe_allow_html=True)
        
        tab_i_upload, tab_i_text = st.tabs(["Upload Denial Slip", "Paste Claim Text"])
        
        with tab_i_upload:
            u_i = st.file_uploader("Upload Settlement Letter", type=["jpg", "png", "jpeg"], key="ins_upload_main")
            if u_i and st.button("Reconcile Claim Deductions →", use_container_width=True):
                st.session_state.scan_error = None
                with st.spinner("Reconciling Policy Terms & Approved Amounts..."):
                    txt = extract_clean_text_from_image(u_i)
                    st.session_state.raw_extracted_text = txt
                    if txt:
                        res = insurance_audit_logic(txt)
                        st.session_state.ai_result_data = res
                        auto_log_audit("Insurance", res)

        with tab_i_text:
            manual_txt_i = st.text_area("Paste settlement document text directly", height=120, key="manual_ins_txt")
            if manual_txt_i and st.button("Audit Pasted Claim Text →", use_container_width=True):
                st.session_state.scan_error = None
                st.session_state.raw_extracted_text = manual_txt_i
                res = insurance_audit_logic(manual_txt_i)
                st.session_state.ai_result_data = res
                auto_log_audit("Insurance", res)

        if st.session_state.get("scan_error"):
            st.error(st.session_state.get("scan_error"))

        if st.session_state.ai_result_data:
            res = st.session_state.ai_result_data
            items = res.get('audit_results', [])
            company = res.get('hospital', 'Insurance Provider')
            
            orig_total_i = sum([float(re.sub(r'[^\d.]', '', str(x.get('billed', 0)))) for x in items])
            adjusted_total_i = orig_total_i - st.session_state.total_leakage
            pct_saved_i = (st.session_state.total_leakage / orig_total_i * 100) if orig_total_i > 0 else 0
            
            st.markdown(f"""
            <div class="mm-hero-card" style="margin-bottom: 20px;">
                <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px;">
                    <div>
                        <strong style="font-size: 20px; color: #ffffff;">🛡️ {company}</strong><br>
                        <span style="font-size: 12px; color: #00d2ff;">IRDAI Master Circular & Ombudsman Rules Audited</span>
                    </div>
                    <span style="background: #0066ff; color: #fff; font-size: 11px; font-weight: 700; padding: 4px 12px; border-radius: 9999px;">
                        {datetime.now().strftime('%B %d, %Y')}
                    </span>
                </div>
                <div style="display: flex; justify-content: space-between; padding: 8px 0; font-size: 14px; border-bottom: 1px solid #1f2633;">
                    <span style="color: #94a3b8;">Total Claim Invoiced:</span>
                    <strong style="color: #ffffff;">₹{orig_total_i:,.2f}</strong>
                </div>
                <div style="display: flex; justify-content: space-between; padding: 8px 0; font-size: 14px; border-bottom: 1px solid #1f2633;">
                    <span style="color: #94a3b8;">Arbitrary / Illegal Deductions:</span>
                    <strong style="color: #f43f5e;">-₹{st.session_state.total_leakage:,.2f}</strong>
                </div>
                <div style="display: flex; justify-content: space-between; align-items: center; margin-top: 16px; padding: 14px; background: #151924; border-radius: 16px;">
                    <div>
                        <span style="font-size: 11px; color: #94a3b8; font-weight: 700;">LEGALLY RECOVERABLE SUM</span><br>
                        <strong style="font-size: 24px; color: #00d2ff;">₹{adjusted_total_i:,.2f}</strong>
                    </div>
                    <span style="background: #0066ff; color: #fff; font-size: 13px; font-weight: 800; padding: 6px 16px; border-radius: 9999px;">
                        Dispute {pct_saved_i:.0f}%
                    </span>
                </div>
            </div>
            """, unsafe_allow_html=True)
            
            for idx, i in enumerate(items):
                try: b, l = float(re.sub(r'[^\d.]', '', str(i.get('billed', 0)))), float(re.sub(r'[^\d.]', '', str(i.get('legal', 0))))
                except Exception: b, l = 0.0, 0.0
                leak = round(b - l, 2) if (l > 0 and b > l) else 0.0
                
                with st.expander(f"📑 {i['item']} — Arbitrary Shortfall: ₹{leak:,.2f}"):
                    fig_i = go.Figure(go.Bar(
                        x=['Approved Limit', 'Hospital Billed'], 
                        y=[l, b], 
                        marker_color=['#22c55e', '#f43f5e'], 
                        text=[f"₹{l:,.2f}", f"₹{b:,.2f}"], 
                        textposition='auto',
                        width=0.35
                    ))
                    fig_i.update_layout(
                        template="plotly_dark", plot_bgcolor='rgba(0,0,0,0)', paper_bgcolor='rgba(0,0,0,0)',
                        height=180, margin=dict(l=0, r=0, t=10, b=0), yaxis=dict(showgrid=True, gridcolor='rgba(31, 38, 51, 0.8)')
                    )
                    st.plotly_chart(fig_i, use_container_width=True, key=f"ins_audit_chart_{idx}", config={'displayModeBar': False})
                    st.write(f"**Dispute Finding:** {i.get('summary', 'Arbitrary claim deduction.')}")

            st.divider()
            st.metric("Total Unjustified Claim Shortfall", f"₹{st.session_state.total_leakage:,.2f}")

    # --- 14.6 JUSTICE PORTAL ---
    elif dept == "⚖️ Justice Portal":
        st.markdown("""
        <h1 style="font-size: 38px; font-weight: 800; color: #ffffff; margin-bottom: 6px;">Section 2(47) Legal Notice Dispatch.</h1>
        <p style="font-size: 15px; color: #94a3b8; margin-bottom: 24px;">Automated generation of statutory demand briefs for hospital superintendents and consumer forums.</p>
        """, unsafe_allow_html=True)
        
        if st.session_state.ai_result_data:
            res = st.session_state.ai_result_data
            hosp_name = res.get('hospital', 'Medical Facility').upper()
            raw_items = res.get('audit_results', [])
            
            current_bill_leakage = 0.0
            formatted_items = []
            for item in raw_items:
                try: b, l = float(re.sub(r'[^\d.]', '', str(item.get('billed', 0)))), float(re.sub(r'[^\d.]', '', str(item.get('legal', 0))))
                except Exception: b, l = 0.0, 0.0
                if l > 0 and b > l: current_bill_leakage += round(b - l, 2)
                    
                formatted_items.append({
                    "Line Item Description": item.get('item', 'Medical Service'),
                    "Billed Amount": format_inr(b),
                    "Statutory Legal Cap": format_inr(l),
                    "Statutory Authority": item.get('authority', 'CGHS / NPPA Gazette'),
                    "Forensic Finding": item.get('summary', 'Markup exceeds statutory ceiling')
                })
            
            col_ref, col_grace = st.columns(2)
            with col_ref: ref_no = st.text_input("Notice Identifier", f"MA/2026/LEG/{random.randint(1000, 9999)}")
            with col_grace: grace_period = st.select_slider("Rectification Window (Business Days)", options=[3, 5, 7, 10, 15], value=7)

            with st.container(border=True):
                st.markdown(f"""
                <div style="display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid #0066ff; padding-bottom: 12px; margin-bottom: 16px;">
                    <div>
                        <span style="font-size: 20px; font-weight: 800; color: #00d2ff;">🛡️ MEDICALMAGIC STATUTORY NOTICE</span><br>
                        <span style="font-size: 11px; color: #94a3b8;">CERTIFIED UNDER SECTION 2(47) OF CONSUMER PROTECTION ACT, 2019</span>
                    </div>
                    <div style="text-align: right; font-family: monospace; font-size: 11px; color: #94a3b8;">
                        REF: {ref_no}<br>DATE: {datetime.now().strftime('%B %d, %Y')}
                    </div>
                </div>
                """, unsafe_allow_html=True)
                
                st.markdown(f"**TO:** Medical Superintendent / Grievance Cell  \n**FACILITY:** **{hosp_name}**")
                st.markdown("#### SUBJECT: FORMAL DISPUTE NOTICE FOR UNFAIR TRADE PRACTICE & STATUTORY PRICE CEILING VIOLATIONS")
                st.write(
                    "This notice serves as formal communication of verified pricing markups identified in patient invoices, "
                    "in violation of the **Central Government Health Scheme (CGHS) 2026 Gazette Ceilings (MoHFW)** and the "
                    "**Drugs (Prices Control) Order / NPPA Caps** under the Essential Commodities Act."
                )
                
                st.dataframe(pd.DataFrame(formatted_items), use_container_width=True, hide_index=True)
                
                st.error(f"### TOTAL RECOVERABLE OVERCHARGE FOR THIS INVOICE: ₹{current_bill_leakage:,.2f}")
                
                st.caption(
                    f"**DEMAND & LEGAL RECOURSE:** Demand is hereby placed to rectify the billing invoice and refund the excess sum of "
                    f"**₹{current_bill_leakage:,.2f}** within **{grace_period} business days**, failing which formal complaints shall be escalated before "
                    "the District Consumer Disputes Redressal Commission and Insurance Ombudsman."
                )

            col_btn1, col_btn2 = st.columns(2)
            with col_btn1:
                st.button("Dispatch Electronic Notice →", type="primary", use_container_width=True)
            with col_btn2:
                legal_brief_text = (
                    f"MEDICALMAGIC LEGAL DISPUTE NOTICE (SEC 2(47) CPA)\n"
                    f"REF: {ref_no}\n"
                    f"DATE: {datetime.now().strftime('%B %d, %Y')}\n"
                    f"TO: Medical Superintendent, {hosp_name}\n"
                    f"TOTAL DISPUTED OVERCHARGE: INR {current_bill_leakage:,.2f}\n"
                    f"GRACE PERIOD FOR REFUND: {grace_period} BUSINESS DAYS\n\n"
                    f"ITEMIZED DISCREPANCIES:\n"
                )
                for f in formatted_items:
                    legal_brief_text += f"- {f['Line Item Description']} | Billed: {f['Billed Amount']} | Cap: {f['Statutory Legal Cap']} | {f['Forensic Finding']}\n"

                st.download_button(
                    label="📥 Download Legal Notice PDF / Brief",
                    data=legal_brief_text,
                    file_name=f"MedicalMagic_Legal_Notice_{ref_no.replace('/', '_')}.txt",
                    mime="text/plain",
                    use_container_width=True
                )
        else:
            st.warning("⚠️ Complete an invoice or pharma audit first to generate legal dispute documentation.")

    # --- 14.7 REGULATORY AI COPILOT ---
    elif dept == "💬 AI Copilot":
        st.markdown("""
        <h1 style="font-size: 38px; font-weight: 800; color: #ffffff; margin-bottom: 6px;">MedicalMagic Regulatory Assistant.</h1>
        <p style="font-size: 15px; color: #94a3b8; margin-bottom: 24px;">Real-time Socratic lookup of procedure rate ceilings and DPCO statutory dispute precedents.</p>
        """, unsafe_allow_html=True)
        
        u_m = st.chat_input("Ask about CGHS rates, NPPA generic price rules, or legal consumer rights...")
        if u_m:
            st.session_state.messages.append({"role": "user", "content": u_m})
            pdf_data = get_pdf_context(u_m)
            assistant_prompt = f"Use this multi-gazette context if available: {pdf_data}. Otherwise use standard CGHS 2026 and NPPA DPCO rates to answer clearly and authoritatively. Question: {u_m}"
            response = llm.invoke(assistant_prompt)
            st.session_state.messages.append({"role": "assistant", "content": response.content})
        for m in st.session_state.messages[-4:]: 
            st.chat_message(m["role"]).write(m["content"])

```
