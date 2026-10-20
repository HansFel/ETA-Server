"""Create unique local bootstrap credentials; never print passwords to build logs."""
import json
import secrets
from pathlib import Path

root=Path(__file__).resolve().parents[1]
private=root/'private'
private.mkdir(exist_ok=True)
target=private/'access.json'
if not target.exists():
    target.write_text(json.dumps({'user':'admin','password':secrets.token_urlsafe(18),
                                 'setup_password':secrets.token_urlsafe(12)},indent=2),encoding='utf-8')
data=json.loads(target.read_text(encoding='utf-8'))
(root/'include'/'provisioning.h').write_text(
    '#pragma once\n#define INITIAL_PASSWORD '+json.dumps(data['password'])+
    '\n#define SETUP_PASSWORD '+json.dumps(data['setup_password'])+'\n',encoding='utf-8')
print('Bootstrap credentials prepared in private/access.json (not printed).')
