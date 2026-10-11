"""Exercise the real SDK against a local HTTP MCP server, no mocked wire protocol."""
import socket,threading,time
import pytest
import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations
from atlas.config import settings
from atlas.mcp_client import discover,call

@pytest.fixture
def mcp_server(client,monkeypatch):
    mcp=FastMCP('Atlas protocol test',stateless_http=True,json_response=True)
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=True,destructiveHint=False))
    def echo(text:str)->str:return 'echo: '+text
    sock=socket.socket();sock.bind(('127.0.0.1',0));port=sock.getsockname()[1]
    server=uvicorn.Server(uvicorn.Config(mcp.streamable_http_app(),log_level='critical',access_log=False))
    thread=threading.Thread(target=lambda:server.run(sockets=[sock]),daemon=True);thread.start()
    for _ in range(100):
        if server.started:break
        time.sleep(.02)
    assert server.started
    monkeypatch.setenv('ATLAS_INTEGRATION_HOSTS','127.0.0.1');settings.cache_clear()
    yield 'http://127.0.0.1:'+str(port)+'/mcp'
    server.should_exit=True;thread.join(timeout=5);sock.close()

def test_real_sdk_discovery_and_call(mcp_server):
    tools=discover(mcp_server)
    assert tools[0]['name']=='echo'
    result=call(mcp_server,None,'echo',{'text':'Atlas'})
    assert 'echo: Atlas' in str(result)

def test_installed_mcp_tool_uses_agent_approval(client,mcp_server):
    from test_agent import create,approve,detail
    from atlas.worker import tick
    manifest={'id':'test-mcp','version':'1.0.0','description':'Локальный тестовый MCP','kind':'mcp','endpoint':mcp_server,'read_tools':['echo']}
    response=client.post('/api/skills',json=manifest)
    assert response.status_code==200,response.text
    client.put('/api/skills/test-mcp',json={'enabled':True})
    client.put('/api/tools/test-mcp.echo/permission',json={'allowed':True})
    task=create(client,[{'tool':'test-mcp.echo','arguments':{'text':'Atlas'}}])
    tick();approve(client);tick()
    assert detail(client,task)['status']=='COMPLETED'
    assert 'echo: Atlas' in str(detail(client,task)['calls'][0]['result'])
