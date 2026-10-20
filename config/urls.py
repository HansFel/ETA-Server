from django.urls import path
from heating import views as v
urlpatterns = [
 path('api/dashboard-preferences/',v.dashboard_preferences,name='dashboard_preferences'),
 path('health/',v.health), path('',v.home,name='home'),path('login/',v.sign_in,name='login'),path('logout/',v.sign_out,name='logout'),path('password/',v.password_change,name='password'),path('forgot/',v.password_reset,name='forgot'),path('access/<str:token>/',v.access,name='access'),
 path('dashboard/',v.dashboard,name='dashboard'),path('admin/',v.admin_home,name='admin_home'),path('api/live/',v.live,name='live'),path('api/history/',v.history,name='history'),path('export/',v.export,name='export'),
 path('control/',v.control,name='control'),path('control/toggle/',v.control_toggle,name='control_toggle'),path('control/schedule/',v.control_schedule,name='control_schedule'),path('control/wiesenegger-mode/',v.control_wiesenegger_mode,name='control_wiesenegger_mode'),path('control/<int:pk>/set/',v.control_set,name='control_set'),path('control/<int:pk>/confirm-setting/',v.control_confirm_setting,name='control_confirm_setting'),
 path('people/',v.people,name='people'),path('mqtt/',v.mqtt_page,name='mqtt'),path('mqtt/punkte.md',v.mqtt_markdown,name='mqtt_markdown'),path('backups/',v.backup_page,name='backups'),path('backups/settings/',v.backup_settings,name='backup_settings'),path('backups/action/',v.backup_action,name='backup_action'),path('backups/<str:ident>/download/',v.backup_download,name='backup_download'),path('backups/<str:ident>/<str:action>/',v.backup_confirm,name='backup_confirm'),
]
