### Please download the zip file for reference

Run the following commands in **PowerShell**:

```powershell
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app:app --reload
```

* `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process` — *Run this if `.\.venv\Scripts\Activate.ps1` prompts an error.*
* `uvicorn app:app --reload --port 8080` — *Run this if `uvicorn app:app --reload` prompts an error.*
