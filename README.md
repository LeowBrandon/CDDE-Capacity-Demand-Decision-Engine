### Please download the zip file for reference

Extract the ZIP, open PowerShell in the cdde_prototype folder, then
run the following commands in **PowerShell**:

```powershell
cd "C:\Users\user\Downloads\cdde_prototype\cdde_prototype"
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app:app --reload
```

* `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope Process` — *Run this if `.\.venv\Scripts\Activate.ps1` prompts an error.*
* `uvicorn app:app --reload --port 8080` — *Run this if `uvicorn app:app --reload` prompts an error.*
