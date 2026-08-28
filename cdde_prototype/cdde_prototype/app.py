from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
import sqlite3, os, json, re
from datetime import date, datetime, timedelta
from scipy.optimize import milp, Bounds, LinearConstraint
import numpy as np

BASE = os.path.dirname(__file__)
DB = os.path.join(BASE, 'data', 'cdde.db')
app = FastAPI(title='CDDE - Capacity & Demand Decision Engine', version='0.1.0')

SCHEMA = '''
CREATE TABLE IF NOT EXISTS demands (
 id INTEGER PRIMARY KEY,
 demand_id TEXT UNIQUE,
 demand_type TEXT NOT NULL,
 customer_name TEXT,
 project_name TEXT,
 plant TEXT NOT NULL,
 product TEXT NOT NULL,
 quantity REAL NOT NULL,
 allocated REAL DEFAULT 0,
 required_date TEXT NOT NULL,
 latest_date TEXT,
 margin_per_unit REAL NOT NULL,
 programme_days REAL DEFAULT 0,
 programme_cost_per_day REAL DEFAULT 0,
 contractual_priority REAL DEFAULT 50,
 urgency_score REAL DEFAULT 50,
 confidence TEXT DEFAULT 'Firm',
 source_type TEXT DEFAULT 'Manual',
 status TEXT DEFAULT 'Open'
);
CREATE TABLE IF NOT EXISTS capacities (
 id INTEGER PRIMARY KEY,
 plant TEXT NOT NULL,
 product TEXT NOT NULL,
 production_date TEXT NOT NULL,
 theoretical REAL NOT NULL,
 maintenance REAL DEFAULT 0,
 downtime REAL DEFAULT 0,
 committed REAL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS allocations (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 run_time TEXT,
 demand_id TEXT,
 recommended REAL,
 score REAL,
 contribution REAL,
 programme_protected REAL,
 decision TEXT DEFAULT 'Recommended',
 reason TEXT
);
CREATE TABLE IF NOT EXISTS audits (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 timestamp TEXT,
 demand_id TEXT,
 recommended REAL,
 actual REAL,
 reason TEXT,
 user TEXT DEFAULT 'demo-supervisor'
);
CREATE TABLE IF NOT EXISTS ai_intake (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 timestamp TEXT,
 raw_text TEXT,
 extracted_json TEXT,
 confidence REAL,
 status TEXT DEFAULT 'Pending Review'
);
'''

SEED_DEMANDS = [
 ('EXT-A01','External','ABC Contractor','', 'Plant 02','AAC Block',800, '2026-08-28', '2026-08-29',120,0,0,80,90,'Firm','Sales Order'),
 ('EXT-B01','External','BuildMax','', 'Plant 02','AAC Block',700, '2026-08-28', '2026-08-29',140,0,0,85,95,'Firm','Sales Order'),
 ('INT-X01','Internal','','Project X','Plant 02','AAC Block',900,'2026-08-28','2026-08-30',90,7,23000,90,100,'Firm','Project Schedule'),
 ('INT-Y01','Internal','','Project Y','Plant 02','AAC Block',600,'2026-08-29','2026-08-31',105,2,20000,70,85,'Firm','Project Schedule'),
 ('EXT-C01','External','MegaBuild','','Plant 02','AAC Block',300,'2026-08-30','2026-09-01',130,0,0,65,70,'Probable','WhatsApp'),
 ('INT-Z01','Internal','','Project Z','Plant 02','AAC Block',500,'2026-09-02','2026-09-04',95,3,12000,75,70,'Forecast','Project Pipeline'),
 ('EXT-D01','External','UrbanWorks','','Plant 02','AAC Block',450,'2026-09-03','2026-09-04',125,0,0,60,65,'Probable','Quotation')
]


