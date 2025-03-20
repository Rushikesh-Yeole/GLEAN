import os
from datetime import datetime, timedelta
from passlib.context import CryptContext
from jose import jwt
from fastapi import HTTPException

SECRET_KEY = os.getenv("JWT")
ALGORITHM = "HS256"
TOKEN_EXPIRE_DAYS = 30

pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash(password: str) -> str:
    return pwd.hash(password)

def matchPswrd(plain: str, hashed: str) -> bool:
    return pwd.verify(plain, hashed)

def create_token(email: str) -> str:
    expire = datetime.utcnow() + timedelta(days=TOKEN_EXPIRE_DAYS)
    expire_timestamp = int(expire.timestamp())  # Convert datetime to Unix timestamp
    return jwt.encode({"sub": email, "exp": expire_timestamp}, SECRET_KEY, algorithm=ALGORITHM)

def login(email: str, password: str, db) -> None:
    user = db.find_one({"email": email})
    if user:
        if not matchPswrd(password, user["pswrd"]):
            raise HTTPException(status_code=400, detail="Incorrect password")
    else:
        db.insert_one({"email": email, "pswrd": hash(password)})
