from typing import Literal
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field, field_validator
from .api import DB, Secure, dto, ensure_conversation
from .conversations import change
from .memory import contains_secret

router = APIRouter(prefix='/api/conversations')


@router.get('/{cid}')
def read(cid: str, session: DB, _: Secure):
    return dto(ensure_conversation(session,cid))


class Update(BaseModel):
    model_config = ConfigDict(extra='forbid')
    revision: int = Field(ge=1)
    title: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = Field(default=None, max_length=1000)
    pinned: bool | None = None
    action: Literal['update','archive','trash','restore'] = 'update'

    @field_validator('title','description')
    @classmethod
    def safe_text(cls, value):
        if value is not None:
            value = value.strip()
            if contains_secret(value):
                raise ValueError('Секреты запрещены')
        return value


@router.patch('/{cid}')
def update(cid: str, body: Update, session: DB, _: Secure):
    if body.title == '':
        raise HTTPException(422,'Укажите название')
    values=body.model_dump(exclude={'revision'}, exclude_none=True)
    if body.action!='update' and any(key in body.model_fields_set for key in ['title','description','pinned']):
        raise HTTPException(422,'Операция со статусом не должна менять другие поля')
    try:
        row=change(session,cid,revision=body.revision,values=values)
    except LookupError as error:
        raise HTTPException(404,str(error))
    except ValueError as error:
        raise HTTPException(409,str(error))
    session.commit()
    return dto(row)
