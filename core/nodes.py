import os
import json
import pytesseract
from PIL import Image
import base64
import io
from dotenv import load_dotenv
from langchain_groq import ChatGroq
from core.state import AuditState
from utils.vector_store import get_retriever

load_dotenv()

# Initialize High-speed Groq Llama Model
llm = ChatGroq(
    groq_api_key=os.getenv("GROQ_API_KEY"),
    model_name="llama-3.3-70b-versatile",
    temperature=0
)

def extraction_node(state: AuditState):
    print("---NODE: GROQ OCR EXTRACTION---")
    # Convert base64 image string to PIL Image and perform local OCR
    img_data = base64.b64decode(state["bill_image"])
    image = Image.open(io.BytesIO(img_data))
    extracted_text = pytesseract.image_to_string(image)
    
    prompt = f"""Extract all medical bill items from this text:
{extracted_text}

Return ONLY a valid JSON list of objects:
[{{"name": "Procedure/Item", "billed_amount": 5000}}]"""
    
    msg = llm.invoke(prompt)
    clean_json = msg.content.replace("```json", "").replace("```", "").strip()
    return {"extracted_items": json.loads(clean_json)}

def audit_node(state: AuditState):
    print("---NODE: GROQ RAG AUDIT---")
    retriever = get_retriever()
    found_discrepancies = []
    
    for item in state.get("extracted_items", []):
        docs = retriever.invoke(item['name'])
        
        if docs:
            legal_context = docs[0].page_content
            comparison_prompt = f"""
            Hospital billed '{item['name']}' at ₹{item['billed_amount']}.
            Govt Gazette Rate Info: {legal_context}
            
            Is there an overcharge? If yes, calculate the excess.
            Return ONLY JSON: {{"item": "{item['name']}", "excess": 500, "legal_rate": 1000}}
            If no overcharge, return: {{"item": "{item['name']}", "excess": 0}}
            """
            res = llm.invoke(comparison_prompt)
            data = json.loads(res.content.replace("```json", "").replace("```", "").strip())
            
            if data.get("excess", 0) > 0:
                found_discrepancies.append(data)
                
    return {"discrepancies": found_discrepancies}

def report_node(state: AuditState):
    print("---NODE: FINAL REPORT---")
    if not state.get("discrepancies"):
        report = "✅ Audit Complete: All charges are compliant with CGHS rates."
    else:
        report = f"🚨 Audit Complete: Found {len(state['discrepancies'])} possible overcharges."
    return {"final_report": report}