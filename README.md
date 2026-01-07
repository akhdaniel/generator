Run frontend:
```
npm install 
npm run dev
```


Run backend:
```
cd backend
python -m venv .venv && source .venv/bin/activate (or your env)
pip install -r requirements.txt
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```