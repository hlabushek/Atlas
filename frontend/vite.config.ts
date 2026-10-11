import {defineConfig} from 'vite';
export default defineConfig({build:{rollupOptions:{input:{app:'index.html',about:'about.html'}}},server:{proxy:{'/api':{target:process.env.ATLAS_E2E_API_URL||'http://127.0.0.1:8000',ws:true}}}});
