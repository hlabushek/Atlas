"""Live Compose restart drill; creates clearly marked test data, never logs credentials."""
import os
import subprocess
import time
import uuid
from pathlib import Path
import httpx

values=dict(line.split("=",1) for line in Path(".env").read_text(encoding="utf-8-sig").splitlines() if "=" in line and not line.startswith("#"))
password=os.environ.get("ATLAS_E2E_PASSWORD",values["ATLAS_OWNER_PASSWORD"])
base=os.environ.get("ATLAS_E2E_URL","http://127.0.0.1:8080")

def restart(service):
    subprocess.run(["docker","compose","restart",service],check=True,capture_output=True)
    time.sleep(2)

with httpx.Client(base_url=base,timeout=30,headers={"X-Atlas-Client":"web"}) as client:
    response=client.post("/api/login",json={"password":password});response.raise_for_status()
    cid=client.post("/api/conversations",json={"title":"RESTART DRILL"}).json()["id"]
    title="RESTART DRILL "+str(uuid.uuid4())
    task=client.post("/api/tasks",json={"conversation_id":cid,"request_key":str(uuid.uuid4()),"goal":title,
        "plan":[{"tool":"memory.search","arguments":{"query":"Atlas"}},{"tool":"notes.create","arguments":{"title":title,"content":"$previous"}}]}).json()
    def wait(status):
        for _ in range(60):
            result=client.get("/api/tasks/"+task["id"]).json()
            if result["status"]==status:return result
            if result["status"] in {"FAILED","CANCELLED"}:raise AssertionError(result["status"])
            time.sleep(.5)
        raise AssertionError("Timed out waiting for "+status)
    wait("WAITING_APPROVAL")
    restart("worker");restart("api")
    result=wait("WAITING_APPROVAL")
    assert result["current_step"]==1 and result["calls"][0]["attempts"]==1
    approval=next(a for a in client.get("/api/approvals").json() if a["task_id"]==task["id"])
    response=client.post("/api/approvals/"+approval["id"],json={"approve":True});response.raise_for_status()
    result=wait("COMPLETED")
    restart("worker")
    notes=[n for n in client.get("/api/notes").json() if n["title"]==title]
    assert len(notes)==1
    assert len(client.get("/api/conversations/"+cid+"/messages").json())==1
    print("PASS: API/worker restart preserved checkpoints, approval and exactly one note/reply")
