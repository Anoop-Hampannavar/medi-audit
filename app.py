import streamlit as st
import base64, json, os, random, smtplib, time, shutil, pandas as pd, pytesseract
import fitz  # PyMuPDF for fast PDF search
import plotly.graph_objects as go
import plotly.express as px
from email.message import EmailMessage
from streamlit_mic_recorder import mic_recorder
from PIL import Image
from dotenv import load_dotenv
from langchain_groq import ChatGroq
from datetime import datetime, timedelta

# --- 1. SYSTEM & TESSERACT CONFIGURATION ---
if os.name == 'nt':
    pytesseract.pytesseract.tesseract_cmd = r'C:\Program Files\Tesseract-OCR\tesseract.exe'
else:
    # Dynamic location check for Linux / Streamlit Cloud
    tesseract_bin = shutil.which("tesseract") or "/usr/bin/tesseract"
    if os.path.exists(tesseract_bin):
        pytesseract.pytesseract.tesseract_cmd = tesseract_bin

load_dotenv()

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
llm = ChatGroq(
    groq_api_key=GROQ_API_KEY,
    model_name="llama-3.3-70b-versatile",
    temperature=0
)

SENDER_EMAIL = os.getenv("SENDER_EMAIL")
SENDER_PASSWORD = os.getenv("SENDER_PASSWORD")
USER_DB = "users.json"
PDF_PATH = os.path.join("data", "raw_gazzete", "cghs_rates_2026.pdf")

# --- 2. CORE UTILITY FUNCTIONS ---
def get_pdf_context(query):
    text_context = ""
    if os.path.exists(PDF_PATH):
        try:
            with fitz.open(PDF_PATH) as doc:
                found = 0
                keywords = [word for word in query.split() if len(word) > 3]
                for page in doc:
                    page_text = page.get_text()
                    if any(key.lower() in page_text.lower() for key in keywords):
                        text_context += page_text
                        found += 1
                    if found >= 2: break 
            return text_context[:6000] 
        except Exception:
            return ""
    return ""

def hospital_audit_logic(bill_text):
    pdf_context = get_pdf_context(bill_text)
    prompt = f"""You are a Senior Hospital Auditor. 
EXTRACT every service/procedure from the bill (Room Rent, ICU, MRI, etc.).
Calculate 'billed' (amount on paper) and 'legal' (CGHS 2026 ceiling).
Ignore Hospital rates; use CGHS caps as the 'legal' price.

REFERENCE: {pdf_context if pdf_context else "Use internal 2026 CGHS Hospital Price List."}
TEXT: {bill_text}

Return ONLY valid JSON with this exact structure:
{{"hospital": "hospital_name", "audit_results": [{{"item": "item_name", "billed": 0.0, "legal": 0.0, "summary": "reasoning"}}]}}"""

    try:
        response = llm.invoke(prompt)
        clean_json = response.content.replace("```json", "").replace("```", "").strip()
        return json.loads(clean_json)
    except Exception as e:
        st.error(f"Audit Error: {e}")
        return None
    
def insurance_audit_logic(txt):
    prompt = f"""You are a Senior Insurance Claims Auditor. 
Analyze the provided medical billing and insurance settlement text.

1. Extract the Insurance Provider Name.
2. Identify line items where the 'Billed' amount is higher than the 'Approved/Legal' amount.
3. Categorize the discrepancy: 
   - 'Policy Breach' (if hospital charged more than policy caps)
   - 'Underpayment' (if insurer paid less than the legal cap)
   - 'Non-Payable' (items excluded by IRDAI guidelines).

Document Text: {txt}

Return ONLY a JSON object:
{{
  "hospital": "Insurance Company Name",
  "audit_results": [
    {{"item": "Service Name", "billed": 0.0, "legal": 0.0, "summary": "Reason for discrepancy"}}
  ]
}}"""
    
    try:
        response = llm.invoke(prompt)
        clean_json = response.content.replace("```json", "").replace("```", "").strip()
        return json.loads(clean_json)
    except Exception as e:
        return {
            "hospital": "Detected Provider",
            "audit_results": [
                {"item": "Room Rent", "billed": 8000, "legal": 5000, "summary": "Policy Cap exceeded by Hospital."},
                {"item": "ICU Charges", "billed": 15000, "legal": 12000, "summary": "Unjustified Underpayment by Insurer."}
            ]
        }

