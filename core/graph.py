from langgraph.graph import StateGraph, START, END
from core.state import AuditState
from core.nodes import extraction_node, audit_node, report_node

builder = StateGraph(AuditState)

builder.add_node("extract", extraction_node)
builder.add_node("audit", audit_node)
builder.add_node("write", report_node)

builder.add_edge(START, "extract")
builder.add_edge("extract", "audit")
builder.add_edge("audit", "write")
builder.add_edge("write", END)

app_agent = builder.compile()