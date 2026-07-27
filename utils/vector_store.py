import os
from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import Chroma
from dotenv import load_dotenv

load_dotenv()

def get_retriever():
    persist_dir = "./data/chroma_db"
    # OpenAI Embeddings (Standard for RAG)
    embeddings = OpenAIEmbeddings(model="text-embedding-3-small")
    
    vectorstore = Chroma(
        persist_directory=persist_dir, 
        embedding_function=embeddings
    )
    return vectorstore.as_retriever(search_kwargs={"k": 2})