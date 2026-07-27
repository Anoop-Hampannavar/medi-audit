from typing import TypedDict, List
from pydantic import BaseModel, Field

class AuditState(TypedDict):
    bill_image: str  # Base64 string from Streamlit
    extracted_items: List[dict]
    discrepancies: List[dict]
    final_report: str