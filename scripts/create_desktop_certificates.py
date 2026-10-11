"""Owner-run local development certificate helper."""
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from atlas.desktop_certificates import create
if __name__=='__main__':
    if len(sys.argv)!=2:raise SystemExit('Укажите каталог вне Git для dev сертификатов')
    print('Fingerprint устройства:',create(sys.argv[1])['device'])
