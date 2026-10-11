import {describe,it,expect,vi,afterEach} from 'vitest';
import {api,ApiError,cancellable} from './api';
afterEach(()=>vi.unstubAllGlobals());
describe('API boundary',()=>{
 it('sends credentials and CSRF header',async()=>{const fetch=vi.fn().mockResolvedValue(Response.json({id:'1'}));vi.stubGlobal('fetch',fetch);expect(await api('/memory','POST',{content:'x'})).toEqual({id:'1'});expect(fetch.mock.calls[0][1]).toMatchObject({credentials:'same-origin',headers:{'X-Atlas-Client':'web'}})});
 it('surfaces backend errors',async()=>{vi.stubGlobal('fetch',vi.fn().mockResolvedValue(Response.json({detail:'Войдите'},{status:401})));await expect(api('/memory')).rejects.toThrow('Войдите')});
 it('proxy failures have a readable error',async()=>{vi.stubGlobal('fetch',vi.fn().mockResolvedValue(new Response('<html>Bad Gateway</html>',{status:502,headers:{'Content-Type':'text/html'}})));await expect(api('/memory')).rejects.toThrow('API временно недоступен (HTTP 502)')});
 it('preserves HTTP status for connection-state decisions',async()=>{vi.stubGlobal('fetch',vi.fn().mockResolvedValue(Response.json({detail:'Войдите'},{status:401})));await expect(api('/settings')).rejects.toEqual(expect.objectContaining({status:401,name:'ApiError'}));expect(ApiError).toBeDefined()});
 it('completed work cannot be cancelled',()=>{expect(cancellable('COMPLETED')).toBe(false);expect(cancellable('PENDING')).toBe(true);expect(cancellable('WAITING_DEVICE')).toBe(true)});
});
