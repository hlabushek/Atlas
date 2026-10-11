from .router import ModelRouter
"""LangGraph orchestrates bounded steps; SQL rows are durable checkpoints and authority."""
import json
from typing import TypedDict
from langgraph.graph import StateGraph, START, END
from sqlalchemy import select
from .db import sessions
from .models import Task, ToolCall, Approval, ToolGrant, ConnectorSource, Audit, Message
from .tools import get_tool, validate_plan
from .integration_permissions import grant_valid

class State(TypedDict):
    task_id: str
    claim: str
    continue_run: bool

class StaleClaim(Exception):pass

def owned(session,state):
    task=session.scalar(select(Task).where(Task.id==state["task_id"]).with_for_update())
    if task is None or task.claim!=state["claim"] or task.status not in {"PLANNING","RUNNING"}:
        raise StaleClaim()
    return task

def release(task,status):
    task.status=status;task.lease_until=None;task.claim=None

def plan_node(state,router):
    with sessions()() as session:
        task=owned(session,state)
        goal,plan,limit,task_id=task.goal,task.plan,task.max_steps,task.id
        profile=task.model_profile
    if not plan:plan=validate_plan(router.plan(goal,profile=profile,task_id=task_id) if isinstance(router,ModelRouter) else router.plan(goal),limit)
    else:plan=validate_plan(plan,limit)
    with sessions()() as session:
        task=owned(session,state)
        task.plan=plan;task.status="RUNNING"
        session.add(Audit(task_id=task.id,event="plan_saved",details={"steps":len(plan)}));session.commit()
    return {"continue_run":True}