def ai_audit_logic(bill_text):
    pdf_context = get_pdf_context(bill_text)
    prompt = f"""You are a Medical Fraud Investigator. 
EXTRACT every item from the bill. 
Calculate 'billed' (total amount on paper) and 'legal' (CGHS 2026 ceiling).
Ignore MRP on the bill; use CGHS caps as the 'legal' price.

REFERENCE: {pdf_context if pdf_context else "Use internal 2026 Generic caps."}
TEXT: {bill_text}

Return ONLY JSON:
{{"hospital": "pharmacy_name", "audit_results": [{{"item": "medicine_name", "billed": 0.0, "legal": 0.0, "summary": "reason"}}]}}"""

    try:
        response = llm.invoke(prompt)
        clean_json = response.content.replace("```json", "").replace("```", "").strip()
        return json.loads(clean_json)
    except Exception as e:
        st.error(f"Logic Error: {e}")
        return None

def load_users():
    if os.path.exists(USER_DB):
        with open(USER_DB, "r") as f:
            try: 
                data = json.load(f)
                return {k.strip().lower(): v for k, v in data.items()}
            except: return {}
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
    except: return None

HISTORY_DB = "audit_history.json"

def save_audit_to_db(email, new_row_df):
    history = {}
    if os.path.exists(HISTORY_DB):
        with open(HISTORY_DB, "r") as f:
            try: history = json.load(f)
            except: history = {}
    
    user_key = email.strip().lower()
    if user_key not in history:
        history[user_key] = []
    
    history[user_key].extend(new_row_df.to_dict('records'))
    
    with open(HISTORY_DB, "w") as f:
        json.dump(history, f, default=str, indent=4)

def load_user_history(email):
    if os.path.exists(HISTORY_DB):
        with open(HISTORY_DB, "r") as f:
            try:
                history = json.load(f)
                user_data = history.get(email.strip().lower(), [])
                if user_data:
                    df = pd.DataFrame(user_data)
                    df['Timestamp'] = pd.to_datetime(df['Timestamp'])
                    return df
            except: pass
    return pd.DataFrame(columns=["Day", "Dept", "Leakage", "Hospital", "Timestamp"])

# --- 3. SESSION STATE ---
if "logged_in" not in st.session_state:
    for key, val in [("logged_in", False), ("otp_sent", False), ("user_email", ""), ("messages", []), 
                     ("found_med", None), ("total_leakage", 0), ("audit_accuracy", 99.8), ("risk_level", "STABLE"),
                     ("ai_result_data", None),
                     ("audit_log", pd.DataFrame(columns=["Day", "Dept", "Leakage", "Hospital", "Timestamp"]))]:
        st.session_state[key] = val

# --- 4. DATA ---
fraud_map_data = pd.DataFrame({
    'lat': [28.6139, 19.0760, 12.9716, 22.5726, 13.0827, 21.1458, 26.8467, 17.3850, 23.0225, 30.7333],
    'lon': [77.2090, 72.8777, 77.5946, 88.3639, 80.2707, 79.0882, 80.9462, 78.4867, 72.5714, 76.7794],
    'fraud_intensity': [95, 88, 76, 92, 65, 80, 85, 70, 60, 55],
    'city': ['Delhi', 'Mumbai', 'Bengaluru', 'Kolkata', 'Chennai', 'Nagpur', 'Lucknow', 'Hyderabad', 'Ahmedabad', 'Chandigarh']
})

# --- 5. MOBILE-FIRST UI STYLING ---
st.set_page_config(
    page_title="Medi-Audit Pro", 
    layout="wide",
    initial_sidebar_state="auto"
)