def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    c = conn(); c.executescript(SCHEMA)
    if c.execute('SELECT COUNT(*) FROM demands').fetchone()[0] == 0:
        for i, d in enumerate(SEED_DEMANDS, 1):
            c.execute('''INSERT INTO demands
            (id,demand_id,demand_type,customer_name,project_name,plant,product,quantity,required_date,latest_date,margin_per_unit,programme_days,programme_cost_per_day,contractual_priority,urgency_score,confidence,source_type)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''', (i,)+d)
    if c.execute('SELECT COUNT(*) FROM capacities').fetchone()[0] == 0:
        today = date(2026,8,28)
        for i in range(12):
            dt = (today + timedelta(days=7*i)).isoformat()
            # 5,000 m3 per week planning capacity; actual day-of-use can still be changed by supervisor.
            c.execute('INSERT INTO capacities(plant,product,production_date,theoretical,maintenance,downtime,committed) VALUES (?,?,?,?,?,?,?)',
                      ('Plant 02','AAC Block',dt,2500 if i==0 else 5000,300 if i==0 else 200,200 if i==0 else 100,0))
    c.commit(); c.close()

init_db()

class DemandIn(BaseModel):
    demand_type: str
    customer_name: str = ''
    project_name: str = ''
    plant: str = 'Plant 02'
    product: str = 'AAC Block'
    quantity: float = Field(gt=0)
    required_date: str
    latest_date: str | None = None
    margin_per_unit: float = 0
    programme_days: float = 0
    programme_cost_per_day: float = 0
    contractual_priority: float = 50
    urgency_score: float = 50
    confidence: str = 'Firm'
    source_type: str = 'Manual'

class AllocationDecision(BaseModel):
    demand_id: str
    actual: float
    reason: str = ''
    user: str = 'demo-supervisor'

class ScenarioIn(BaseModel):
    allocations: dict[str, float]


def planning_weight(conf):
    return {'Firm':1.0,'Probable':0.7,'Forecast':0.3}.get(conf,1.0)


def demand_rows(plant='Plant 02'):
    c=conn(); rows=c.execute('SELECT * FROM demands WHERE plant=? AND status != "Closed" ORDER BY required_date, demand_id',(plant,)).fetchall(); c.close(); return rows


def effective_capacity(plant='Plant 02'):
    c=conn(); rows=c.execute('SELECT * FROM capacities WHERE plant=? ORDER BY production_date',(plant,)).fetchall(); c.close()
    out=[]
    for r in rows:
        out.append({**dict(r), 'available': max(0, r['theoretical']-r['maintenance']-r['downtime']-r['committed'])})
    return out


def allocate(plant='Plant 02', available=None):
    rows = demand_rows(plant)
    if available is None:
        available = effective_capacity(plant)[0]['available']
    n=len(rows)
    if n==0:
        return {'capacity':available,'allocations':[],'contribution':0,'programme':0,'total_value':0}
    # Linearised value: contribution + programme-protection per unit; programme exposure is awarded when any allocation occurs.
    # For the demo, use per-unit programme value distributed across requested quantity.
    values=[]
    for r in rows:
        prog_total = r['programme_days'] * r['programme_cost_per_day']
        prog_per_unit = prog_total / max(r['quantity'],1)
        conf = planning_weight(r['confidence'])
        score_per_unit = r['margin_per_unit'] + prog_per_unit * conf + 0.15*r['urgency_score'] + 0.10*r['contractual_priority']
        values.append(score_per_unit)
    c=-np.array(values,dtype=float)
    ub=np.array([r['quantity'] for r in rows], dtype=float)
    # Firm first constraint effect is represented in score; forecast/probable are down-weighted.
    res=milp(c=c, integrality=np.zeros(n), bounds=Bounds(np.zeros(n),ub), constraints=LinearConstraint(np.ones((1,n)), -np.inf, available), options={'time_limit':2})
    if not res.success:
        # fallback greedy
        order=np.argsort(-np.array(values)); x=np.zeros(n); rem=available
        for j in order:
            q=min(ub[j],rem); x[j]=q; rem-=q
            if rem<=1e-9: break
    else:
        x=res.x
    result=[]; tc=tp=0
    for r,a in zip(rows,x):
        a=float(max(0,min(a,r['quantity'])))
        contribution=a*r['margin_per_unit']
        potential=r['programme_days']*r['programme_cost_per_day']
        protected=potential*(a/max(r['quantity'],1))
        score=values[len(result)]
        action='Allocate' if a >= r['quantity']-1e-6 else ('Partial' if a>1e-6 else 'Defer')
        tc+=contribution; tp+=protected
        result.append({'demand_id':r['demand_id'],'name':r['project_name'] or r['customer_name'],'type':r['demand_type'],'quantity':r['quantity'],'recommended':round(a,2),'margin':r['margin_per_unit'],'contribution':round(contribution,2),'programme_protected':round(protected,2),'score':round(score,2),'action':action,'confidence':r['confidence'],'required_date':r['required_date']})
    return {'capacity':available,'allocations':result,'contribution':round(tc,2),'programme':round(tp,2),'total_value':round(tc+tp,2)}