def execute_node(state):
    with sessions()() as session:
        task=owned(session,state)
        if task.current_step>=len(task.plan):return {"continue_run":True}
        if task.current_step>=task.max_steps:raise ValueError("Step budget exhausted")
        step=task.plan[task.current_step]
        tool=get_tool(step["tool"])
        call=session.scalar(select(ToolCall).where(ToolCall.task_id==task.id,ToolCall.step==task.current_step))
        if call is None:
            args=dict(step["arguments"])
            if args.get("content")=="$previous":
                previous=session.scalar(select(ToolCall).where(ToolCall.task_id==task.id,ToolCall.step==task.current_step-1))
                if previous is None or previous.status not in {"SUCCEEDED","DRY_RUN"}:raise ValueError("Missing dependency")
                args["content"]=json.dumps(previous.result,ensure_ascii=False)[:12000]
            args=tool.schema.model_validate(args).model_dump()
            call=ToolCall(task_id=task.id,step=task.current_step,tool=tool.name,arguments=args)
            session.add(call);session.flush()
            session.add(Audit(task_id=task.id,event="tool_prepared",details={"call_id":call.id,"tool":tool.name,"step":call.step}))
        call_id=call.id
        session.commit()  # PREPARED is durable before action.
    try:
        with sessions()() as session:
            if tool.execution=='device':
                from .desktop import lock
                lock(session)
            task=owned(session,state)
            call=session.get(ToolCall,call_id)
            if call.status in {"SUCCEEDED","DRY_RUN"}:
                task.current_step+=1;session.commit();return {"continue_run":True}
            if call.status in {"STARTED","UNKNOWN"} or tool.execution not in {"read","atomic","device"}:
                # No blind replay of ambiguous/non-atomic actions.
                call.status="UNKNOWN";release(task,"PAUSED");task.error="Неизвестный результат действия: требуется проверка, повтор запрещён"
                session.add(Audit(task_id=task.id,event="reconciliation_required",details={"call_id":call.id}));session.commit()
                return {"continue_run":False}
            from .skills import skill_for_tool
            source=skill_for_tool(session,tool.name)
            grant=session.scalar(select(ToolGrant).where(ToolGrant.tool==tool.name).with_for_update())
            approval=session.scalar(select(Approval).where(Approval.call_id==call.id))
            if task.dry_run:
                call.status="DRY_RUN";call.result={"dry_run":True,"tool":tool.name,"arguments":call.arguments,"requires_approval":tool.requires_approval}
            elif not grant_valid(grant,source if isinstance(source,ConnectorSource) else None):
                call.status="BLOCKED";release(task,"FAILED");task.error="Разрешение инструмента отозвано"
                session.add(Audit(task_id=task.id,event="permission_denied",details={"tool":tool.name}));session.commit()
                return {"continue_run":False}
            elif tool.capability.confirmation == 'always' and (approval is None or approval.status!="APPROVED"):
                if approval is None:session.add(Approval(call_id=call.id,task_id=task.id,reason="Разрешить этот вызов: "+tool.description))
                call.status="WAITING_APPROVAL";release(task,"WAITING_APPROVAL")
                session.add(Audit(task_id=task.id,event="approval_requested",details={"call_id":call.id}));session.commit()
                return {"continue_run":False}
            elif tool.execution=='device':
                tool.execute(session,call);call.status='STARTED';call.attempts+=1
                release(task,'WAITING_DEVICE');session.commit();return {'continue_run':False}
            else:
                call.status="STARTED";call.attempts+=1
                # Built-ins act inside this transaction; crash rolls back effect and checkpoint together.
                result=tool.execute(session,call)
                if not tool.verify(session,call,result):raise ValueError("Verification failed")
                call.result=result;call.status="SUCCEEDED"
                if approval:approval.status="CONSUMED"
            task.current_step+=1
            session.add(Audit(task_id=task.id,event="tool_verified" if not task.dry_run else "tool_previewed",details={"call_id":call.id}))
            session.commit()
        return {"continue_run":True}
    except StaleClaim:raise
    except Exception as exc:
        with sessions()() as session:
            task=owned(session,state);call=session.get(ToolCall,call_id)
            call.attempts+=1;call.error="Ошибка инструмента: "+type(exc).__name__
            # Only read/DB-atomic effects can safely retry after rolled-back transaction.
            if call.attempts<3 and tool.execution in {"read","atomic"}:
                call.status="PREPARED";release(task,"PENDING")
            else:call.status="FAILED";release(task,"FAILED");task.error=call.error
            session.add(Audit(task_id=task.id,event="tool_failed",details={"call_id":call.id,"attempt":call.attempts,"type":type(exc).__name__}));session.commit()
        return {"continue_run":False}

def finalize_node(state):
    with sessions()() as session:
        task=owned(session,state)
        if task.current_step<len(task.plan):
            release(task,"PENDING");session.commit();return {}
        calls=session.scalars(select(ToolCall).where(ToolCall.task_id==task.id).order_by(ToolCall.step)).all()
        prefix="Предварительный просмотр: действия не выполнены." if task.dry_run else "Задача выполнена; результаты инструментов проверены."
        result=prefix+"\n"+json.dumps([{"tool":c.tool,"result":c.result} for c in calls],ensure_ascii=False,indent=2)
        task.result=result;release(task,"COMPLETED")
        session.add(Message(conversation_id=task.conversation_id,role="assistant",content=result,task_id=task.id))
        session.add(Audit(task_id=task.id,event="agent_completed"));session.commit()
    return {}

def run(task_id,claim,router):
    builder=StateGraph(State)
    builder.add_node("plan",lambda s:plan_node(s,router))
    builder.add_node("execute",execute_node)
    builder.add_node("finalize",finalize_node)
    builder.add_edge(START,"plan");builder.add_edge("plan","execute")
    builder.add_conditional_edges("execute",lambda s: "finalize" if s["continue_run"] else END)
    builder.add_edge("finalize",END)
    try:builder.compile().invoke({"task_id":task_id,"claim":claim,"continue_run":True},{"recursion_limit":8})
    except StaleClaim:pass  # Paused/cancelled/reclaimed task cannot act or publish.