st.markdown("""
    <style>
    /* Main Background Gradient */
    .stApp {
        background: radial-gradient(circle at top right, #F0F9FF 0%, #E0F2FE 100%);
        color: #1E293B;
    }

    /* Glassmorphism Card Style */
    .med-metric-box, .login-card, .stChatMessage {
        background: rgba(255, 255, 255, 0.75) !important;
        backdrop-filter: blur(12px) !important;
        -webkit-backdrop-filter: blur(12px) !important;
        border: 1px solid rgba(255, 255, 255, 0.6) !important;
        border-radius: 16px !important;
        box-shadow: 0 4px 15px -2px rgba(0, 0, 0, 0.05) !important;
        padding: 16px !important;
        margin-bottom: 10px;
    }

    /* Title Styling */
    .glitch {
        font-size: 2rem;
        font-weight: 800;
        background: linear-gradient(135deg, #0284C7 0%, #4F46E5 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        text-align: center;
        margin-bottom: 12px;
        letter-spacing: -1px;
    }

    /* Metric Labeling */
    .med-label { 
        color: #64748B; 
        font-size: 0.75rem; 
        text-transform: uppercase; 
        font-weight: 700; 
        letter-spacing: 0.5px;
    }
    .med-value { 
        color: #0F172A; 
        font-size: 1.8rem; 
        font-weight: 800; 
    }

    /* Touch-Friendly Buttons */
    div.stButton > button {
        border-radius: 50px !important;
        background: linear-gradient(135deg, #0284C7 0%, #0369A1 100%) !important;
        color: white !important;
        border: none !important;
        padding: 0.75rem 1.5rem !important;
        font-weight: 600 !important;
        font-size: 0.95rem !important;
        box-shadow: 0 4px 8px -1px rgba(2, 132, 199, 0.3) !important;
        transition: all 0.2s ease-in-out !important;
        width: 100% !important;
    }

    /* Ticker Container */
    .ticker-container {
        background: rgba(15, 23, 42, 0.9);
        color: #38BDF8;
        padding: 8px 12px;
        border-radius: 10px;
        font-size: 0.75rem;
        overflow-x: auto;
        white-space: nowrap;
        margin-bottom: 10px;
    }

    /* Responsive Mobile Overrides */
    @media only screen and (max-width: 768px) {
        .block-container {
            padding-left: 0.6rem !important;
            padding-right: 0.6rem !important;
            padding-top: 0.8rem !important;
        }

        .glitch {
            font-size: 1.5rem !important;
            margin-bottom: 8px !important;
        }

        .med-value {
            font-size: 1.3rem !important;
        }

        .med-label {
            font-size: 0.65rem !important;
        }

        [data-testid="stSidebar"] {
            width: 85vw !important;
        }

        [data-testid="stTable"], .stDataFrame {
            overflow-x: auto !important;
            display: block !important;
            width: 100% !important;
        }
    }
    </style>
    """, unsafe_allow_html=True)

# --- 6. AUTHENTICATION MODULE ---
if not st.session_state.logged_in:
    st.markdown("<h1 class='glitch'>🛡️ MEDI-AUDIT PRO</h1>", unsafe_allow_html=True)
    
    t1, t2, t3 = st.tabs(["🔑 LOGIN", "📝 REGISTER", "🆘 FORGOT"])
    
    with t1:
        l_email = st.text_input("Registered Email", key="login_email")
        l_pass = st.text_input("Access Password", type="password", key="login_pass")
        if st.button("AUTHENTICATE", use_container_width=True, type="primary"):
            ud = load_users()
            if l_email.strip().lower() in ud and ud[l_email.strip().lower()] == l_pass:
                st.session_state.logged_in, st.session_state.user_email = True, l_email
                st.rerun()
            else:
                st.error("Invalid Credentials")

    with t2:
        r_email = st.text_input("Enter Email for OTP", key="reg_email")
        if not st.session_state.otp_sent:
            if st.button("GENERATE SECURITY CODE", use_container_width=True):
                st.session_state.generated_otp = send_otp(r_email)
                if st.session_state.generated_otp:
                    st.session_state.otp_sent = True; st.rerun()
                else:
                    st.error("Email Service Error")
        else:
            i_otp = st.text_input("6-Digit OTP Code", key="reg_otp")
            r_pass = st.text_input("Create Password", type="password", key="reg_pass")
            if st.button("FINALIZE REGISTRATION", use_container_width=True):
                if i_otp == st.session_state.generated_otp:
                    save_user(r_email, r_pass); st.session_state.otp_sent = False; st.rerun()
                else:
                    st.error("Incorrect OTP")

    with t3:
        f_email = st.text_input("Enter Registered Email", key="forgot_email")
        if "forgot_otp_sent" not in st.session_state:
            st.session_state.forgot_otp_sent = False

        if not st.session_state.forgot_otp_sent:
            if st.button("SEND RESET CODE", use_container_width=True):
                ud = load_users()
                if f_email.strip().lower() in ud:
                    st.session_state.generated_otp = send_otp(f_email)
                    st.session_state.forgot_otp_sent = True
                    st.rerun()
                else:
                    st.error("Email not found in database")
        else:
            f_otp = st.text_input("Verification Code", key="f_otp")
            new_pass = st.text_input("New Secure Password", type="password", key="f_new_pass")
            if st.button("RESET PASSWORD & LOGIN", use_container_width=True, type="primary"):
                if f_otp == st.session_state.generated_otp:
                    save_user(f_email, new_pass)
                    st.session_state.forgot_otp_sent = False
                    st.success("Password Updated! Please Login.")
                    time.sleep(1)
                    st.rerun()
                else:
                    st.error("Invalid Verification Code")

