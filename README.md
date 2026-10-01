# ⚡ Madar Energy — AI Energy War Room

> 🏆 **2nd Place – Best Project Award** for Excellence in Innovation and Implementation
> *Agentic AI with Vibe Coding* program · Al-Hussein Technical University (HTU) · CEGCO · India Jordan Center of Excellence in IT (2026)

Madar is a multi-agent AI control room that investigates anomalies in Jordan's power grid.
Instead of one chatbot guessing, three specialist agents gather evidence with real analysis tools,
challenge each other in a debate, and an arbitrator decides the most likely cause.

## How it works

```
Incident (manual / PDF report / auto-monitoring)
        │
        ├── ⚡ Meter Agent     – meter faults, theft, abnormal consumption
        ├── 🔌 Charging Agent  – EV charging load shifts, charger failures
        └── 🏭 Plant Agent     – equipment degradation, thermal anomalies
        │
   Debate round – agents challenge each other's evidence, weakest is eliminated
        │
   ⚖️ Arbitrator – code-based confidence scoring → verdict (or "undecided" if evidence is weak)
        │
   Recommended action → impact map → email dossier
```

## Features

- **3 specialist agents + arbitrator** running on Groq (`openai/gpt-oss-120b`)
- **27 analysis tools** built on real open datasets
- **Code-based confidence** – no invented answers; weak evidence returns *undecided*
- **Grid graph & impact map** (networkx) covering all **12 governorates** of Jordan
- **Auto-monitoring mode** – detects an anomaly and opens an investigation by itself, with Arabic voice alerts
- **VOLT-AI assistant** – chat with voice input/output
- **PDF report intake** – upload a report, fields are extracted automatically
- **Email incident dossier** via SMTP
- **Experience replay** – learns from past cases to speed up repeated investigations
- **Synthetic test agent** – stress-tests the system with known scenarios (PASS / FAIL)

## Datasets

| Domain | Dataset |
|---|---|
| Smart meters | London Smart Meters |
| Power plants | NASA C-MAPSS |
| EV charging | Caltech ACN-Data |
| Charging stations | Open Charge Map |

Data is mapped onto Jordan's governorates for demonstration; synthetic incidents are clearly labeled 🧪 in the UI.

## Tech stack

Python · Flask (SSE) · Groq · SQLite · pandas · numpy · networkx · pypdf · three.js

## Getting started

```bash
git clone https://github.com/mayyarr/madar-energy.git
cd madar-energy
python -m venv venv
venv\Scripts\activate          # Windows  (Linux/Mac: source venv/bin/activate)
pip install -r requirements.txt
```

Set your environment variables (never commit keys):

```powershell
$env:GROQ_API_KEY="your_groq_key"
# optional – email dossier
$env:MADAR_SMTP_USER="you@gmail.com"
$env:MADAR_SMTP_PASS="your_app_password"
```

Run:

```bash
python app.py
```

Open http://127.0.0.1:5000

## Project structure

```
agents/      Meter, Charging, Plant agents + Arbitrator + chat agent
tools/       27 analysis tools (meter, charging, plant, geo)
services/    auto-monitoring, grid graph, approvals, PDF intake, experience replay
db/          database seeding and the 12-governorate region mapping
templates/   control-room UI
static/      front-end assets
```

## Team

Built by Mayar Husam Mohammad and team during the *Agentic AI with Vibe Coding* training at HTU.
