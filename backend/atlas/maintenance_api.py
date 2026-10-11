from fastapi import APIRouter, HTTPException
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field
from .api import DB, Secure
from .demo_reset import public_plan, reset

router=APIRouter(prefix='/api/maintenance')


class ResetInput(BaseModel):
    model_config=ConfigDict(extra='forbid')
    digest:str=Field(pattern=r'^[a-f0-9]{64}$')
    confirmation:Literal['СБРОСИТЬ ДЕМО']


@router.get('/demo')
def preview(session:DB,_:Secure):
    return public_plan(session)


@router.post('/demo/reset')
def perform(body:ResetInput,session:DB,_:Secure):
    try:result=reset(session,body.digest)
    except ValueError as error:raise HTTPException(409,str(error))
    session.commit()
    return result
