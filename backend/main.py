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
REPORT="LEGAL DOCUMENT ANALYSIS\n======================\n\nDocument: sample1\nAnalysis Date: 2025-03-22 12:31:38\nRisk Score: 48 (Medium Risk)\n\n# Summary\n\n### Comprehensive Summary of the Confidentiality and Technology Services Agreement\n\n#### 1. Key Contract Terms and Obligations\n- **Services Provided**: TechFusion will provide technology services, including software solutions and data processing services, to DataWorks.\n- **Confidentiality**: Both parties agree to maintain the confidentiality of sensitive information, including business strategies, technical designs, and financial information.\n- **Data Security**: Both parties will implement robust data security measures, such as encryption protocols and multi-factor authentication.\n- **Performance Standards**: TechFusion will use iterative development processes and agile project management techniques to ensure deliverables meet agreed specifications.\n- **Cooperation**: DataWorks will provide necessary cooperation and support, including timely access to relevant data or personnel.\n\n#### 2. Important Deadlines and Dates\n- **Effective Date**: December 15, 2024.\n- **Term**: The agreement will continue for two (2) years unless terminated earlier.\n- **Notice Periods**:\n  - **Termination for Cause**: 30 days to cure a breach.\n  - **Termination for Convenience**: 60 days' notice.\n\n#### 3. Potential Legal Risks or Ambiguities\n- **Security Breaches**: Despite robust measures, the agreement acknowledges that no system is infallible, which could lead to potential security breaches.\n- **Dispute Resolution**: The agreement favors mediation and arbitration, which could be time-consuming and costly.\n- **Ambiguity in Scope Modifications**: Any modifications to the scope of services require written agreement, which could lead to delays or disputes if not properly documented.\n\n#### 4. Rights and Responsibilities of Each Party\n- **TechFusion**:\n  - Provide technology services in a professional manner.\n  - Implement and maintain data security measures.\n  - Notify DataWorks of any security breaches.\n- **DataWorks**:\n  - Provide necessary cooperation and support.\n  - Implement and maintain data security measures.\n  - Notify TechFusion of any security breaches.\n\n#### 5. Termination Conditions\n- **Termination for Cause**: Either party can terminate the agreement if the other party fails to comply with any material provision and the breach remains uncured for 30 days.\n- **Termination for Convenience**: Either party can terminate the agreement by providing 60 days' written notice.\n\n#### 6. Jurisdiction Handling\n- **Dispute Resolution**: Any disputes will be resolved through mediation and, if necessary, binding arbitration in accordance with the rules of the American Arbitration Association. The jurisdiction for arbitration will be mutually acceptable to both parties.\n\n#### 7. Parameters for Analysis\n- **Contract Terms**: Services provided, confidentiality, data security, performance standards, and cooperation.\n- **Deadlines**: Effective date, term, and notice periods.\n- **Legal Risks**: Security breaches, dispute resolution, and scope modifications.\n- **Rights and Responsibilities**: Obligations of TechFusion and DataWorks.\n- **Termination Conditions**: Conditions for termination for cause and convenience.\n- **Jurisdiction**: Dispute resolution and arbitration jurisdiction.\n\n#### 8. Short Summary\nThe Confidentiality and Technology Services Agreement between TechFusion Dynamics Inc. and Global DataWorks LLC establishes a framework for providing technology services while ensuring confidentiality and data security. The agreement spans two years, with provisions for termination for cause or convenience. Both parties are responsible for implementing robust security measures and notifying each other of any breaches. Disputes will be resolved through mediation and arbitration, with the agreement superseding all prior communications. The document emphasizes mutual cooperation and adherence to legal and regulatory standards.\n\nThis summary highlights the key legal elements, deadlines, risks, and responsibilities, providing a clear overview of the agreement's terms and conditions.\n\n# Key Insights\n\nNone"

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
    allow_origins=["*"], 
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
                        "report": data.get("answer", None),
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

    file_bytes = await file.read()
    if len(file_bytes) > 10 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File too large (max 10MB)")

    try:
        pdf_doc = fitz.open(stream=file_bytes, filetype="pdf")
    except Exception:
        raise HTTPException(status_code=400, detail="Error processing PDF file")

    extracted_text = ""
    for page in pdf_doc:
        extracted_text += page.get_text()
    pdf_doc.close()

    cleaned_text = cleanText(extracted_text)

    document = {
        "pdf_text": cleaned_text,
        "state": "processed",
        "report": "processing...",
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

    asyncio.create_task(process(doc_id, file_bytes))  # Pass raw bytes

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
        print(os.getenv("REPORT"))
        return {
            "report": os.getenv("REPORT")
            # "report": document.get("report"),
            # "summary": document.get("summary"),
            # "clauses": document.get("clauses"),
            # "riskScore": document.get("riskScore"),
            # "riskFactors": document.get("riskFactors"),
            # "insights": document.get("insights"),
            # "entities": document.get("entities"),
            # "pdf": document.get("pdf")
        }
    else:
        return Response(status_code=204)

@app.post("/ask")
async def ask_chatbot(request_data: list[AskRequest]):
    try:
        if not request_data or not isinstance(request_data, list):
            raise HTTPException(status_code=400, detail="Invalid request format")

        doc_id = request_data[0].doc_id
        query = request_data[0].query

        # Fetch document from MongoDB
        document = docColn.find_one({"_id": ObjectId(doc_id)})
        if not document:
            raise HTTPException(status_code=404, detail="Document not found")

        pdf_text = document.get("pdf_text")
        if not pdf_text:
            raise HTTPException(status_code=400, detail="PDF content not available")

        # Convert text back to bytes (optional: handle binary PDF if stored)
        pdf_bytes = pdf_text.encode("utf-8")  

        async with httpx.AsyncClient() as client:
            # Step 1: Upload the PDF to ML backend
            files = {"file": ("document.pdf", pdf_bytes, "application/pdf")}
            upload_response = await client.post("http://127.0.0.1:8000/upload", files=files)
            upload_response.raise_for_status()

            upload_data = upload_response.json()
            ml_doc_id = upload_data.get("doc_id")

            if not ml_doc_id:
                raise HTTPException(status_code=500, detail="ML backend failed to process document")

            # Step 2: Send the query to ML backend
            ask_response = await client.post("http://127.0.0.1:8000/ask", json={"query": query})
            ask_response.raise_for_status()

            answer_data = ask_response.json()
            answer = answer_data.get("answer", "No response from ML backend")

            # Step 3: Store Q&A in MongoDB
            docColn.update_one(
                {"_id": ObjectId(doc_id)},
                {"$push": {"chat": {"query": query, "answer": answer}}}
            )

            return {"answer": answer}

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error processing request: {str(e)}")

    
# @app.post("/ask")
# async def ask_chatbot(request: AskRequest, current_user: dict = Depends(decodeUser)):
#     try:
#         # Check if document belongs to the current user
#         if request.doc_id not in current_user.get("docs", []):
#             raise HTTPException(status_code=403, detail="Document doesn't belong to the user")
        
#         # Retrieve document from database
#         document = docColn.find_one({"_id": ObjectId(request.doc_id)})
#         if not document:
#             raise HTTPException(status_code=404, detail="Document not found")
        
#         # Extract context from the document
#         context = document.get("pdf_text", "")
        
#         # Get the report data
#         report = document.get("report", "")
#         summary = document.get("summary", "")
        
#         # Create a context that combines document text with analysis
#         enhanced_context = f"""
#         DOCUMENT TEXT:
#         {context}
        
#         DOCUMENT ANALYSIS:
#         {report}
        
#         DOCUMENT SUMMARY:
#         {summary}
#         """
        
#         # Import the Gemini library
#         import google.generativeai as genai
        
#         # Configure the Gemini API
#         API_KEY =  "AIzaSyBB7HKiIIGUmyAOCAqbS0eMCFvcSmFH-70"
        
#         genai.configure(api_key=API_KEY)
        
#         # Set up the model
#         model = genai.GenerativeModel('gemini-pro')
        
#         # Create prompt with context and query
#         prompt = f"""
#         Based on the following document information, please answer the question.
        
#         {enhanced_context}
        
#         QUESTION:
#         {request.query}
        
#         Please provide a clear, concise, and accurate response based only on the information in the document.
#         If the answer cannot be found in the document, please state that clearly.
#         """
        
#         # Get response from Gemini
#         response = model.generate_content(prompt)
        
#         # Extract the answer
#         answer = response.text
        
#         # Store the chat history in the database
#         docColn.update_one(
#             {"_id": ObjectId(request.doc_id)}, 
#             {"$push": {"chat": {"query": request.query, "answer": answer, "timestamp": datetime.now()}}}
#         )
        
#         return {"answer": answer}
#     except Exception as e:
#         # Log the error for debugging
#         print(f"Error in ask_chatbot: {str(e)}")
#         raise HTTPException(status_code=500, detail=f"Error processing request: {str(e)}")