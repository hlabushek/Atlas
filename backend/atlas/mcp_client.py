"""Streamable HTTP MCP only. Never launches local stdio processes."""
import asyncio,json
import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from .network import endpoint,headers

async def request(url,credential_env=None,name=None,arguments=None):
    async with asyncio.timeout(25):
        async with httpx.AsyncClient(headers=headers(credential_env),timeout=20,follow_redirects=False) as client:
            async with streamable_http_client(endpoint(url),http_client=client) as (read,write,_):
                async with ClientSession(read,write) as session:
                    await session.initialize()
                    if name is None:
                        result=await session.list_tools()
                        return [t.model_dump(mode='json',by_alias=True) for t in result.tools][:50]
                    result=await session.call_tool(name,arguments or {})
                    data=result.model_dump(mode='json',by_alias=True)
                    if len(json.dumps(data))>50000:raise ValueError('MCP result too large')
                    if data.get('isError'):raise ValueError('MCP tool failed')
                    return data

def discover(url,credential_env=None):return asyncio.run(request(url,credential_env))
def call(url,credential_env,name,arguments):return asyncio.run(request(url,credential_env,name,arguments))