@app.get('/')
def home():
    return FileResponse(os.path.join(BASE,'static','index.html'))

@app.get('/api/dashboard')
def dashboard():
    rows=demand_rows(); cap=effective_capacity()[0]['available']
    firm=sum(r['quantity'] for r in rows if r['confidence']=='Firm' and r['required_date'] <= effective_capacity()[0]['production_date'])
    total=sum(r['quantity']*planning_weight(r['confidence']) for r in rows if r['required_date'] <= effective_capacity()[0]['production_date'])
    rec=allocate(available=cap)
    exceptions=[]
    if total>cap: exceptions.append({'level':'RED','title':'Capacity shortage','detail':f'{round(total-cap)} m³ planning gap','value':round(total-cap)})
    for a in rec['allocations']:
        if a['recommended'] < a['quantity'] and a['programme_protected']>0:
            exceptions.append({'level':'RED','title':a['name'],'detail':f'{a["quantity"]-a["recommended"]:.0f} m³ shortfall','value':a['programme_protected']})
        elif a['confidence']!='Firm':
            exceptions.append({'level':'AMBER','title':a['name'],'detail':f'Demand confidence: {a["confidence"]}','value':0})
    return {'plant':'Plant 02','capacity':cap,'firm_demand':firm,'planning_demand':round(total,2),'shortage':round(max(0,total-cap),2),'recommendation':rec,'exceptions':exceptions[:8]}

@app.get('/api/demands')
def demands():
    return [dict(r) for r in demand_rows()]

