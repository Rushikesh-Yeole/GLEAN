from fastapi import FastAPI, File, UploadFile, HTTPException, Depends, Header, Response
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

class AuthData(BaseModel):
    email: str
    password: str

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

# upload doc pdf
@app.post("/doc")
async def upload_file(file: UploadFile = File(...), current_user: dict = Depends(decodeUser)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are allowed")
    content = await file.read()
    # Limit file size to 10MB
    if len(content) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 10MB)")
    
    # PDF to text
    try:
        doc = fitz.open(stream=content, filetype="pdf")
    except Exception:
        raise HTTPException(status_code=400, detail="Error processing PDF file")

    extracted_text = ""
    for page in doc:
        extracted_text += page.get_text()
    doc.close()
    
    cleaned_text = cleanText(extracted_text)
    
    document = {
        "pdf": content,
        "extract": cleaned_text,
        "state": "processing", 
        "summary": None,
        "clauses": None,
        "risk_score": None,
        "risk_factors": None,
        "actionable_insights": None,
        "entity_info": None
    }
    result = docColn.insert_one(document)
    doc_id = str(result.inserted_id)
    
    userColn.update_one({"email": current_user["email"]}, {"$push": {"docs": doc_id}})
    
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
    
    if document.get("state") == "processed":
        return {
            "summary": document.get("summary"),
            "clauses": document.get("clauses"),
            "risk_score": document.get("risk_score"),
            "risk_factors": document.get("risk_factors"),
            "actionable_insights": document.get("actionable_insights"),
            "entity_info": document.get("entity_info"),
            "pdf": document.get("pdf")
        }
    else:
        return Response(status_code=204)
