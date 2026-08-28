# CDDE Prototype — Capacity & Demand Decision Engine

A runnable MVP based on the supplied Capacity Allocation & Integrated Demand Planning proposal.

## Included
- Plant Command Center
- Integrated internal/external Demand Register
- 12-week demand vs capacity view
- Constrained-capacity allocation optimiser
- Programme-delay economic value
- Allocation Workbench with audit decisions
- What-if Scenario Simulator
- Exception Center
- AI-assisted WhatsApp-style extraction demo with human validation
- SQLite demo database and seeded Plant 02 / AAC Block data

## Run

### Windows PowerShell
```powershell
cd cdde_prototype
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app:app --reload
```
Open http://127.0.0.1:8000

### Windows CMD
```bat
cd cdde_prototype
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
uvicorn app:app --reload
```

## Demo flow
1. Open Command Center.
2. Go to Allocation Workbench and run the optimiser.
3. Review the recommended allocation and the contribution / programme value.
4. Open Scenario Simulator and test a margin-only style allocation versus a project-protection scenario.
5. Add a demand manually or use AI Intake to turn an example WhatsApp message into Probable demand.
6. Approve/modify an allocation; the choice is saved in the audit table.
7. Open Exception Center to see capacity and programme risks.

## Design notes
- The optimiser is intentionally explainable and keeps a human decision step.
- AI extraction is a local demo parser rather than an external LLM dependency, so the prototype works offline.
- The data is illustrative and should be replaced by pilot-plant data before business decisions are made.
