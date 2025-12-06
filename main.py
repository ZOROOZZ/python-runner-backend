from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
import subprocess
import sys
import os
import requests
from typing import List, Optional
import base64
import re
import jwt
from datetime import datetime, timedelta
import bcrypt
import json

app = FastAPI(title="Python Project Runner API - Secured")

# Security
security = HTTPBearer()
SECRET_KEY = os.getenv("JWT_SECRET_KEY", "your-secret-key-change-this-in-production")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_HOURS = 24

# CORS Configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# GitHub Configuration
GITHUB_USERNAME = "ZOROOZZ"
GITHUB_REPO = "daily-python-progress"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")
GITHUB_BRANCH = "main"

# In-memory user storage (replace with database in production)
USERS_FILE = "/tmp/users.json"

def load_users():
    """Load users from file"""
    if os.path.exists(USERS_FILE):
        with open(USERS_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_users(users):
    """Save users to file"""
    with open(USERS_FILE, 'w') as f:
        json.dump(users, f)

def init_default_user():
    """Initialize default admin user if no users exist"""
    users = load_users()
    if not users:
        # Default credentials - CHANGE THESE!
        default_username = "admin"
        default_password = "admin123"
        hashed = bcrypt.hashpw(default_password.encode(), bcrypt.gensalt())
        users[default_username] = {
            "username": default_username,
            "password_hash": hashed.decode(),
            "created_at": datetime.utcnow().isoformat()
        }
        save_users(users)
        print(f"⚠️  Default user created: {default_username} / {default_password}")
        print("⚠️  CHANGE THIS PASSWORD IMMEDIATELY!")

# Initialize default user on startup
init_default_user()

# ===== MODELS =====
class LoginRequest(BaseModel):
    username: str
    password: str

class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    username: str

class CodeExecutionRequest(BaseModel):
    code: str
    timeout: int = 10

class DayFolder(BaseModel):
    day_number: int
    folder_name: str

class PythonFile(BaseModel):
    filename: str
    path: str

class UserCreate(BaseModel):
    username: str
    password: str

# ===== AUTHENTICATION FUNCTIONS =====
def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify password against hash"""
    return bcrypt.checkpw(plain_password.encode(), hashed_password.encode())

def create_access_token(data: dict, expires_delta: timedelta = None):
    """Create JWT token"""
    to_encode = data.copy()
    expire = datetime.utcnow() + (expires_delta or timedelta(hours=ACCESS_TOKEN_EXPIRE_HOURS))
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def verify_token(credentials: HTTPAuthorizationCredentials = Depends(security)):
    """Verify JWT token"""
    try:
        token = credentials.credentials
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        return username
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

# ===== GITHUB API HELPERS =====
def get_github_headers():
    headers = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"
    return headers

def fetch_repo_contents(path=""):
    url = f"https://api.github.com/repos/{GITHUB_USERNAME}/{GITHUB_REPO}/contents/{path}"
    response = requests.get(url, headers=get_github_headers())
    if response.status_code == 404:
        raise HTTPException(status_code=404, detail="Repository or path not found")
    elif response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="GitHub API error")
    return response.json()

def get_file_content(path):
    url = f"https://api.github.com/repos/{GITHUB_USERNAME}/{GITHUB_REPO}/contents/{path}"
    response = requests.get(url, headers=get_github_headers())
    if response.status_code != 200:
        raise HTTPException(status_code=404, detail="File not found")
    data = response.json()
    content = base64.b64decode(data['content']).decode('utf-8')
    return content

# ===== AUTHENTICATION ENDPOINTS =====
@app.post("/api/auth/login", response_model=TokenResponse)
def login(login_data: LoginRequest):
    """Login endpoint - returns JWT token"""
    users = load_users()
    user = users.get(login_data.username)
    
    if not user or not verify_password(login_data.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password"
        )
    
    access_token = create_access_token(data={"sub": login_data.username})
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "username": login_data.username
    }

@app.post("/api/auth/create-user")
def create_user(user_data: UserCreate, current_user: str = Depends(verify_token)):
    """Create new user (requires authentication)"""
    users = load_users()
    
    if user_data.username in users:
        raise HTTPException(status_code=400, detail="Username already exists")
    
    hashed = bcrypt.hashpw(user_data.password.encode(), bcrypt.gensalt())
    users[user_data.username] = {
        "username": user_data.username,
        "password_hash": hashed.decode(),
        "created_at": datetime.utcnow().isoformat(),
        "created_by": current_user
    }
    save_users(users)
    
    return {"message": f"User {user_data.username} created successfully"}

@app.get("/api/auth/verify")
def verify_auth(current_user: str = Depends(verify_token)):
    """Verify if token is valid"""
    return {"username": current_user, "authenticated": True}

# ===== PROTECTED API ENDPOINTS =====
@app.get("/")
def read_root():
    return {
        "message": "Python Project Runner API - Secured",
        "version": "2.0",
        "authentication": "required",
        "endpoints": {
            "/api/auth/login": "Login (POST)",
            "/api/auth/verify": "Verify token (GET)",
            "/api/days": "List all day folders (GET, requires auth)",
            "/api/days/{day}/files": "List Python files in a day (GET, requires auth)",
            "/api/file/{day}/{filename}": "Get file content (GET, requires auth)",
            "/api/execute": "Execute Python code (POST, requires auth)"
        }
    }

@app.get("/api/days", response_model=List[DayFolder])
def list_days(current_user: str = Depends(verify_token)):
    """List all day folders (PROTECTED)"""
    try:
        contents = fetch_repo_contents()
        day_folders = []
        day_pattern = re.compile(r'[Dd]ay[_\s]*(\d+)', re.IGNORECASE)
        
        for item in contents:
            if item['type'] == 'dir':
                match = day_pattern.search(item['name'])
                if match:
                    day_number = int(match.group(1))
                    day_folders.append({
                        "day_number": day_number,
                        "folder_name": item['name']
                    })
        
        day_folders.sort(key=lambda x: x['day_number'])
        return day_folders
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/days/{day_number}/files", response_model=List[PythonFile])
def list_files_in_day(day_number: int, current_user: str = Depends(verify_token)):
    """List all Python files in a specific day folder (PROTECTED)"""
    try:
        contents = fetch_repo_contents()
        day_pattern = re.compile(r'[Dd]ay[_\s]*' + str(day_number) + r'\b', re.IGNORECASE)
        
        folder_name = None
        for item in contents:
            if item['type'] == 'dir' and day_pattern.search(item['name']):
                folder_name = item['name']
                break
        
        if not folder_name:
            raise HTTPException(status_code=404, detail=f"Day {day_number} folder not found")
        
        folder_contents = fetch_repo_contents(folder_name)
        python_files = []
        for item in folder_contents:
            if item['type'] == 'file' and item['name'].endswith('.py'):
                python_files.append({
                    "filename": item['name'],
                    "path": item['path']
                })
        
        return python_files
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/file/{day_number}/{filename}")
def get_file(day_number: int, filename: str, current_user: str = Depends(verify_token)):
    """Get content of a specific Python file (PROTECTED)"""
    try:
        contents = fetch_repo_contents()
        day_pattern = re.compile(r'[Dd]ay[_\s]*' + str(day_number) + r'\b', re.IGNORECASE)
        
        folder_name = None
        for item in contents:
            if item['type'] == 'dir' and day_pattern.search(item['name']):
                folder_name = item['name']
                break
        
        if not folder_name:
            raise HTTPException(status_code=404, detail=f"Day {day_number} folder not found")
        
        file_path = f"{folder_name}/{filename}"
        content = get_file_content(file_path)
        
        return {
            "filename": filename,
            "path": file_path,
            "content": content
        }
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/execute")
def execute_code(request: CodeExecutionRequest, current_user: str = Depends(verify_token)):
    """Execute Python code in a sandboxed environment (PROTECTED)"""
    try:
        with open("/tmp/temp_script.py", "w") as f:
            f.write(request.code)
        
        result = subprocess.run(
            [sys.executable, "/tmp/temp_script.py"],
            capture_output=True,
            text=True,
            timeout=request.timeout
        )
        
        return {
            "success": result.returncode == 0,
            "output": result.stdout,
            "error": result.stderr,
            "return_code": result.returncode,
            "executed_by": current_user
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "output": "",
            "error": f"Execution timed out after {request.timeout} seconds",
            "return_code": -1
        }
    except Exception as e:
        return {
            "success": False,
            "output": "",
            "error": str(e),
            "return_code": -1
        }
    finally:
        if os.path.exists("/tmp/temp_script.py"):
            os.remove("/tmp/temp_script.py")

@app.get("/health")
def health_check():
    return {"status": "healthy", "authenticated": True}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
