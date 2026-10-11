"""Единое описание возможностей. Метаданные не выдают разрешение сами по себе."""
from dataclasses import asdict, dataclass
import time


@dataclass(frozen=True)
class Capability:
    id: str
    risk: str
    grants: tuple[str, ...]
    confirmation: str
    cancellable: bool
    reversible: bool = False
    journal: bool = True
    permission_ttl: int = 12

    def public(self):
        return asdict(self)


LOCAL = {
    name: Capability('local.' + name, risk, ('local.' + name,), confirmation, cancel, undo)
    for name, risk, confirmation, cancel, undo in (
        ('volume_delta', 'low', 'never', False, True), ('volume_set', 'low', 'never', False, True),
        ('mute_set', 'low', 'never', False, True), ('media', 'low', 'never', False, False),
        ('time_now', 'low', 'never', False, False), ('date_today', 'low', 'never', False, False),
        ('show_help', 'low', 'never', False, False), ('open_voice_settings', 'low', 'never', False, False),
        ('open_site', 'medium', 'configured', False, False), ('launch_app', 'medium', 'configured', False, False),
    )
}


def server_capability(tool):
    high = tool.execution not in {'read'}
    return Capability(tool.name, 'high' if high else 'low', (tool.name,),
                      'always' if high or tool.requires_approval or not tool.default_allowed else 'never',
                      True, permission_ttl=0)


def local_policy(actions, config):
    capabilities = [LOCAL.get(action.get('type')) for action in actions]
    if not capabilities or any(c is None for c in capabilities):
        raise PermissionError('Неизвестная локальная возможность')
    grants = config.get('local_grants', [c.id for c in LOCAL.values()])
    expiry = config.get('local_grant_expires_at', 0)
    if expiry and time.time() >= expiry:
        raise PermissionError('Срок локального разрешения истёк')
    if any(set(c.grants) - set(grants) for c in capabilities):
        raise PermissionError('Локальная возможность запрещена')
    confirm = any(c.confirmation == 'always' or c.confirmation == 'configured' and
                  config.get('confirm_local_actions', True) for c in capabilities)
    risk = max((c.risk for c in capabilities), key={'low': 0, 'medium': 1, 'high': 2}.get)
    return {'risk': risk, 'needs_confirmation': confirm, 'capabilities': [c.id for c in capabilities]}