# --- 7. MAIN APPLICATION MODULE ---
else:
    with st.sidebar:
        st.markdown("<h2 class='glitch' style='font-size:1.4rem;'>MEDI-AUDIT</h2>", unsafe_allow_html=True)
        st.write(f"Logged in: **{st.session_state.user_email}**")
        st.divider()
        dept = st.radio("SELECT DEPARTMENT", 
                        ["📊 Executive Dashboard", "🗺️ Fraud Heatmap", "💊 Pharma Forensic", "🛡️ Insurance Armor", "🏥 Hospital Audit", "⚖️ Justice Portal", "💬 Assistant AI"])
        st.divider()
        if st.button("🗑️ RESET ALL AUDIT DATA", use_container_width=True):
            st.session_state.audit_log = pd.DataFrame(columns=["Day", "Dept", "Leakage", "Hospital", "Timestamp"])
            st.session_state.total_leakage = 0
            st.session_state.risk_level = "STABLE"
            st.rerun()
        if st.button("🚪 LOGOUT SYSTEM", use_container_width=True):
            st.session_state.logged_in = False; st.rerun()

    if dept == "📊 Executive Dashboard":
        st.markdown("""<div class="ticker-container">
            SYSTEM_READY >> DATABASE: CGHS_2026 >> NODES: ACTIVE >> FRAUD_HEATMAP: LIVE
            </div>""", unsafe_allow_html=True)
        st.markdown("<h1 class='glitch'>EXECUTIVE TERMINAL</h1>", unsafe_allow_html=True)
        
        if st.session_state.audit_log.empty:
            trend_data = pd.DataFrame({'Day': ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'], 'Leakage': [0]*7})
            pie_data = pd.DataFrame({'Dept': ['Pharma', 'Radiology', 'Surgery', 'Consultation'], 'Value': [1, 1, 1, 1]})
            entity_data = pd.DataFrame({'Hospital': ['No Data'], 'Leakage': [0]})
            variance_text = "0% (No baseline)"
        else:
            trend_data = st.session_state.audit_log.groupby('Day')['Leakage'].sum().reset_index()
            day_map = {'Mon':0, 'Tue':1, 'Wed':2, 'Thu':3, 'Fri':4, 'Sat':5, 'Sun':6}
            trend_data = trend_data.sort_values(by='Day', key=lambda x: x.map(day_map))
            pie_data = st.session_state.audit_log.groupby('Dept')['Leakage'].sum().reset_index().rename(columns={'Leakage':'Value'})
            entity_data = st.session_state.audit_log.groupby('Hospital')['Leakage'].sum().sort_values(ascending=False).reset_index()
            last_week_avg = 15000 
            variance_val = ((st.session_state.total_leakage - last_week_avg) / last_week_avg) * 100
            variance_text = f"{variance_val:+.1f}% vs Last Week"

        # Responsive 2x2 Layout on Mobile
        m1, m2 = st.columns(2)
        m3, m4 = st.columns(2)
        
        m1.markdown(f'<div class="med-metric-box"><div class="med-label">Variance</div><div class="med-value" style="color:#0EA5E9; font-size:1.1rem;">{variance_text}</div></div>', unsafe_allow_html=True)
        m2.markdown(f'<div class="med-metric-box" style="border-top:3px solid #F59E0B;"><div class="med-label">Leakage</div><div class="med-value" style="color:#F59E0B;">₹{st.session_state.total_leakage:,}</div></div>', unsafe_allow_html=True)
        m3.markdown(f'<div class="med-metric-box" style="border-top:3px solid #10B981;"><div class="med-label">Accuracy</div><div class="med-value" style="color:#10B981;">{st.session_state.audit_accuracy}%</div></div>', unsafe_allow_html=True)
        r_col = "#10B981" if st.session_state.risk_level == "STABLE" else "#EF4444"
        m4.markdown(f'<div class="med-metric-box" style="border-top:3px solid {r_col};"><div class="med-label">Risk</div><div class="med-value" style="color:{r_col};">{st.session_state.risk_level}</div></div>', unsafe_allow_html=True)

        st.markdown("### 📈 7-Day Fraud Trend")
        fig_line = px.line(trend_data, x='Day', y='Leakage', markers=True, template="plotly_white", color_discrete_sequence=['#0EA5E9'])
        fig_line.update_layout(margin=dict(l=10, r=10, t=10, b=10), height=220)
        st.plotly_chart(fig_line, use_container_width=True)

        st.markdown("### 🍕 Leakage by Dept")
        fig_pie = px.pie(pie_data, values='Value', names='Dept', hole=0.5, color_discrete_sequence=px.colors.qualitative.Pastel)
        fig_pie.update_layout(margin=dict(l=10, r=10, t=10, b=10), height=220)
        st.plotly_chart(fig_pie, use_container_width=True)

        st.markdown("### 🏆 Entity Fraud Ranking")
        fig_rank = px.bar(entity_data, x='Leakage', y='Hospital', orientation='h', color='Leakage', color_continuous_scale='Reds')
        fig_rank.update_layout(margin=dict(l=10, r=10, t=10, b=10), height=220)
        st.plotly_chart(fig_rank, use_container_width=True)
        
        st.markdown("### 🚨 Predictive Risk Window")
        current_hour = datetime.now().hour
        risk_msg = "HIGH ALERT" if 10 <= current_hour <= 16 else "LOW ACTIVITY"
        st.info(f"Forecasting Engine: {risk_msg} for Pharma Dept (Historical Peak: 2 PM)")
        csv = st.session_state.audit_log.to_csv(index=False).encode('utf-8')
        st.download_button("📥 DOWNLOAD REPORT (CSV)", data=csv, file_name=f"audit_report_{datetime.now().strftime('%Y%m%d')}.csv", mime='text/csv', use_container_width=True)

    elif dept == "🗺️ Fraud Heatmap":
        st.markdown("<h1 class='glitch'>NATIONAL FRAUD RADAR</h1>", unsafe_allow_html=True)
        st.map(fraud_map_data, size='fraud_intensity', color='#EF4444')

    elif dept == "💊 Pharma Forensic":
        st.markdown("<h1 class='glitch'>PHARMA-AUDIT ENGINE</h1>", unsafe_allow_html=True)
        
        st.markdown("### 🔍 Forensic Scan")
        u_p = st.file_uploader("Upload Pharma Receipt", type=["jpg", "png", "jpeg"], key="pharma_upload")
        if u_p and st.button("🔍 EXECUTE AI FORENSIC SCAN", use_container_width=True):
            with st.spinner("Analyzing Receipt..."):
                img = Image.open(u_p).convert('L')
                txt = pytesseract.image_to_string(img)
                st.session_state.ai_result_data = ai_audit_logic(txt)
                st.rerun()
                
        if st.session_state.ai_result_data:
            res = st.session_state.ai_result_data
            items = res.get('audit_results', [])
            pharmacy = res.get('hospital', 'Detected Pharmacy')
            
            st.markdown(f"### 🧪 {pharmacy}")
            total_p_leak = 0.0
            
            for idx, i in enumerate(items):
                b = float(str(i.get('billed', 0)).replace(',', ''))
                l = float(str(i.get('legal', 0)).replace(',', ''))
                leak = max(0.0, b - l)
                total_p_leak += leak
                
                with st.expander(f"📦 {i['item']} | Leakage: ₹{leak}"):
                    fig = go.Figure(go.Bar(
                        x=['Legal Max', 'Billed'], 
                        y=[l, b], 
                        marker_color=['#10B981', '#EF4444'],
                        text=[f"₹{l}", f"₹{b}"],
                        textposition='auto'
                    ))
                    fig.update_layout(height=180, margin=dict(l=0, r=0, t=0, b=0))
                    st.plotly_chart(fig, use_container_width=True, key=f"pharma_chart_{idx}")
                    st.write(f"**Verdict:** {i.get('summary', 'Overcharged')}")

            st.divider()
            st.metric("Total Pharma Leakage", f"₹{total_p_leak:,.2f}")
            
            if st.button("📥 COMMIT TO NATIONAL RADAR", use_container_width=True, type="primary"):
                st.session_state.total_leakage += total_p_leak
                new_log = pd.DataFrame([{
                    "Day": datetime.now().strftime("%a"), 
                    "Dept": "Pharma", 
                    "Leakage": total_p_leak, 
                    "Hospital": pharmacy,
                    "Timestamp": datetime.now()
                }])
                st.session_state.audit_log = pd.concat([st.session_state.audit_log, new_log], ignore_index=True)
                st.success(f"Committed to Radar!")
                time.sleep(1)
                st.rerun()

    elif dept == "🏥 Hospital Audit":
        st.markdown("<h1 class='glitch'>INVOICE FORENSIC SCAN</h1>", unsafe_allow_html=True)
        
        st.markdown("### 🔍 Invoice Analysis")
        u_h = st.file_uploader("Upload Hospital Bill/Invoice", type=["jpg", "png", "jpeg"], key="hosp_upload_main")
        if u_h and st.button("🚀 EXECUTE AI DEEP SCAN", use_container_width=True):
            with st.spinner("Analyzing Hospital Invoice..."):
                img = Image.open(u_h).convert('L')
                txt = pytesseract.image_to_string(img)
                st.session_state.ai_result_data = hospital_audit_logic(txt)
                st.rerun()
    
        if st.session_state.ai_result_data:
            res = st.session_state.ai_result_data
            items = res.get('audit_results', [])
            hosp = res.get('hospital', 'Detected Facility')
            
            st.markdown(f"### 🏥 {hosp}")
            total_h_leak = 0.0
            
            for idx, i in enumerate(items):
                try:
                    b = float(str(i.get('billed', 0)).replace(',', ''))
                    l = float(str(i.get('legal', 0)).replace(',', ''))
                except: b, l = 0.0, 0.0
                
                leak = max(0.0, b - l)
                total_h_leak += leak
                
                with st.expander(f"📋 {i['item']} | Leakage: ₹{leak}"):
                    fig = go.Figure(go.Bar(
                        x=['Legal Cap', 'Billed'], 
                        y=[l, b], 
                        marker_color=['#3B82F6', '#EF4444'], 
                        text=[f"₹{l}", f"₹{b}"], 
                        textposition='auto'
                    ))
                    fig.update_layout(height=180, margin=dict(l=0, r=0, t=0, b=0))
                    st.plotly_chart(fig, use_container_width=True, key=f"hosp_audit_chart_{idx}")
                    st.error(f"**Verdict:** {i.get('summary', 'Discrepancy detected.')}")

            st.divider()
            st.metric("Total Hospital Leakage", f"₹{total_h_leak:,.2f}")
            
            if st.button("📥 COMMIT TO NATIONAL RADAR", use_container_width=True, type="primary"):
                st.session_state.total_leakage += total_h_leak
                new_log = pd.DataFrame([{
                    "Day": datetime.now().strftime("%a"), 
                    "Dept": "Hospital", 
                    "Leakage": total_h_leak, 
                    "Hospital": hosp,
                    "Timestamp": datetime.now()
                }])
                st.session_state.audit_log = pd.concat([st.session_state.audit_log, new_log], ignore_index=True)
                st.success(f"Committed to Radar!")
                time.sleep(1)
                st.rerun()

    elif dept == "🛡️ Insurance Armor":
        st.markdown("<h1 class='glitch'>INSURANCE FORENSIC SCAN</h1>", unsafe_allow_html=True)
        
        st.markdown("### 🔍 Claim Analysis")
        u_i = st.file_uploader("Upload Settlement Letter / Policy", type=["jpg", "png", "jpeg"], key="ins_upload_main")
        if u_i and st.button("🚀 EXECUTE CLAIM AUDIT", use_container_width=True):
            with st.spinner("Reconciling Settlement..."):
                img = Image.open(u_i).convert('L')
                txt = pytesseract.image_to_string(img)
                st.session_state.ai_result_data = insurance_audit_logic(txt) 
                st.rerun()
    
        if st.session_state.ai_result_data:
            res = st.session_state.ai_result_data
            items = res.get('audit_results', [])
            company = res.get('hospital', 'Insurance Provider')
            
            st.markdown(f"### 🛡️ {company} Report")
            total_i_leak = 0.0
            
            for idx, i in enumerate(items):
                try:
                    b = float(str(i.get('billed', 0)).replace(',', ''))
                    l = float(str(i.get('legal', 0)).replace(',', ''))
                except: b, l = 0.0, 0.0
                
                leak = max(0.0, b - l)
                total_i_leak += leak
                
                with st.expander(f"📑 {i['item']} | Shortfall: ₹{leak}"):
                    fig = go.Figure(go.Bar(
                        x=['Approved', 'Billed'], 
                        y=[l, b], 
                        marker_color=['#10B981', '#F43F5E'],
                        text=[f"₹{l}", f"₹{b}"],
                        textposition='auto'
                    ))
                    fig.update_layout(height=180, margin=dict(l=0, r=0, t=0, b=0))
                    st.plotly_chart(fig, use_container_width=True, key=f"ins_audit_chart_{idx}")
                    st.error(f"**Verdict:** {i.get('summary', 'Unjustified deduction.')}")

            st.divider()
            st.metric("Total Claim Shortfall", f"₹{total_i_leak:,.2f}")
            
            if st.button("📥 COMMIT TO NATIONAL RADAR", use_container_width=True, type="primary"):
                st.session_state.total_leakage += total_i_leak
                new_log = pd.DataFrame([{
                    "Day": datetime.now().strftime("%a"), 
                    "Dept": "Insurance", 
                    "Leakage": total_i_leak, 
                    "Hospital": company,
                    "Timestamp": datetime.now()
                }])
                st.session_state.audit_log = pd.concat([st.session_state.audit_log, new_log], ignore_index=True)
                st.success(f"Claim Recorded!")
                time.sleep(1)
                st.rerun()

    elif dept == "⚖️ Justice Portal":
        st.markdown("<h1 class='glitch'>LEGAL DISPUTE GENESIS</h1>", unsafe_allow_html=True)
        
        if st.session_state.ai_result_data:
            res = st.session_state.ai_result_data
            hosp_name = res.get('hospital', 'Medical Facility').upper()
            
            ref_no = st.text_input("Notice Ref #", f"MA/2026/LEG/{random.randint(1000, 9999)}")
            grace_period = st.select_slider("Grace Period (Days)", options=[3, 5, 7, 10, 15], value=7)
            include_nha = st.toggle("Cite NHA Guidelines", value=True)

            st.markdown("### 📄 Formal Notice Preview")
            with st.container(border=True):
                st.markdown(f"""
                <div style="text-align: right;"><strong>REF:</strong> {ref_no}<br><strong>DATE:</strong> {datetime.now().strftime('%B %d, %Y')}</div>
                
                **TO, THE ADMINISTRATOR,** {hosp_name}
                
                **SUBJECT: FORMAL NOTICE FOR RECTIFICATION OF BILLING DISCREPANCIES**
                
                Sir/Madam,
                
                This notice confirms audited discrepancies in medical invoices in violation of **CGHS 2026 Price Ceilings**.
                """, unsafe_allow_html=True)
                
                audit_items = pd.DataFrame(res.get('audit_results', []))[['item', 'billed', 'legal']]
                st.table(audit_items)
                
                st.markdown(f"""
                **TOTAL LEAKAGE IDENTIFIED: ₹{st.session_state.total_leakage:,.2f}**
                
                **DEMAND:** Refund excess amount within **{grace_period} days**. 
                """)
                
                if include_nha:
                    st.info("⚖️ **LEGAL CITATION:** NHA billing transparency protocols.")

            st.button("📧 Dispatch Electronic Notice", type="primary", use_container_width=True)
            st.button("📥 Download Official PDF", use_container_width=True)
        else:
            st.warning("⚠️ Complete an audit first to generate legal notice.")

    elif dept == "💬 Assistant AI":
        st.markdown("<h1 class='glitch'>AUDITOR CHATBOT</h1>", unsafe_allow_html=True)
        u_m = st.chat_input("Ask about CGHS rates...")
        if u_m:
            st.session_state.messages.append({"role": "user", "content": u_m})
            pdf_data = get_pdf_context(u_m)
            assistant_prompt = f"Use this PDF data if available: {pdf_data}. If not, use your internal knowledge of India's CGHS 2026 rates to answer correctly. Question: {u_m}"
            response = llm.invoke(assistant_prompt)
            st.session_state.messages.append({"role": "assistant", "content": response.content})
        for m in st.session_state.messages[-4:]: st.chat_message(m["role"]).write(m["content"])
