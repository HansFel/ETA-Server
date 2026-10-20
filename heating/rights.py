"""Rechte pro Person und Bereich.

Administratoren (Gruppe ``admin``) dürfen alles. Alle anderen Personen erhalten pro Bereich
``view`` (sehen) oder ``control`` (sehen und schalten); ohne Eintrag besteht kein Zugriff.
"""
SECTIONS = ['Kessel', 'HK Haus', 'Puffer', 'WW', 'Wiesenegger', 'Werkstatt']
LABELS = {'WW': 'Warmwasser'}
LEVELS = [('', 'Kein Zugriff'), ('view', 'Sehen'), ('control', 'Schalten')]
WIESENEGGER_CONTROL_PROFILES = [
    ('switch_schedule', 'Nur Ein/Aus + Wochenschaltuhr'),
    ('full', 'Alle Wiesenegger-Steuerpunkte'),
]

# Vorlagen sind nur Voreinstellungen beim Anlegen; gespeichert werden immer die einzelnen Rechte.
PRESETS = {
    'wiesenegger': ('Familie Wiesenegger', {'Wiesenegger': 'control'}),
    'reader': ('Mitleser (alles sehen)', {s: 'view' for s in SECTIONS}),
    'operator': ('Bediener (Heizkreise und Warmwasser schalten)', {'Kessel': 'view', 'Puffer': 'view', 'HK Haus': 'control', 'WW': 'control', 'Wiesenegger': 'control', 'Werkstatt': 'control'}),
}

# Punkte, bei denen standardmäßig eine einfache Rückfrage (Ja/Nein, ohne Passwort) erscheint.
# Der Administrator kann das je Punkt ändern.
CRITICAL_KEYS = {'boiler-switch', 'curve-cold', 'curve-warm', 'wiese-curve-cold', 'wiese-curve-warm', 'puffer-min', 'ww-priority'}

def label(section):
    return LABELS.get(section, section)

def is_admin(user):
    return user.is_authenticated and user.is_active and user.groups.filter(name='admin').exists()

def grants_for(user):
    """Dict Bereich -> 'view'/'control' für nicht-administrative Personen."""
    if not user.is_authenticated or not user.is_active:
        return {}
    return dict(user.grants.values_list('section', 'level'))

def details_for(user):
    if not user.is_authenticated or not user.is_active:
        return {}
    return dict(user.control_details.values_list('section', 'profile'))

def can_view(request, section):
    return request.is_admin or section in request.grants

def can_control(request, section):
    return request.is_admin or request.grants.get(section) == 'control'

def visible_sections(request):
    return list(SECTIONS) if request.is_admin else [s for s in SECTIONS if s in request.grants]

def controllable_sections(request):
    return list(SECTIONS) if request.is_admin else [s for s in SECTIONS if request.grants.get(s) == 'control']
