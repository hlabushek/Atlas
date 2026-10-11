from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,Field,ConfigDict
from sqlalchemy import update
from .api import DB,Secure
from .models import ModelPreferences,Audit
from .model_settings import Preferences,PROVIDERS,credential,view
from .memory import contains_secret
router=APIRouter(prefix='/api')
class Update(BaseModel):
    model_config=ConfigDict(extra='forbid')
    revision:int=Field(ge=1)
    config:Preferences
@router.get('/model-settings')
def get(s:DB,_:Secure):return view(s)
@router.put('/model-settings')
def save(body:Update,s:DB,_:Secure):
    config=body.config.model_dump()
    if contains_secret(str(config)):raise HTTPException(422,'Секреты запрещены в настройках моделей')
    won=s.execute(update(ModelPreferences).where(ModelPreferences.id==1,ModelPreferences.revision==body.revision).values(config=config,revision=body.revision+1))
    if won.rowcount!=1:raise HTTPException(409,'Настройки изменены в другой сессии. Обновите страницу')
    s.add(Audit(event='model_preferences_changed',details={'revision':body.revision+1}));s.commit();return view(s)
@router.get('/model-catalog/{provider}')
def catalog(provider:str,_:Secure):
    import httpx,json
    if provider not in PROVIDERS:raise HTTPException(422,'Неизвестный провайдер')
    secret=credential(provider)
    if provider=='plusvibe' and not secret:raise HTTPException(409,'Сначала настройте ATLAS_PLUSVIBE_API_KEY на сервере')
    headers={'Authorization':'Bearer '+secret} if secret else {}
    try:
        with httpx.Client(timeout=20,follow_redirects=False) as c,c.stream('GET',PROVIDERS[provider][1]+'/models',headers=headers) as r:
            r.raise_for_status();raw=b''
            for chunk in r.iter_bytes():
                raw+=chunk
                if len(raw)>2000000:raise ValueError('Catalog too large')
        rows=json.loads(raw)['data']
        return [{'id':m['id'],'name':str(m.get('name') or m['id'])[:200]} for m in rows[:2000] if isinstance(m,dict) and isinstance(m.get('id'),str) and len(m['id'])<=160 and not contains_secret(m['id']+str(m.get('name','')))]
    except Exception:raise HTTPException(502,'Каталог провайдера недоступен. Можно ввести идентификатор вручную')
