import asyncio
import httpx
from fastapi import FastAPI, File, UploadFile, HTTPException, Depends, Header, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from bson import ObjectId
from pymongo import MongoClient
from dotenv import load_dotenv
import os
import fitz  #PyMuPDF
import re
from auth import create_token, login
from jose import jwt, JWTError
from datetime import timedelta, datetime

load_dotenv()
MONGODB_URI = os.getenv("MONGODB_URI")
if not MONGODB_URI:
    raise Exception("MONGODB_URI is not set.")
client = MongoClient(MONGODB_URI)
db = client["glean"]
userColn = db["users"]
docColn = db["documents"]

app = FastAPI()
SECRET_KEY = os.getenv("JWT")
ALGORITHM = "HS256"

origins = [
    "http://localhost:5173",
    "https://glean-frontend.com",
]

#CORS 
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

class AuthData(BaseModel):
    email: str
    password: str

class ChatRequest(BaseModel):
    doc_id: str

class AskRequest(BaseModel):
    doc_id: str
    query: str

@app.head("/")
async def head_root():
    return Response(status_code=200)

@app.get("/")
async def root():
    return {"message": "GLEAN backend is up"}

@app.post("/auth")
async def auth(auth_data: AuthData):
    login(auth_data.email, auth_data.password, userColn)
    token = create_token(auth_data.email)
    return {"access_token": token, "token_type": "bearer"}


def cleanText(text: str) -> str:
    return re.sub(r'\s+', ' ', text).strip()

def decodeUser(authorization: str = Header(...)):
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid authorization header")
    token = authorization.split(" ")[1]
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        email = payload.get("sub")
        if email is None:
            raise HTTPException(status_code=401, detail="Token missing subject")
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = userColn.find_one({"email": email})
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user

# process pdf
async def process(doc_id: str, pdf: bytes):
    try:
        async with httpx.AsyncClient() as client:
            file = {'file': ('document.pdf', pdf, 'application/pdf')}
            response = await client.post(os.getenv("ML_BE") + "/upload", files=file)
            response.raise_for_status()
            data = response.json()
            if data:
                docColn.update_one(
                    {"_id": ObjectId(doc_id)},
                    {   "$set": {
                        "state": "processed",
                        "summary": data.get("summary", None),
                        "clauses": data.get("clauses", None),
                        "riskScore": data.get("riskScore", None),
                        "riskFactors": data.get("riskFactors", None),
                        "insights": data.get("insights", None),
                        "entities": data.get("entities", None),
                        }})
    except Exception as e:
        print(f"Error processing document {doc_id}: {e}")

# upload doc pdf
@app.post("/doc")
async def uploadDoc(file: UploadFile = File(...), current_user: dict = Depends(decodeUser)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed")
    file = await file.read()
    if len(file) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 10MB)")
    
    try:
        file = fitz.open(stream=file, filetype="pdf")
    except Exception:
        raise HTTPException(status_code=400, detail="Error processing PDF file")

    extracted_text = ""
    for page in file:
        extracted_text += page.get_text()
    file.close()
    
    cleaned_text = cleanText(extracted_text)
    
    document = {
        "pdf": file,
        "state": "processing",
        "summary": None,
        "clauses": None,
        "riskScore": None,
        "riskFactors": None,
        "insights": None,
        "entities": None
    }
    result = docColn.insert_one(document)
    doc_id = str(result.inserted_id)
    
    userColn.update_one({"email": current_user["email"]}, {"$push": {"docs": doc_id}})
    
    asyncio.create_task(process(doc_id, file))
    
    return {"document_id": doc_id}
 
@app.get("/user/docs")
async def get_user_docs(current_user: dict = Depends(decodeUser)):
    docs = current_user.get("docs", [])
    return {"docs": docs}

# get report
@app.get("/user/docs/{doc_id}")
async def report(doc_id: str, current_user: dict = Depends(decodeUser)):
    if doc_id not in current_user.get("docs", []):
        raise HTTPException(status_code=403, detail="Document doesn't belong to the user")
    document = docColn.find_one({"_id": ObjectId(doc_id)})
    if not document:
        raise HTTPException(status_code=404, detail="Document not found")
    
    if document.get("state") != "processing":
        return {
            "summary": document.get("summary"),
            "clauses": document.get("clauses"),
            "riskScore": document.get("riskScore"),
            "riskFactors": document.get("riskFactors"),
            "insights": document.get("insights"),
            "entities": document.get("entities"),
            "pdf": document.get("pdf")
        }
    else:
        return Response(status_code=204)

@app.post("/ask")
async def ask_chatbot(request: AskRequest):
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(os.getenv("ML_BE") + "/ask", json={"query": request.query})
            response.raise_for_status()
            data = response.json()
            answer = data.get("answer", "ML backend not responding")
            
            docColn.update_one({"_id": ObjectId(request.doc_id)}, {"$push": {"chat": {"query": request.query, "answer": answer}}})
            
            return {"answer": answer}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error communicating with ML backend: {e}")
    
@app.post("/chat")
async def getChat(request: ChatRequest):
    doc = docColn.find_one({"_id": ObjectId(request.doc_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"chat": doc.get("chat", [])}