@app.post('/api/demands')
def add_demand(d:DemandIn):
    c=conn(); did=f"D{datetime.now().strftime('%m%d%H%M%S')}"
    c.execute('''INSERT INTO demands(demand_id,demand_type,customer_name,project_name,plant,product,quantity,required_date,latest_date,margin_per_unit,programme_days,programme_cost_per_day,contractual_priority,urgency_score,confidence,source_type)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(did,d.demand_type,d.customer_name,d.project_name,d.plant,d.product,d.quantity,d.required_date,d.latest_date,d.margin_per_unit,d.programme_days,d.programme_cost_per_day,d.contractual_priority,d.urgency_score,d.confidence,d.source_type)); c.commit(); c.close()
    return {'demand_id':did}

@app.get('/api/allocation')
def allocation():
    res=allocate();
    c=conn(); c.execute('DELETE FROM allocations'); now=datetime.now().isoformat()
    for a in res['allocations']:
        c.execute('INSERT INTO allocations(run_time,demand_id,recommended,score,contribution,programme_protected,decision,reason) VALUES(?,?,?,?,?,?,?,?)',(now,a['demand_id'],a['recommended'],a['score'],a['contribution'],a['programme_protected'],a['action'],'Optimiser recommendation'))
    c.commit(); c.close(); return res

@app.post('/api/allocation/decision')
def decision(d:AllocationDecision):
    c=conn(); rec=c.execute('SELECT recommended FROM allocations WHERE demand_id=? ORDER BY id DESC LIMIT 1',(d.demand_id,)).fetchone()
    if rec is None: raise HTTPException(404,'Run allocation first')
    c.execute('INSERT INTO audits(timestamp,demand_id,recommended,actual,reason,user) VALUES(?,?,?,?,?,?)',(datetime.now().isoformat(),d.demand_id,rec['recommended'],d.actual,d.reason,d.user));
    if d.actual > 0:
        c.execute('UPDATE demands SET allocated=? WHERE demand_id=?',(d.actual,d.demand_id))
    c.commit(); c.close(); return {'ok':True}

@app.post('/api/scenario')
def scenario(s:ScenarioIn):
    c=conn(); rows={r['demand_id']:r for r in c.execute('SELECT * FROM demands WHERE plant="Plant 02"').fetchall()}; c.close()
    cap=effective_capacity()[0]['available']; total=sum(max(0,v) for v in s.allocations.values())
    if total>cap: raise HTTPException(400,f'Scenario allocation {total:.0f} exceeds available capacity {cap:.0f}')
    contribution=programme=0
    details=[]
    for did,q in s.allocations.items():
        if did not in rows: continue
        r=rows[did]; q=min(q,r['quantity'])
        contribution+=q*r['margin_per_unit']
        programme+=(r['programme_days']*r['programme_cost_per_day'])*(q/max(r['quantity'],1))
        details.append({'demand_id':did,'name':r['project_name'] or r['customer_name'],'quantity':q})
    return {'capacity':cap,'allocated':total,'contribution':round(contribution,2),'programme':round(programme,2),'total_value':round(contribution+programme,2),'details':details}

@app.post('/api/ai/extract')
def ai_extract(payload:dict):
    text=payload.get('text','').strip()
    if not text: raise HTTPException(400,'Text required')
    qty_m=re.search(r'(\d+(?:\.\d+)?)\s*(?:m3|m³|cubes?)',text,re.I)
    project_m=re.search(r'(?:for|project)\s+([A-Za-z0-9 _-]+?)(?=\s+by\b|\s+on\b|\.|,|$)',text,re.I)
    qty=float(qty_m.group(1)) if qty_m else 0
    project=(project_m.group(1).strip() if project_m else 'Unknown Project')
    urgency='high' if any(w in text.lower() for w in ['urgent','pushing','asap','critical']) else 'normal'
    conf='probable' if 'tentative' in text.lower() or 'need' in text.lower() else 'firm'
    extracted={'project':project,'quantity':qty,'unit':'m3','required_date':'2026-08-28' if 'friday' in text.lower() else '2026-09-03','urgency':urgency,'confidence':conf,'source':'WhatsApp'}
    confidence=0.88 if qty and project!='Unknown Project' else 0.58
    c=conn(); c.execute('INSERT INTO ai_intake(timestamp,raw_text,extracted_json,confidence) VALUES(?,?,?,?)',(datetime.now().isoformat(),text,json.dumps(extracted),confidence)); c.commit(); c.close()
    return {'extracted':extracted,'confidence':confidence}

@app.post('/api/reset')
def reset():
    c=conn(); c.execute('DELETE FROM demands'); c.execute('DELETE FROM allocations'); c.execute('DELETE FROM audits'); c.execute('DELETE FROM ai_intake')
    for i,d in enumerate(SEED_DEMANDS,1):
        c.execute('''INSERT INTO demands(id,demand_id,demand_type,customer_name,project_name,plant,product,quantity,required_date,latest_date,margin_per_unit,programme_days,programme_cost_per_day,contractual_priority,urgency_score,confidence,source_type) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(i,)+d)
    c.commit(); c.close(); return {'ok':True}
