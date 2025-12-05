from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import subprocess
import sys
import os
import requests
from typing import List, Optional
import base64
import re

app = FastAPI(title="Python Project Runner API")

# CORS Configuration - Allow Cloudflare Pages
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Change to your Cloudflare Pages URL in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ===== CONFIGURATION =====
# Replace these with your actual values
GITHUB_USERNAME = "ZOROOZZ"
GITHUB_REPO = "daily-python-progress"
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")  # Optional: for private repos
GITHUB_BRANCH = "main"  # or "master"

# ===== MODELS =====
class CodeExecutionRequest(BaseModel):
    code: str
    timeout: int = 10  # seconds

class DayFolder(BaseModel):
    day_number: int
    folder_name: str

class PythonFile(BaseModel):
    filename: str
    path: str

# ===== GITHUB API HELPERS =====
def get_github_headers():
    headers = {"Accept": "application/vnd.github.v3+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"token {GITHUB_TOKEN}"
    return headers

def fetch_repo_contents(path=""):
    """Fetch contents from GitHub repository"""
    url = f"https://api.github.com/repos/{GITHUB_USERNAME}/{GITHUB_REPO}/contents/{path}"
    response = requests.get(url, headers=get_github_headers())
    
    if response.status_code == 404:
        raise HTTPException(status_code=404, detail="Repository or path not found")
    elif response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail="GitHub API error")
    
    return response.json()

def get_file_content(path):
    """Get file content from GitHub"""
    url = f"https://api.github.com/repos/{GITHUB_USERNAME}/{GITHUB_REPO}/contents/{path}"
    response = requests.get(url, headers=get_github_headers())
    
    if response.status_code != 200:
        raise HTTPException(status_code=404, detail="File not found")
    
    data = response.json()
    content = base64.b64decode(data['content']).decode('utf-8')
    return content

# ===== API ENDPOINTS =====

@app.get("/")
def read_root():
    return {
        "message": "Python Project Runner API",
        "version": "1.0",
        "endpoints": {
            "/api/days": "List all day folders",
            "/api/days/{day}/files": "List Python files in a day",
            "/api/file/{day}/{filename}": "Get file content",
            "/api/execute": "Execute Python code"
        }
    }

@app.get("/api/days", response_model=List[DayFolder])
def list_days():
    """List all day folders from the repository"""
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
        
        # Sort by day number
        day_folders.sort(key=lambda x: x['day_number'])
        return day_folders
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/days/{day_number}/files", response_model=List[PythonFile])
def list_files_in_day(day_number: int):
    """List all Python files in a specific day folder"""
    try:
        # First, find the exact folder name
        contents = fetch_repo_contents()
        day_pattern = re.compile(r'[Dd]ay[_\s]*' + str(day_number) + r'\b', re.IGNORECASE)
        
        folder_name = None
        for item in contents:
            if item['type'] == 'dir' and day_pattern.search(item['name']):
                folder_name = item['name']
                break
        
        if not folder_name:
            raise HTTPException(status_code=404, detail=f"Day {day_number} folder not found")
        
        # Get files in the folder
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
def get_file(day_number: int, filename: str):
    """Get content of a specific Python file"""
    try:
        # Find the day folder
        contents = fetch_repo_contents()
        day_pattern = re.compile(r'[Dd]ay\s*' + str(day_number) + r'\b', re.IGNORECASE)
        
        folder_name = None
        for item in contents:
            if item['type'] == 'dir' and day_pattern.search(item['name']):
                folder_name = item['name']
                break
        
        if not folder_name:
            raise HTTPException(status_code=404, detail=f"Day {day_number} folder not found")
        
        # Get file content
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
def execute_code(request: CodeExecutionRequest):
    """Execute Python code in a sandboxed environment"""
    try:
        # Create a temporary file to execute
        with open("/tmp/temp_script.py", "w") as f:
            f.write(request.code)
        
        # Execute with timeout and capture output
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
            "return_code": result.returncode
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
        # Clean up temp file
        if os.path.exists("/tmp/temp_script.py"):
            os.remove("/tmp/temp_script.py")

# Health check for Render
@app.get("/health")
def health_check():
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
